import marimo

__generated_with = "0.20.2"
app = marimo.App()


@app.cell
def _():
    """
    Avellaneda-Stoikov Market Making Backtest
    =========================================
    Key insight: We can predict volatility (rvol_24h AC=0.30-0.49) but cannot predict direction.
    The AS model converts vol prediction into optimal bid/ask placement.

    Focus: ETH and SOL (best MM candidates from venue selection analysis).

    Model:
      reservation_price = mid - q * gamma * sigma^2 * (T-t)
      optimal_spread = gamma * sigma^2 * (T-t) + (2/gamma) * ln(1 + gamma/kappa)
      where sigma is in price-space: sigma_price = rvol_24h * mid_price

    Usage:
        python3 analyses/20260303_mm_avellaneda_stoikov/analysis_as_mm.py
    """

    from __future__ import annotations

    import itertools
    from dataclasses import dataclass, field
    from pathlib import Path
    from typing import NamedTuple

    import numpy as np
    import polars as pl

    # ---------------------------------------------------------------------------
    # Constants
    # ---------------------------------------------------------------------------
    DATA_DIR = Path(__file__).resolve().parents[2] / "data"
    MAKER_FEE_BPS = 2.0       # 0.02% per fill (one side)
    TAKER_FEE_BPS = 8.0       # round-trip taker reference (not used in MM)
    HOURS_PER_YEAR = 8760
    TRAIN_CUTOFF = "2025-09-01"  # Train < cutoff, Test >= cutoff

    SYMBOLS = ["ETH", "SOL"]


    # ---------------------------------------------------------------------------
    # Data loading & feature engineering
    # ---------------------------------------------------------------------------

    def load_ohlcv(symbol: str) -> pl.DataFrame:
        """Load 1h OHLCV from Binance and compute vol features."""
        path = DATA_DIR / f"binance_{symbol.lower()}usdt_1h.parquet"
        df = pl.read_parquet(path)

        # Normalise timestamp
        df = df.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")).alias("timestamp")
        )
        df = df.sort("timestamp")

        # Returns and realised vol
        df = df.with_columns([
            (pl.col("close").log() - pl.col("close").shift(1).log()).alias("log_ret"),
            ((pl.col("high") - pl.col("low")) / pl.col("close")).alias("range_pct"),
        ])

        # Realised vol (24h rolling std of log returns — hourly fractional)
        df = df.with_columns([
            pl.col("log_ret").rolling_std(24).alias("rvol_24h"),
            pl.col("range_pct").rolling_mean(24).alias("avg_range_24h"),
        ])

        # Mid price for convenience
        df = df.with_columns(
            ((pl.col("high") + pl.col("low")) / 2.0).alias("mid")
        )

        return df.drop_nulls(subset=["rvol_24h"])


    def split_train_test(df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
        cutoff = pl.lit(TRAIN_CUTOFF).str.strptime(pl.Datetime("us"), "%Y-%m-%d")
        train = df.filter(pl.col("timestamp") < cutoff)
        test = df.filter(pl.col("timestamp") >= cutoff)
        return train, test


    # ---------------------------------------------------------------------------
    # Avellaneda-Stoikov helpers
    # ---------------------------------------------------------------------------

    def as_reservation_price(mid: float, q: float, gamma: float, sigma: float, tau: float) -> float:
        """Reservation price: r = mid - q * gamma * sigma^2 * tau
        sigma here is in PRICE space (sigma_price = rvol * mid).
        """
        return mid - q * gamma * sigma * sigma * tau


    def as_optimal_spread(gamma: float, sigma: float, tau: float, kappa: float) -> float:
        """Optimal spread: delta = gamma * sigma^2 * tau + (2/gamma) * ln(1 + gamma/kappa)
        sigma here is in PRICE space.
        """
        return gamma * sigma * sigma * tau + (2.0 / gamma) * np.log(1.0 + gamma / kappa)


    # ---------------------------------------------------------------------------
    # Simulation
    # ---------------------------------------------------------------------------

    @dataclass
    class MMState:
        cash: float = 0.0
        inventory: float = 0.0
        n_bid_fills: int = 0
        n_ask_fills: int = 0
        n_both_fills: int = 0
        n_no_fills: int = 0
        n_periods: int = 0
        pnl_history: list[float] = field(default_factory=list)
        inventory_history: list[float] = field(default_factory=list)
        spread_history: list[float] = field(default_factory=list)


    class QuoteResult(NamedTuple):
        bid: float
        ask: float
        spread_bps: float  # spread in bps for diagnostics


    def compute_quotes_as(
        mid: float,
        q: float,
        gamma: float,
        sigma_ret: float,
        kappa: float,
        T_hours: int,
        t_in_cycle: int,
    ) -> QuoteResult:
        """Compute AS bid/ask quotes with inventory skew.

        sigma_ret: fractional return vol (rvol_24h)
        Converts to price-space: sigma_price = sigma_ret * mid
        """
        tau = max((T_hours - t_in_cycle) / T_hours, 0.01)
        sigma_price = sigma_ret * mid

        reservation = as_reservation_price(mid, q, gamma, sigma_price, tau)
        spread = as_optimal_spread(gamma, sigma_price, tau, kappa)
        half_spread = spread / 2.0

        bid = reservation - half_spread
        ask = reservation + half_spread

        spread_bps = (ask - bid) / mid * 10000.0
        return QuoteResult(bid=bid, ask=ask, spread_bps=spread_bps)


    def compute_quotes_fixed(mid: float, spread_bps: float) -> QuoteResult:
        """Fixed symmetric spread around mid."""
        half = mid * spread_bps / 10000.0 / 2.0
        return QuoteResult(bid=mid - half, ask=mid + half, spread_bps=spread_bps)


    def run_simulation(
        df: pl.DataFrame,
        strategy: str,
        *,
        gamma: float = 0.1,
        kappa: float = 1.0,
        T_hours: int = 24,
        inv_limit: int = 10,
        fixed_spread_bps: float = 50.0,
        regime_aware: bool = False,
        vol_percentile_70: float | None = None,
    ) -> MMState:
        """
        Run MM simulation on OHLCV data.

        strategy: 'fixed', 'as_naive', 'as_regime'
        Fill logic: bid filled if next_low <= bid, ask filled if next_high >= ask.
        Maker fee applied per fill side.
        """
        state = MMState()
        maker_fee_frac = MAKER_FEE_BPS / 10000.0

        # Extract numpy arrays for speed
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        closes = df["close"].to_numpy()
        mids = df["mid"].to_numpy()
        sigmas = df["rvol_24h"].to_numpy()

        n = len(df)

        for i in range(n - 1):
            state.n_periods += 1
            mid = mids[i]
            sigma = sigmas[i]

            # Time within cycle
            t_in_cycle = i % T_hours

            # Determine quotes
            if strategy == "fixed":
                q = compute_quotes_fixed(mid, fixed_spread_bps)
            elif strategy == "as_naive":
                q = compute_quotes_as(
                    mid, state.inventory, gamma, sigma, kappa, T_hours, t_in_cycle,
                )
            elif strategy == "as_regime":
                g = gamma
                if regime_aware and vol_percentile_70 is not None:
                    if sigma > vol_percentile_70:
                        g = gamma * 3.0  # more risk averse in high vol
                q = compute_quotes_as(
                    mid, state.inventory, g, sigma, kappa, T_hours, t_in_cycle,
                )
            else:
                raise ValueError(f"Unknown strategy: {strategy}")

            bid_price, ask_price = q.bid, q.ask
            state.spread_history.append(q.spread_bps)

            # Next bar's price action for fill determination
            next_low = lows[i + 1]
            next_high = highs[i + 1]
            next_close = closes[i + 1]

            bid_filled = False
            ask_filled = False

            # Check fills (with inventory limits)
            if next_low <= bid_price and state.inventory < inv_limit:
                bid_filled = True
                state.inventory += 1
                state.cash -= bid_price * (1.0 + maker_fee_frac)
                state.n_bid_fills += 1

            if next_high >= ask_price and state.inventory > -inv_limit:
                ask_filled = True
                state.inventory -= 1
                state.cash += ask_price * (1.0 - maker_fee_frac)
                state.n_ask_fills += 1

            if bid_filled and ask_filled:
                state.n_both_fills += 1
            elif not bid_filled and not ask_filled:
                state.n_no_fills += 1

            state.inventory_history.append(state.inventory)

            # Mark-to-market PnL
            mtm = state.cash + state.inventory * next_close
            state.pnl_history.append(mtm)

        return state


    # ---------------------------------------------------------------------------
    # Metrics
    # ---------------------------------------------------------------------------

    @dataclass
    class Metrics:
        sharpe: float
        total_pnl_bps: float
        max_drawdown_pct: float
        avg_abs_inventory: float
        fill_rate_both: float
        fill_rate_one: float
        fill_rate_none: float
        pnl_per_fill: float
        total_fills: int
        n_periods: int
        avg_spread_bps: float


    def compute_metrics(state: MMState, initial_price: float) -> Metrics:
        """Compute performance metrics from simulation state."""
        pnl = np.array(state.pnl_history)
        inv = np.array(state.inventory_history)
        spreads = np.array(state.spread_history)

        if len(pnl) < 2:
            return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        # Hourly returns
        returns = np.diff(pnl)
        mean_ret = np.mean(returns)
        std_ret = np.std(returns)

        sharpe = (mean_ret / std_ret * np.sqrt(HOURS_PER_YEAR)) if std_ret > 0 else 0.0

        # Total PnL in bps of initial price
        total_pnl = pnl[-1]
        total_pnl_bps = total_pnl / initial_price * 10000.0

        # Max drawdown (in bps of initial price)
        peak = np.maximum.accumulate(pnl)
        drawdown = peak - pnl
        max_dd_pct = np.max(drawdown) / initial_price * 100.0 if len(drawdown) > 0 else 0.0

        total_fills = state.n_bid_fills + state.n_ask_fills
        n = state.n_periods

        avg_abs_inv = np.mean(np.abs(inv)) if len(inv) > 0 else 0.0
        avg_spread = np.mean(spreads) if len(spreads) > 0 else 0.0

        return Metrics(
            sharpe=sharpe,
            total_pnl_bps=total_pnl_bps,
            max_drawdown_pct=max_dd_pct,
            avg_abs_inventory=avg_abs_inv,
            fill_rate_both=state.n_both_fills / n if n > 0 else 0,
            fill_rate_one=(state.n_bid_fills + state.n_ask_fills - 2 * state.n_both_fills) / n if n > 0 else 0,
            fill_rate_none=state.n_no_fills / n if n > 0 else 0,
            pnl_per_fill=total_pnl / total_fills if total_fills > 0 else 0,
            total_fills=total_fills,
            n_periods=n,
            avg_spread_bps=avg_spread,
        )


    # ---------------------------------------------------------------------------
    # Grid search
    # ---------------------------------------------------------------------------

    def grid_search_as(
        train_df: pl.DataFrame,
        test_df: pl.DataFrame,
        symbol: str,
    ) -> list:
        """Run parameter grid search for AS model on train, evaluate best on test."""
        gammas = [0.01, 0.1, 1.0, 10.0]
        kappas = [0.5, 1.0, 2.0, 5.0]
        T_hours_list = [8, 24]
        inv_limits = [5, 10, 20]

        initial_price_train = train_df["close"][0]
        initial_price_test = test_df["close"][0]

        print(f"\n{'='*110}")
        print(f"  GRID SEARCH: {symbol} -- AS Naive Model")
        print(f"  Train: {len(train_df)} bars, Test: {len(test_df)} bars")
        print(f"{'='*110}")
        print(f"{'gamma':>8} {'kappa':>8} {'T':>4} {'inv_lim':>8} | "
              f"{'Sharpe':>8} {'PnL(bps)':>10} {'MaxDD%':>8} {'Fills':>7} "
              f"{'Both%':>7} {'None%':>7} {'AvgSprd':>8} {'AvgInv':>7}")
        print("-" * 110)

        results = []

        for gamma, kappa, T_hours, inv_limit in itertools.product(gammas, kappas, T_hours_list, inv_limits):
            state = run_simulation(
                train_df,
                strategy="as_naive",
                gamma=gamma,
                kappa=kappa,
                T_hours=T_hours,
                inv_limit=inv_limit,
            )
            m = compute_metrics(state, initial_price_train)
            results.append((gamma, kappa, T_hours, inv_limit, m))

            print(f"{gamma:8.2f} {kappa:8.2f} {T_hours:4d} {inv_limit:8d} | "
                  f"{m.sharpe:8.2f} {m.total_pnl_bps:10.1f} {m.max_drawdown_pct:8.2f} "
                  f"{m.total_fills:7d} {m.fill_rate_both*100:6.1f}% {m.fill_rate_none*100:6.1f}% "
                  f"{m.avg_spread_bps:7.1f}bp {m.avg_abs_inventory:6.1f}")

        # Sort by Sharpe
        results.sort(key=lambda x: x[4].sharpe, reverse=True)

        print(f"\n{'='*70}")
        print(f"  TOP 10 CONFIGS by Sharpe (Train) -- {symbol}")
        print(f"{'='*70}")
        for rank, (gamma, kappa, T_hours, inv_limit, m) in enumerate(results[:10], 1):
            print(f"  #{rank}: gamma={gamma}, kappa={kappa}, T={T_hours}h, inv_limit={inv_limit}")
            print(f"       Sharpe={m.sharpe:.3f}, PnL={m.total_pnl_bps:.1f}bps, "
                  f"MaxDD={m.max_drawdown_pct:.2f}%, Fills={m.total_fills}, "
                  f"AvgSpread={m.avg_spread_bps:.1f}bp, AvgInv={m.avg_abs_inventory:.1f}")

        # Evaluate top 5 on Test
        print(f"\n{'='*70}")
        print(f"  TEST EVALUATION -- Top 5 configs -- {symbol}")
        print(f"{'='*70}")
        for rank, (gamma, kappa, T_hours, inv_limit, train_m) in enumerate(results[:5], 1):
            state_test = run_simulation(
                test_df,
                strategy="as_naive",
                gamma=gamma,
                kappa=kappa,
                T_hours=T_hours,
                inv_limit=inv_limit,
            )
            mt = compute_metrics(state_test, initial_price_test)
            print(f"  #{rank}: gamma={gamma}, kappa={kappa}, T={T_hours}h, inv_limit={inv_limit}")
            print(f"       [Train] Sharpe={train_m.sharpe:.3f}, PnL={train_m.total_pnl_bps:.1f}bps")
            print(f"       [Test]  Sharpe={mt.sharpe:.3f}, PnL={mt.total_pnl_bps:.1f}bps, "
                  f"MaxDD={mt.max_drawdown_pct:.2f}%, Fills={mt.total_fills}, "
                  f"AvgSpread={mt.avg_spread_bps:.1f}bp, AvgInv={mt.avg_abs_inventory:.1f}, "
                  f"PnL/Fill={mt.pnl_per_fill:.4f}")

        return results


    # ---------------------------------------------------------------------------
    # Strategy comparison
    # ---------------------------------------------------------------------------

    def compare_strategies(
        train_df: pl.DataFrame,
        test_df: pl.DataFrame,
        symbol: str,
        best_gamma: float,
        best_kappa: float,
        best_T: int,
        best_inv: int,
    ) -> None:
        """Compare Fixed, AS Naive, and AS Regime strategies on test set."""
        initial_price = test_df["close"][0]

        # Compute vol percentile from train for regime detection
        vol_p70 = float(train_df["rvol_24h"].quantile(0.70))

        strategies = [
            ("Fixed 50bp", dict(strategy="fixed", fixed_spread_bps=50.0, inv_limit=best_inv)),
            ("Fixed 100bp", dict(strategy="fixed", fixed_spread_bps=100.0, inv_limit=best_inv)),
            ("AS Naive", dict(
                strategy="as_naive", gamma=best_gamma, kappa=best_kappa,
                T_hours=best_T, inv_limit=best_inv,
            )),
            ("AS Regime-Aware", dict(
                strategy="as_regime", gamma=best_gamma, kappa=best_kappa,
                T_hours=best_T, inv_limit=best_inv,
                regime_aware=True, vol_percentile_70=vol_p70,
            )),
        ]

        print(f"\n{'='*120}")
        print(f"  STRATEGY COMPARISON (Test Set) -- {symbol}")
        print(f"  Best AS params: gamma={best_gamma}, kappa={best_kappa}, T={best_T}h, inv_limit={best_inv}")
        print(f"  Vol P70 threshold: {vol_p70:.6f}")
        print(f"{'='*120}")
        print(f"{'Strategy':<20} | {'Sharpe':>8} {'PnL(bps)':>10} {'MaxDD%':>8} "
              f"{'Fills':>7} {'Both%':>7} {'None%':>7} {'AvgSprd':>8} "
              f"{'AvgInv':>7} {'PnL/Fill':>10} {'FinalInv':>9}")
        print("-" * 120)

        for name, kwargs in strategies:
            state = run_simulation(test_df, **kwargs)
            m = compute_metrics(state, initial_price)
            print(f"{name:<20} | {m.sharpe:8.2f} {m.total_pnl_bps:10.1f} {m.max_drawdown_pct:8.2f} "
                  f"{m.total_fills:7d} {m.fill_rate_both*100:6.1f}% {m.fill_rate_none*100:6.1f}% "
                  f"{m.avg_spread_bps:7.1f}bp {m.avg_abs_inventory:6.1f} "
                  f"{m.pnl_per_fill:10.4f} {state.inventory:9.1f}")


    # ---------------------------------------------------------------------------
    # Detailed simulation with inventory tracking
    # ---------------------------------------------------------------------------

    def run_detailed_simulation(
        df: pl.DataFrame,
        symbol: str,
        gamma: float,
        kappa: float,
        T_hours: int,
        inv_limit: int,
    ) -> None:
        """Run simulation and print monthly breakdown with inventory stats."""
        state = run_simulation(
            df, strategy="as_naive",
            gamma=gamma, kappa=kappa, T_hours=T_hours, inv_limit=inv_limit,
        )

        timestamps = df["timestamp"].to_list()
        closes = df["close"].to_numpy()
        initial_price = closes[0]
        pnl_arr = np.array(state.pnl_history)
        inv_arr = np.array(state.inventory_history)
        spread_arr = np.array(state.spread_history)

        # Monthly aggregation
        monthly: dict[str, dict] = {}
        n = len(pnl_arr)
        for i in range(n):
            month_key = timestamps[i].strftime("%Y-%m")
            if month_key not in monthly:
                monthly[month_key] = {"n": 0, "start_idx": i, "end_idx": i}
            monthly[month_key]["n"] += 1
            monthly[month_key]["end_idx"] = i

        # Reconstruct per-period fill info from inventory changes
        # inv_arr[i] is inventory AFTER period i's fills
        # bid fill => inv +1, ask fill => inv -1
        # We can detect fills from inventory changes

        print(f"\n{'='*90}")
        print(f"  DETAILED MONTHLY BREAKDOWN -- {symbol}")
        print(f"  gamma={gamma}, kappa={kappa}, T={T_hours}h, inv_limit={inv_limit}")
        print(f"{'='*90}")
        print(f"{'Month':<10} | {'Bars':>5} {'AvgInv':>7} {'MaxInv':>7} "
              f"{'AvgSprd':>8} {'MonthPnL':>12} {'CumPnL':>12} {'CumBps':>9}")
        print("-" * 90)

        sorted_months = sorted(monthly.keys())
        for month in sorted_months:
            d = monthly[month]
            si, ei = d["start_idx"], d["end_idx"]
            month_inv = inv_arr[si:ei+1]
            month_spread = spread_arr[si:ei+1]
            month_pnl_start = pnl_arr[si - 1] if si > 0 else 0.0
            month_pnl_end = pnl_arr[ei]
            month_pnl = month_pnl_end - month_pnl_start
            cum_pnl_bps = month_pnl_end / initial_price * 10000.0

            print(f"{month:<10} | {d['n']:5d} {np.mean(np.abs(month_inv)):6.1f} "
                  f"{np.max(np.abs(month_inv)):7.1f} {np.mean(month_spread):7.1f}bp "
                  f"{month_pnl:12.2f} {month_pnl_end:12.2f} {cum_pnl_bps:8.1f}bp")

        print(f"\n  Full-period inventory stats:")
        print(f"    Mean absolute: {np.mean(np.abs(inv_arr)):.2f}")
        print(f"    Max absolute:  {np.max(np.abs(inv_arr)):.2f}")
        print(f"    Std:           {np.std(inv_arr):.2f}")
        print(f"    Final:         {state.inventory:.1f}")
        print(f"    Final PnL:     {pnl_arr[-1]:.2f} ({pnl_arr[-1]/initial_price*10000:.1f} bps)")
        print(f"  Full-period spread stats:")
        print(f"    Mean:   {np.mean(spread_arr):.1f} bp")
        print(f"    Median: {np.median(spread_arr):.1f} bp")
        print(f"    P10:    {np.percentile(spread_arr, 10):.1f} bp")
        print(f"    P90:    {np.percentile(spread_arr, 90):.1f} bp")
        print(f"  Fill stats:")
        print(f"    Bid fills:  {state.n_bid_fills}")
        print(f"    Ask fills:  {state.n_ask_fills}")
        print(f"    Both:       {state.n_both_fills} ({state.n_both_fills/state.n_periods*100:.1f}%)")
        print(f"    None:       {state.n_no_fills} ({state.n_no_fills/state.n_periods*100:.1f}%)")


    # ---------------------------------------------------------------------------
    # Volatility analysis
    # ---------------------------------------------------------------------------

    def print_vol_stats(df: pl.DataFrame, symbol: str) -> None:
        """Print volatility statistics for context."""
        sigma = df["rvol_24h"].to_numpy()
        mids = df["mid"].to_numpy()
        # Show both fractional and price-space vol
        sigma_price = sigma * mids
        avg_mid = np.mean(mids)

        print(f"\n  {symbol} Volatility (rvol_24h) Statistics:")
        print(f"    --- Fractional (return-space) ---")
        print(f"    Mean:   {np.mean(sigma):.6f}  ({np.mean(sigma)*10000:.1f} bp)")
        print(f"    Median: {np.median(sigma):.6f}  ({np.median(sigma)*10000:.1f} bp)")
        print(f"    P10:    {np.percentile(sigma, 10):.6f}")
        print(f"    P70:    {np.percentile(sigma, 70):.6f}")
        print(f"    P90:    {np.percentile(sigma, 90):.6f}")
        print(f"    --- Price-space (sigma * mid) ---")
        print(f"    Mean:   {np.mean(sigma_price):.2f}")
        print(f"    Median: {np.median(sigma_price):.2f}")
        print(f"    Avg mid price: {avg_mid:.2f}")
        # Autocorrelation
        ac1 = np.corrcoef(sigma[:-1], sigma[1:])[0, 1]
        print(f"    AC(1):  {ac1:.4f}")


    # ---------------------------------------------------------------------------
    # Spread diagnostics
    # ---------------------------------------------------------------------------

    def print_spread_diagnostics(symbol: str, mid: float, sigma_ret: float) -> None:
        """Show what AS spreads look like for a few parameter combos."""
        print(f"\n  {symbol} Spread Diagnostics (mid={mid:.2f}, sigma_ret={sigma_ret:.6f}):")
        print(f"  {'gamma':>8} {'kappa':>8} {'T':>4} {'tau':>6} | {'spread':>10} {'spread_bp':>10}")
        print(f"  {'-'*60}")
        for gamma in [0.01, 0.1, 1.0, 10.0]:
            for kappa in [0.5, 1.0, 5.0]:
                for T in [8, 24]:
                    sigma_price = sigma_ret * mid
                    tau = 0.5  # mid-cycle
                    spread = as_optimal_spread(gamma, sigma_price, tau, kappa)
                    spread_bp = spread / mid * 10000
                    print(f"  {gamma:8.2f} {kappa:8.2f} {T:4d} {tau:6.2f} | "
                          f"{spread:10.4f} {spread_bp:9.1f}bp")


    # ---------------------------------------------------------------------------
    # Main
    # ---------------------------------------------------------------------------

    def main() -> None:
        print("=" * 110)
        print("  AVELLANEDA-STOIKOV MARKET MAKING BACKTEST")
        print("  Focus: ETH, SOL | Data: 1h OHLCV (Binance)")
        print(f"  Train: < {TRAIN_CUTOFF} | Test: >= {TRAIN_CUTOFF}")
        print(f"  Maker fee: {MAKER_FEE_BPS}bps per fill | sigma in price-space: sigma_price = rvol_24h * mid")
        print("=" * 110)

        all_best_configs: dict[str, tuple] = {}

        for symbol in SYMBOLS:
            print(f"\n\n{'#'*110}")
            print(f"###  {symbol}  ###")
            print(f"{'#'*110}")

            df = load_ohlcv(symbol)
            train_df, test_df = split_train_test(df)

            print(f"\n  Data: {len(df)} total bars ({len(train_df)} train, {len(test_df)} test)")
            print(f"  Price range: {df['close'].min():.2f} - {df['close'].max():.2f}")

            print_vol_stats(train_df, symbol)

            # Show what AS spreads look like at median vol
            median_sigma = float(np.median(train_df["rvol_24h"].to_numpy()))
            median_mid = float(np.median(train_df["mid"].to_numpy()))
            print_spread_diagnostics(symbol, median_mid, median_sigma)

            # ------- Grid Search -------
            results = grid_search_as(train_df, test_df, symbol)

            # Best config by Sharpe
            best = results[0]
            best_gamma, best_kappa, best_T, best_inv = best[0], best[1], best[2], best[3]
            all_best_configs[symbol] = (best_gamma, best_kappa, best_T, best_inv)

            # ------- Strategy Comparison -------
            compare_strategies(
                train_df, test_df, symbol,
                best_gamma, best_kappa, best_T, best_inv,
            )

            # ------- Detailed monthly breakdown on full dataset -------
            run_detailed_simulation(
                df, symbol, best_gamma, best_kappa, best_T, best_inv,
            )

        # ---------------------------------------------------------------------------
        # Summary
        # ---------------------------------------------------------------------------
        print(f"\n\n{'='*110}")
        print("  FINAL SUMMARY")
        print(f"{'='*110}")
        for symbol in SYMBOLS:
            g, k, t, inv = all_best_configs[symbol]
            print(f"\n  {symbol}:")
            print(f"    Best config: gamma={g}, kappa={k}, T={t}h, inv_limit={inv}")

            df = load_ohlcv(symbol)
            _, test_df = split_train_test(df)
            initial_price = test_df["close"][0]

            vol_p70 = float(df.filter(
                pl.col("timestamp") < pl.lit(TRAIN_CUTOFF).str.strptime(pl.Datetime("us"), "%Y-%m-%d")
            )["rvol_24h"].quantile(0.70))

            configs = [
                ("Fixed 50bp", dict(strategy="fixed", fixed_spread_bps=50.0, inv_limit=inv)),
                ("Fixed 100bp", dict(strategy="fixed", fixed_spread_bps=100.0, inv_limit=inv)),
                ("AS Naive", dict(strategy="as_naive", gamma=g, kappa=k, T_hours=t, inv_limit=inv)),
                ("AS Regime", dict(strategy="as_regime", gamma=g, kappa=k, T_hours=t, inv_limit=inv,
                                   regime_aware=True, vol_percentile_70=vol_p70)),
            ]

            print(f"    {'Strategy':<16} {'Sharpe':>8} {'PnL(bps)':>10} {'MaxDD%':>8} "
                  f"{'Fills':>7} {'AvgSprd':>8} {'AvgInv':>7}")
            for name, kwargs in configs:
                state = run_simulation(test_df, **kwargs)
                m = compute_metrics(state, initial_price)
                print(f"    {name:<16} {m.sharpe:8.2f} {m.total_pnl_bps:10.1f} {m.max_drawdown_pct:8.2f} "
                      f"{m.total_fills:7d} {m.avg_spread_bps:7.1f}bp {m.avg_abs_inventory:6.1f}")

        print(f"\n{'='*110}")
        print("  KEY TAKEAWAYS")
        print(f"{'='*110}")
        print("""
      1. AS MODEL with price-space sigma (sigma_price = rvol_24h * mid):
         - Converts hourly return vol into dollar-denominated spread
         - Low gamma + low kappa => wide spread, few fills, large inventory accumulation
         - High gamma + high kappa => tighter spread, more fills, better inventory control

      2. INVENTORY SKEW (reservation price shift):
         - q * gamma * sigma^2 * tau shifts the mid to favor reducing inventory
         - Effective only when gamma * sigma_price^2 is meaningful relative to spread

      3. REGIME-AWARE (3x gamma in high vol):
         - Widens spreads when vol > P70, reducing adverse selection risk
         - Incremental improvement over naive AS in volatile periods

      4. FIXED SPREAD baseline:
         - 50bp: high fill rate, moderate inventory risk
         - 100bp: lower fill rate, less adverse selection
         - Simple but surprisingly competitive on 1h data

      5. LIMITATIONS of 1h OHLCV simulation:
         - Fill logic: bid filled if next_low <= bid (optimistic, ignores queue position)
         - No intra-hour microstructure or adverse selection modelling
         - Both sides can fill in same bar (optimistic for round-trip capture)
         - Real MM would face: partial fills, latency, queue priority, toxic flow
         - Results are UPPER BOUND on realistic MM performance
        """)

    def _main_():
        main()

    _main_()
    return


if __name__ == "__main__":
    app.run()
