"""
Vol-of-Vol Conditioned Market Making Simulation
================================================
Tests whether VoV-based regime filtering improves MM performance.
Key hypothesis: High Vol + Low VoV is ideal for MM (stable wide spreads).
"""

import numpy as np
import polars as pl
from dataclasses import dataclass
from typing import Optional


# ── Parameters ──────────────────────────────────────────────────────
@dataclass
class ASParams:
    gamma: float = 0.1       # risk aversion
    kappa: float = 2.0       # order arrival intensity
    T: float = 24.0          # horizon in hours
    inv_limit: int = 10      # max inventory


FEE_SCENARIOS = {
    "0bp": 0.0,
    "-0.25bp (Drift)": -0.25e-4,
    "2bp (CEX)": 2.0e-4,
}

REGIME_NAMES = ["LV-LVV", "LV-HVV", "HV-LVV", "HV-HVV"]


# ── Feature computation ────────────────────────────────────────────
def compute_features(df: pl.DataFrame) -> pl.DataFrame:
    """Compute rvol, vov, and regime labels with expanding-window medians (no lookahead)."""
    df = df.sort("timestamp")

    # log returns
    close = df["close"].to_numpy()
    log_ret = np.log(close[1:] / close[:-1])
    log_ret = np.concatenate([[np.nan], log_ret])

    # rolling 24h rvol (std of log returns)
    rvol_24h = np.full(len(log_ret), np.nan)
    for i in range(24, len(log_ret)):
        rvol_24h[i] = np.nanstd(log_ret[i - 23 : i + 1], ddof=1)

    # rolling 168h vov (std of rvol)
    vov_168h = np.full(len(log_ret), np.nan)
    for i in range(168 + 24, len(log_ret)):
        window = rvol_24h[i - 167 : i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) >= 24:
            vov_168h[i] = np.std(valid, ddof=1)

    # expanding-window medians (no lookahead)
    vol_median = np.full(len(log_ret), np.nan)
    vov_median = np.full(len(log_ret), np.nan)
    for i in range(168 + 24, len(log_ret)):
        valid_vol = rvol_24h[24 : i + 1]
        valid_vol = valid_vol[~np.isnan(valid_vol)]
        if len(valid_vol) > 0:
            vol_median[i] = np.median(valid_vol)

        valid_vov = vov_168h[168 + 24 : i + 1]
        valid_vov = valid_vov[~np.isnan(valid_vov)]
        if len(valid_vov) > 0:
            vov_median[i] = np.median(valid_vov)

    # regime labels: 0=LV-LVV, 1=LV-HVV, 2=HV-LVV, 3=HV-HVV
    regime = np.full(len(log_ret), -1, dtype=int)
    valid_mask = ~np.isnan(vol_median) & ~np.isnan(vov_median) & ~np.isnan(rvol_24h) & ~np.isnan(vov_168h)
    high_vol = rvol_24h >= vol_median
    high_vov = vov_168h >= vov_median
    regime[valid_mask & ~high_vol & ~high_vov] = 0  # LV-LVV
    regime[valid_mask & ~high_vol & high_vov] = 1   # LV-HVV
    regime[valid_mask & high_vol & ~high_vov] = 2   # HV-LVV
    regime[valid_mask & high_vol & high_vov] = 3    # HV-HVV

    return df.with_columns([
        pl.Series("log_ret", log_ret),
        pl.Series("rvol_24h", rvol_24h),
        pl.Series("vov_168h", vov_168h),
        pl.Series("vol_median", vol_median),
        pl.Series("vov_median", vov_median),
        pl.Series("regime", regime),
    ])


# ── Avellaneda-Stoikov quote computation ────────────────────────────
def compute_as_quotes(mid: float, sigma: float, q: int, t_remain: float, params: ASParams) -> tuple[float, float]:
    """Compute AS model bid/ask prices."""
    gamma, kappa, T = params.gamma, params.kappa, params.T
    t = max(t_remain / T, 1e-6)

    # reservation price
    r = mid - q * gamma * sigma**2 * t

    # optimal spread
    spread = gamma * sigma**2 * t + (2.0 / gamma) * np.log(1 + gamma / kappa)

    bid = r - spread / 2.0
    ask = r + spread / 2.0
    return bid, ask


# ── Strategy definitions ────────────────────────────────────────────
def strategy_always_on(regime: int, params: ASParams) -> tuple[bool, float, float]:
    """Always active, no adjustment."""
    return True, 1.0, params.gamma


def strategy_vov_filter(regime: int, params: ASParams) -> tuple[bool, float, float]:
    """Only active in HV-LVV (regime 2)."""
    if regime == 2:
        return True, 1.0, params.gamma
    return False, 0.0, params.gamma


def strategy_vov_adaptive(regime: int, params: ASParams) -> tuple[bool, float, float]:
    """Always active but 3x gamma in HV-HVV (regime 3)."""
    if regime == 3:
        return True, 1.0, params.gamma * 3.0
    return True, 1.0, params.gamma


def strategy_vov_smart(regime: int, params: ASParams) -> tuple[bool, float, float]:
    """Full in HV-LVV, half in LV-LVV and LV-HVV, skip HV-HVV."""
    if regime == 2:  # HV-LVV: full
        return True, 1.0, params.gamma
    elif regime in (0, 1):  # LV-LVV, LV-HVV: half size
        return True, 0.5, params.gamma
    else:  # HV-HVV: skip
        return False, 0.0, params.gamma


STRATEGIES = {
    "Always-On": strategy_always_on,
    "VoV-Filter": strategy_vov_filter,
    "VoV-Adaptive": strategy_vov_adaptive,
    "VoV-Smart": strategy_vov_smart,
}


# ── Simulation engine ──────────────────────────────────────────────
@dataclass
class SimResult:
    name: str
    fee_label: str
    symbol: str
    total_pnl_bps: float
    annual_sharpe: float
    max_dd_bps: float
    n_bars: int
    active_bars: int
    n_fills: int
    fill_rate: float
    avg_abs_inv: float
    max_abs_inv: int
    regime_stats: dict  # regime_id -> {bars, pnl_bps, sharpe}


def run_simulation(
    df: pl.DataFrame,
    strategy_fn,
    strategy_name: str,
    fee: float,
    fee_label: str,
    symbol: str,
    params: ASParams,
) -> SimResult:
    """Run MM simulation bar-by-bar."""
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    rvol = df["rvol_24h"].to_numpy()
    regime = df["regime"].to_numpy()
    n = len(close)

    # State
    inventory = 0
    cash = 0.0
    pnl_series = []
    regime_pnl = {r: [] for r in range(4)}
    regime_bars = {r: 0 for r in range(4)}
    active_bars = 0
    n_fills = 0
    fill_opportunities = 0
    inv_history = []

    for i in range(n - 1):
        r = regime[i]
        if r < 0 or np.isnan(rvol[i]):
            pnl_series.append(0.0)
            continue

        regime_bars[r] += 1
        active, size_mult, gamma_adj = strategy_fn(r, params)

        bar_pnl = 0.0

        if active and not np.isnan(rvol[i]) and rvol[i] > 0:
            active_bars += 1
            mid = close[i]
            sigma = rvol[i]

            # Use adjusted params
            adj_params = ASParams(gamma=gamma_adj, kappa=params.kappa, T=params.T, inv_limit=params.inv_limit)
            bid, ask = compute_as_quotes(mid, sigma, inventory, params.T / 2, adj_params)

            next_low = low[i + 1]
            next_high = high[i + 1]
            next_close = close[i + 1]

            fill_opportunities += 2

            # Fill logic
            bid_filled = next_low <= bid and abs(inventory + 1) <= params.inv_limit
            ask_filled = next_high >= ask and abs(inventory - 1) <= params.inv_limit

            if bid_filled:
                trade_size = max(1, int(size_mult))
                if abs(inventory + trade_size) <= params.inv_limit:
                    inventory += trade_size
                    cash -= bid * trade_size
                    # fee on notional
                    cash -= fee * bid * trade_size
                    n_fills += 1

            if ask_filled:
                trade_size = max(1, int(size_mult))
                if abs(inventory - trade_size) <= params.inv_limit:
                    inventory -= trade_size
                    cash += ask * trade_size
                    # fee on notional
                    cash -= fee * ask * trade_size
                    n_fills += 1

            # Mark-to-market PnL
            portfolio_value = cash + inventory * next_close
            bar_pnl = portfolio_value - (cash + inventory * mid)
            # Actually track cumulative properly below

        pnl_series.append(bar_pnl)
        regime_pnl[r].append(bar_pnl)
        inv_history.append(abs(inventory))

    # Recompute proper mark-to-market PnL series
    # Re-run with proper tracking
    inventory = 0
    cash = 0.0
    mtm_series = [0.0]
    regime_mtm = {r: [] for r in range(4)}

    for i in range(n - 1):
        r = regime[i]
        if r < 0 or np.isnan(rvol[i]):
            mtm_series.append(cash + inventory * close[i + 1])
            continue

        active, size_mult, gamma_adj = strategy_fn(r, params)

        if active and not np.isnan(rvol[i]) and rvol[i] > 0:
            mid = close[i]
            sigma = rvol[i]
            adj_params = ASParams(gamma=gamma_adj, kappa=params.kappa, T=params.T, inv_limit=params.inv_limit)
            bid, ask = compute_as_quotes(mid, sigma, inventory, params.T / 2, adj_params)

            next_low = low[i + 1]
            next_high = high[i + 1]

            bid_filled = next_low <= bid and abs(inventory + 1) <= params.inv_limit
            ask_filled = next_high >= ask and abs(inventory - 1) <= params.inv_limit

            if bid_filled:
                trade_size = max(1, int(size_mult))
                if abs(inventory + trade_size) <= params.inv_limit:
                    inventory += trade_size
                    cash -= bid * trade_size
                    cash -= fee * bid * trade_size

            if ask_filled:
                trade_size = max(1, int(size_mult))
                if abs(inventory - trade_size) <= params.inv_limit:
                    inventory -= trade_size
                    cash += ask * trade_size
                    cash -= fee * ask * trade_size

        mtm_val = cash + inventory * close[i + 1]
        mtm_series.append(mtm_val)

        if r >= 0:
            # Per-bar PnL for this regime
            bar_pnl = mtm_series[-1] - mtm_series[-2]
            regime_mtm[r].append(bar_pnl)

    mtm = np.array(mtm_series)
    bar_pnls = np.diff(mtm)

    # Compute stats
    # Use first valid mid price as reference for bps conversion
    valid_idx = np.where(regime >= 0)[0]
    ref_price = close[valid_idx[0]] if len(valid_idx) > 0 else close[0]

    total_pnl_bps = (mtm[-1] / ref_price) * 1e4

    # Sharpe
    if len(bar_pnls) > 0 and np.std(bar_pnls) > 0:
        hourly_sharpe = np.mean(bar_pnls) / np.std(bar_pnls)
        annual_sharpe = hourly_sharpe * np.sqrt(8760)
    else:
        annual_sharpe = 0.0

    # Max drawdown
    cummax = np.maximum.accumulate(mtm)
    drawdown = mtm - cummax
    max_dd_bps = (np.min(drawdown) / ref_price) * 1e4

    # Fill rate
    fill_rate = n_fills / (2 * active_bars) if active_bars > 0 else 0.0

    # Inventory stats
    avg_abs_inv = np.mean(inv_history) if inv_history else 0.0
    max_abs_inv = int(np.max(inv_history)) if inv_history else 0

    # Regime stats
    regime_stats = {}
    for r in range(4):
        rpnls = regime_mtm[r]
        if len(rpnls) > 1 and np.std(rpnls) > 0:
            r_sharpe = (np.mean(rpnls) / np.std(rpnls)) * np.sqrt(8760)
        else:
            r_sharpe = 0.0
        r_pnl_bps = (sum(rpnls) / ref_price) * 1e4
        regime_stats[r] = {
            "bars": regime_bars[r],
            "pnl_bps": r_pnl_bps,
            "sharpe": r_sharpe,
        }

    return SimResult(
        name=strategy_name,
        fee_label=fee_label,
        symbol=symbol,
        total_pnl_bps=total_pnl_bps,
        annual_sharpe=annual_sharpe,
        max_dd_bps=max_dd_bps,
        n_bars=n,
        active_bars=active_bars,
        n_fills=n_fills,
        fill_rate=fill_rate,
        avg_abs_inv=avg_abs_inv,
        max_abs_inv=max_abs_inv,
        regime_stats=regime_stats,
    )


# ── Display ─────────────────────────────────────────────────────────
def print_regime_distribution(df: pl.DataFrame, symbol: str):
    """Print regime distribution."""
    regime = df["regime"].to_numpy()
    valid = regime[regime >= 0]
    total = len(valid)

    print(f"\n{'='*60}")
    print(f"  Regime Distribution: {symbol}")
    print(f"{'='*60}")
    print(f"  {'Regime':<12} {'Count':>8} {'Pct':>8}")
    print(f"  {'-'*28}")
    for r in range(4):
        cnt = np.sum(valid == r)
        pct = cnt / total * 100 if total > 0 else 0
        print(f"  {REGIME_NAMES[r]:<12} {cnt:>8} {pct:>7.1f}%")
    print(f"  {'Total':<12} {total:>8} {'100.0':>7}%")


def print_results_table(results: list[SimResult], fee_label: str, symbol: str):
    """Print formatted results table for one fee scenario."""
    filtered = [r for r in results if r.fee_label == fee_label and r.symbol == symbol]
    if not filtered:
        return

    print(f"\n{'='*90}")
    print(f"  {symbol} | Fee: {fee_label}")
    print(f"{'='*90}")
    header = f"  {'Strategy':<16} {'PnL(bps)':>10} {'Sharpe':>8} {'MaxDD(bps)':>11} {'Active%':>8} {'Fills':>7} {'FillRate':>9} {'AvgInv':>7} {'MaxInv':>7}"
    print(header)
    print(f"  {'-'*(len(header)-2)}")

    for r in filtered:
        active_pct = r.active_bars / r.n_bars * 100
        print(
            f"  {r.name:<16} {r.total_pnl_bps:>10.1f} {r.annual_sharpe:>8.2f} {r.max_dd_bps:>11.1f} "
            f"{active_pct:>7.1f}% {r.n_fills:>7} {r.fill_rate:>8.1%} {r.avg_abs_inv:>7.1f} {r.max_abs_inv:>7}"
        )

    # Regime breakdown
    print(f"\n  Regime Breakdown (PnL bps / Sharpe):")
    print(f"  {'Strategy':<16}", end="")
    for rn in REGIME_NAMES:
        print(f" {rn:>16}", end="")
    print()
    print(f"  {'-'*80}")
    for r in filtered:
        print(f"  {r.name:<16}", end="")
        for ri in range(4):
            rs = r.regime_stats[ri]
            print(f" {rs['pnl_bps']:>7.1f}/{rs['sharpe']:>6.2f}", end="")
        print()

    # Regime time allocation
    print(f"\n  Regime Bars:")
    print(f"  {'Strategy':<16}", end="")
    for rn in REGIME_NAMES:
        print(f" {rn:>10}", end="")
    print()
    print(f"  {'-'*60}")
    for r in filtered:
        print(f"  {r.name:<16}", end="")
        for ri in range(4):
            print(f" {r.regime_stats[ri]['bars']:>10}", end="")
        print()


def print_critical_test(results: list[SimResult], symbol: str):
    """Print the critical test: VoV-Filter vs Always-On Sharpe comparison."""
    print(f"\n{'='*70}")
    print(f"  CRITICAL TEST: {symbol}")
    print(f"  Does VoV-Filter (HV-LVV only) beat Always-On Sharpe?")
    print(f"{'='*70}")
    for fee_label in FEE_SCENARIOS:
        always = [r for r in results if r.name == "Always-On" and r.fee_label == fee_label and r.symbol == symbol]
        vov_f = [r for r in results if r.name == "VoV-Filter" and r.fee_label == fee_label and r.symbol == symbol]
        if always and vov_f:
            a, v = always[0], vov_f[0]
            diff = v.annual_sharpe - a.annual_sharpe
            winner = "VoV-Filter" if diff > 0 else "Always-On"
            mark = "YES" if diff > 0 else "NO"
            print(
                f"  {fee_label:<20} Always-On={a.annual_sharpe:>6.2f}  VoV-Filter={v.annual_sharpe:>6.2f}  "
                f"Delta={diff:>+6.2f}  [{mark}: {winner}]"
            )


# ── Main ────────────────────────────────────────────────────────────
def main():
    params = ASParams()

    datasets = {
        "SOL": "data/binance_solusdt_1h_full.parquet",
        "ETH": "data/binance_ethusdt_1h_full.parquet",
    }

    all_results: list[SimResult] = []

    for symbol, path in datasets.items():
        print(f"\n{'#'*70}")
        print(f"  Loading {symbol} from {path}")
        print(f"{'#'*70}")

        df = pl.read_parquet(path)
        df = df.select(["timestamp", "open", "high", "low", "close", "volume"]).sort("timestamp")
        print(f"  Rows: {len(df)}, Range: {df['timestamp'][0]} ~ {df['timestamp'][-1]}")

        print("  Computing features...")
        df = compute_features(df)

        print_regime_distribution(df, symbol)

        # Run all strategy x fee combos
        for fee_label, fee_val in FEE_SCENARIOS.items():
            for strat_name, strat_fn in STRATEGIES.items():
                result = run_simulation(df, strat_fn, strat_name, fee_val, fee_label, symbol, params)
                all_results.append(result)

        # Print results
        for fee_label in FEE_SCENARIOS:
            print_results_table(all_results, fee_label, symbol)

        print_critical_test(all_results, symbol)

    # Cross-asset summary
    print(f"\n\n{'#'*70}")
    print(f"  CROSS-ASSET SUMMARY")
    print(f"{'#'*70}")
    print(f"\n  {'Symbol':<6} {'Strategy':<16} {'Fee':<20} {'PnL(bps)':>10} {'Sharpe':>8} {'MaxDD(bps)':>11}")
    print(f"  {'-'*75}")
    for symbol in datasets:
        for fee_label in FEE_SCENARIOS:
            for r in all_results:
                if r.symbol == symbol and r.fee_label == fee_label:
                    print(
                        f"  {r.symbol:<6} {r.name:<16} {r.fee_label:<20} "
                        f"{r.total_pnl_bps:>10.1f} {r.annual_sharpe:>8.2f} {r.max_dd_bps:>11.1f}"
                    )
            print()


if __name__ == "__main__":
    main()
