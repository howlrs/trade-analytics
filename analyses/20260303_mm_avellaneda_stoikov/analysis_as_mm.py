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
"""

import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


# ---------------------------------------------------------------------------
# Cell 1: Setup (imports, constants)
# ---------------------------------------------------------------------------
@app.cell
def setup():
    from __future__ import annotations

    import itertools
    from dataclasses import dataclass, field
    from pathlib import Path
    from typing import NamedTuple

    import marimo as mo
    import numpy as np
    import polars as pl

    DATA_DIR = Path(__file__).resolve().parents[2] / "data"
    MAKER_FEE_BPS = 2.0
    TAKER_FEE_BPS = 8.0
    HOURS_PER_YEAR = 8760
    TRAIN_CUTOFF = "2025-09-01"
    SYMBOLS = ["ETH", "SOL"]

    return (
        DATA_DIR,
        HOURS_PER_YEAR,
        MAKER_FEE_BPS,
        SYMBOLS,
        TAKER_FEE_BPS,
        TRAIN_CUTOFF,
        dataclass,
        field,
        itertools,
        mo,
        np,
        pl,
        NamedTuple,
        Path,
    )


# ---------------------------------------------------------------------------
# Cell 2: Data loading & feature engineering
# ---------------------------------------------------------------------------
@app.cell
def data_funcs(DATA_DIR, TRAIN_CUTOFF, pl):
    def load_ohlcv(symbol: str) -> pl.DataFrame:
        """Load 1h OHLCV from Binance and compute vol features."""
        _path = DATA_DIR / f"binance_{symbol.lower()}usdt_1h.parquet"
        _df = pl.read_parquet(_path)

        _df = _df.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")).alias("timestamp")
        )
        _df = _df.sort("timestamp")

        _df = _df.with_columns([
            (pl.col("close").log() - pl.col("close").shift(1).log()).alias("log_ret"),
            ((pl.col("high") - pl.col("low")) / pl.col("close")).alias("range_pct"),
        ])

        _df = _df.with_columns([
            pl.col("log_ret").rolling_std(24).alias("rvol_24h"),
            pl.col("range_pct").rolling_mean(24).alias("avg_range_24h"),
        ])

        _df = _df.with_columns(
            ((pl.col("high") + pl.col("low")) / 2.0).alias("mid")
        )

        return _df.drop_nulls(subset=["rvol_24h"])

    def split_train_test(df: pl.DataFrame) -> tuple:
        _cutoff = pl.lit(TRAIN_CUTOFF).str.strptime(pl.Datetime("us"), "%Y-%m-%d")
        _train = df.filter(pl.col("timestamp") < _cutoff)
        _test = df.filter(pl.col("timestamp") >= _cutoff)
        return _train, _test

    return load_ohlcv, split_train_test


# ---------------------------------------------------------------------------
# Cell 3: AS model helper functions
# ---------------------------------------------------------------------------
@app.cell
def as_helpers(np):
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

    return as_reservation_price, as_optimal_spread


# ---------------------------------------------------------------------------
# Cell 4: Simulation infrastructure
# ---------------------------------------------------------------------------
@app.cell
def simulation_infra(
    MAKER_FEE_BPS,
    as_optimal_spread,
    as_reservation_price,
    dataclass,
    field,
    np,
    NamedTuple,
):
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
        spread_bps: float

    def compute_quotes_as(
        mid: float,
        q: float,
        gamma: float,
        sigma_ret: float,
        kappa: float,
        T_hours: int,
        t_in_cycle: int,
    ) -> QuoteResult:
        """Compute AS bid/ask quotes with inventory skew."""
        _tau = max((T_hours - t_in_cycle) / T_hours, 0.01)
        _sigma_price = sigma_ret * mid
        _reservation = as_reservation_price(mid, q, gamma, _sigma_price, _tau)
        _spread = as_optimal_spread(gamma, _sigma_price, _tau, kappa)
        _half_spread = _spread / 2.0
        _bid = _reservation - _half_spread
        _ask = _reservation + _half_spread
        _spread_bps = (_ask - _bid) / mid * 10000.0
        return QuoteResult(bid=_bid, ask=_ask, spread_bps=_spread_bps)

    def compute_quotes_fixed(mid: float, spread_bps: float) -> QuoteResult:
        """Fixed symmetric spread around mid."""
        _half = mid * spread_bps / 10000.0 / 2.0
        return QuoteResult(bid=mid - _half, ask=mid + _half, spread_bps=spread_bps)

    def run_simulation(
        df,
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
        _state = MMState()
        _maker_fee_frac = MAKER_FEE_BPS / 10000.0

        _highs = df["high"].to_numpy()
        _lows = df["low"].to_numpy()
        _closes = df["close"].to_numpy()
        _mids = df["mid"].to_numpy()
        _sigmas = df["rvol_24h"].to_numpy()

        _n = len(df)

        for _i in range(_n - 1):
            _state.n_periods += 1
            _mid = _mids[_i]
            _sigma = _sigmas[_i]
            _t_in_cycle = _i % T_hours

            if strategy == "fixed":
                _q = compute_quotes_fixed(_mid, fixed_spread_bps)
            elif strategy == "as_naive":
                _q = compute_quotes_as(
                    _mid, _state.inventory, gamma, _sigma, kappa, T_hours, _t_in_cycle,
                )
            elif strategy == "as_regime":
                _g = gamma
                if regime_aware and vol_percentile_70 is not None:
                    if _sigma > vol_percentile_70:
                        _g = gamma * 3.0
                _q = compute_quotes_as(
                    _mid, _state.inventory, _g, _sigma, kappa, T_hours, _t_in_cycle,
                )
            else:
                raise ValueError(f"Unknown strategy: {strategy}")

            _bid_price, _ask_price = _q.bid, _q.ask
            _state.spread_history.append(_q.spread_bps)

            _next_low = _lows[_i + 1]
            _next_high = _highs[_i + 1]
            _next_close = _closes[_i + 1]

            _bid_filled = False
            _ask_filled = False

            if _next_low <= _bid_price and _state.inventory < inv_limit:
                _bid_filled = True
                _state.inventory += 1
                _state.cash -= _bid_price * (1.0 + _maker_fee_frac)
                _state.n_bid_fills += 1

            if _next_high >= _ask_price and _state.inventory > -inv_limit:
                _ask_filled = True
                _state.inventory -= 1
                _state.cash += _ask_price * (1.0 - _maker_fee_frac)
                _state.n_ask_fills += 1

            if _bid_filled and _ask_filled:
                _state.n_both_fills += 1
            elif not _bid_filled and not _ask_filled:
                _state.n_no_fills += 1

            _state.inventory_history.append(_state.inventory)

            _mtm = _state.cash + _state.inventory * _next_close
            _state.pnl_history.append(_mtm)

        return _state

    return MMState, QuoteResult, compute_quotes_as, compute_quotes_fixed, run_simulation


# ---------------------------------------------------------------------------
# Cell 5: Metrics
# ---------------------------------------------------------------------------
@app.cell
def metrics_cell(dataclass, np, HOURS_PER_YEAR):
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

    def compute_metrics(state, initial_price: float) -> Metrics:
        """Compute performance metrics from simulation state."""
        _pnl = np.array(state.pnl_history)
        _inv = np.array(state.inventory_history)
        _spreads = np.array(state.spread_history)

        if len(_pnl) < 2:
            return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        _returns = np.diff(_pnl)
        _mean_ret = np.mean(_returns)
        _std_ret = np.std(_returns)

        _sharpe = (_mean_ret / _std_ret * np.sqrt(HOURS_PER_YEAR)) if _std_ret > 0 else 0.0

        _total_pnl = _pnl[-1]
        _total_pnl_bps = _total_pnl / initial_price * 10000.0

        _peak = np.maximum.accumulate(_pnl)
        _drawdown = _peak - _pnl
        _max_dd_pct = np.max(_drawdown) / initial_price * 100.0 if len(_drawdown) > 0 else 0.0

        _total_fills = state.n_bid_fills + state.n_ask_fills
        _n = state.n_periods

        _avg_abs_inv = np.mean(np.abs(_inv)) if len(_inv) > 0 else 0.0
        _avg_spread = np.mean(_spreads) if len(_spreads) > 0 else 0.0

        return Metrics(
            sharpe=_sharpe,
            total_pnl_bps=_total_pnl_bps,
            max_drawdown_pct=_max_dd_pct,
            avg_abs_inventory=_avg_abs_inv,
            fill_rate_both=state.n_both_fills / _n if _n > 0 else 0,
            fill_rate_one=(state.n_bid_fills + state.n_ask_fills - 2 * state.n_both_fills) / _n if _n > 0 else 0,
            fill_rate_none=state.n_no_fills / _n if _n > 0 else 0,
            pnl_per_fill=_total_pnl / _total_fills if _total_fills > 0 else 0,
            total_fills=_total_fills,
            n_periods=_n,
            avg_spread_bps=_avg_spread,
        )

    return Metrics, compute_metrics


# ---------------------------------------------------------------------------
# Cell 6: Load data for both symbols
# ---------------------------------------------------------------------------
@app.cell
def load_data(mo, SYMBOLS, MAKER_FEE_BPS, TRAIN_CUTOFF, load_ohlcv, split_train_test):
    datasets = {}
    for _sym in SYMBOLS:
        _df = load_ohlcv(_sym)
        _train, _test = split_train_test(_df)
        datasets[_sym] = {"full": _df, "train": _train, "test": _test}

    _lines = [
        "# Avellaneda-Stoikov Market Making Backtest",
        "",
        "Focus: ETH, SOL | Data: 1h OHLCV (Binance)",
        f"- Train: < {TRAIN_CUTOFF} | Test: >= {TRAIN_CUTOFF}",
        f"- Maker fee: {MAKER_FEE_BPS}bps per fill | sigma in price-space: sigma_price = rvol_24h * mid",
        "",
    ]
    for _sym in SYMBOLS:
        _d = datasets[_sym]
        _lines.append(
            f"**{_sym}**: {len(_d['full'])} total bars "
            f"({len(_d['train'])} train, {len(_d['test'])} test), "
            f"price range: {_d['full']['close'].min():.2f} - {_d['full']['close'].max():.2f}"
        )

    mo.md("\n".join(_lines))
    return (datasets,)


# ---------------------------------------------------------------------------
# Cell 7: Volatility stats display
# ---------------------------------------------------------------------------
@app.cell
def vol_stats(mo, np, SYMBOLS, datasets):
    _lines = ["## Volatility Statistics (Train Set)\n"]

    for _sym in SYMBOLS:
        _train = datasets[_sym]["train"]
        _sigma = _train["rvol_24h"].to_numpy()
        _mids = _train["mid"].to_numpy()
        _sigma_price = _sigma * _mids
        _avg_mid = np.mean(_mids)
        _ac1 = np.corrcoef(_sigma[:-1], _sigma[1:])[0, 1]

        _lines.append(f"### {_sym}")
        _lines.append("```")
        _lines.append(f"  --- Fractional (return-space) ---")
        _lines.append(f"  Mean:   {np.mean(_sigma):.6f}  ({np.mean(_sigma)*10000:.1f} bp)")
        _lines.append(f"  Median: {np.median(_sigma):.6f}  ({np.median(_sigma)*10000:.1f} bp)")
        _lines.append(f"  P10:    {np.percentile(_sigma, 10):.6f}")
        _lines.append(f"  P70:    {np.percentile(_sigma, 70):.6f}")
        _lines.append(f"  P90:    {np.percentile(_sigma, 90):.6f}")
        _lines.append(f"  --- Price-space (sigma * mid) ---")
        _lines.append(f"  Mean:   {np.mean(_sigma_price):.2f}")
        _lines.append(f"  Median: {np.median(_sigma_price):.2f}")
        _lines.append(f"  Avg mid price: {_avg_mid:.2f}")
        _lines.append(f"  AC(1):  {_ac1:.4f}")
        _lines.append("```\n")

    mo.md("\n".join(_lines))
    return


# ---------------------------------------------------------------------------
# Cell 8: Spread diagnostics display
# ---------------------------------------------------------------------------
@app.cell
def spread_diag(mo, np, SYMBOLS, datasets, as_optimal_spread):
    _lines = ["## Spread Diagnostics (at median vol, tau=0.5)\n"]

    for _sym in SYMBOLS:
        _train = datasets[_sym]["train"]
        _median_sigma = float(np.median(_train["rvol_24h"].to_numpy()))
        _median_mid = float(np.median(_train["mid"].to_numpy()))

        _lines.append(f"### {_sym} (mid={_median_mid:.2f}, sigma_ret={_median_sigma:.6f})")
        _lines.append("```")
        _lines.append(f"  {'gamma':>8} {'kappa':>8} {'T':>4} {'tau':>6} | {'spread':>10} {'spread_bp':>10}")
        _lines.append(f"  {'-'*60}")
        for _gamma in [0.01, 0.1, 1.0, 10.0]:
            for _kappa in [0.5, 1.0, 5.0]:
                for _T in [8, 24]:
                    _sigma_price = _median_sigma * _median_mid
                    _tau = 0.5
                    _spread = as_optimal_spread(_gamma, _sigma_price, _tau, _kappa)
                    _spread_bp = _spread / _median_mid * 10000
                    _lines.append(
                        f"  {_gamma:8.2f} {_kappa:8.2f} {_T:4d} {_tau:6.2f} | "
                        f"{_spread:10.4f} {_spread_bp:9.1f}bp"
                    )
        _lines.append("```\n")

    mo.md("\n".join(_lines))
    return


# ---------------------------------------------------------------------------
# Cell 9: Grid search
# ---------------------------------------------------------------------------
@app.cell
def grid_search_cell(
    mo,
    np,
    itertools,
    SYMBOLS,
    datasets,
    run_simulation,
    compute_metrics,
):
    def grid_search_as(train_df, test_df, symbol):
        """Run parameter grid search for AS model on train, evaluate best on test."""
        _gammas = [0.01, 0.1, 1.0, 10.0]
        _kappas = [0.5, 1.0, 2.0, 5.0]
        _T_hours_list = [8, 24]
        _inv_limits = [5, 10, 20]

        _initial_price_train = train_df["close"][0]
        _initial_price_test = test_df["close"][0]

        _header_lines = [
            f"### GRID SEARCH: {symbol} -- AS Naive Model",
            f"Train: {len(train_df)} bars, Test: {len(test_df)} bars\n",
            "```",
            f"{'gamma':>8} {'kappa':>8} {'T':>4} {'inv_lim':>8} | "
            f"{'Sharpe':>8} {'PnL(bps)':>10} {'MaxDD%':>8} {'Fills':>7} "
            f"{'Both%':>7} {'None%':>7} {'AvgSprd':>8} {'AvgInv':>7}",
            "-" * 110,
        ]

        _results = []
        _detail_lines = []

        for _gamma, _kappa, _T_hours, _inv_limit in itertools.product(
            _gammas, _kappas, _T_hours_list, _inv_limits
        ):
            _state = run_simulation(
                train_df,
                strategy="as_naive",
                gamma=_gamma,
                kappa=_kappa,
                T_hours=_T_hours,
                inv_limit=_inv_limit,
            )
            _m = compute_metrics(_state, _initial_price_train)
            _results.append((_gamma, _kappa, _T_hours, _inv_limit, _m))

            _detail_lines.append(
                f"{_gamma:8.2f} {_kappa:8.2f} {_T_hours:4d} {_inv_limit:8d} | "
                f"{_m.sharpe:8.2f} {_m.total_pnl_bps:10.1f} {_m.max_drawdown_pct:8.2f} "
                f"{_m.total_fills:7d} {_m.fill_rate_both*100:6.1f}% {_m.fill_rate_none*100:6.1f}% "
                f"{_m.avg_spread_bps:7.1f}bp {_m.avg_abs_inventory:6.1f}"
            )

        _results.sort(key=lambda x: x[4].sharpe, reverse=True)

        _top_lines = [
            "```\n",
            f"#### TOP 10 CONFIGS by Sharpe (Train) -- {symbol}\n",
            "```",
        ]
        for _rank, (_gamma, _kappa, _T_hours, _inv_limit, _m) in enumerate(_results[:10], 1):
            _top_lines.append(f"  #{_rank}: gamma={_gamma}, kappa={_kappa}, T={_T_hours}h, inv_limit={_inv_limit}")
            _top_lines.append(
                f"       Sharpe={_m.sharpe:.3f}, PnL={_m.total_pnl_bps:.1f}bps, "
                f"MaxDD={_m.max_drawdown_pct:.2f}%, Fills={_m.total_fills}, "
                f"AvgSpread={_m.avg_spread_bps:.1f}bp, AvgInv={_m.avg_abs_inventory:.1f}"
            )

        _test_lines = [
            "```\n",
            f"#### TEST EVALUATION -- Top 5 configs -- {symbol}\n",
            "```",
        ]
        for _rank, (_gamma, _kappa, _T_hours, _inv_limit, _train_m) in enumerate(_results[:5], 1):
            _state_test = run_simulation(
                test_df,
                strategy="as_naive",
                gamma=_gamma,
                kappa=_kappa,
                T_hours=_T_hours,
                inv_limit=_inv_limit,
            )
            _mt = compute_metrics(_state_test, _initial_price_test)
            _test_lines.append(f"  #{_rank}: gamma={_gamma}, kappa={_kappa}, T={_T_hours}h, inv_limit={_inv_limit}")
            _test_lines.append(f"       [Train] Sharpe={_train_m.sharpe:.3f}, PnL={_train_m.total_pnl_bps:.1f}bps")
            _test_lines.append(
                f"       [Test]  Sharpe={_mt.sharpe:.3f}, PnL={_mt.total_pnl_bps:.1f}bps, "
                f"MaxDD={_mt.max_drawdown_pct:.2f}%, Fills={_mt.total_fills}, "
                f"AvgSpread={_mt.avg_spread_bps:.1f}bp, AvgInv={_mt.avg_abs_inventory:.1f}, "
                f"PnL/Fill={_mt.pnl_per_fill:.4f}"
            )
        _test_lines.append("```")

        _all_lines = _header_lines + _detail_lines + _top_lines + _test_lines
        return _results, "\n".join(_all_lines)

    _output_parts = ["## Grid Search Results\n"]
    all_best_configs = {}

    for _sym in SYMBOLS:
        _d = datasets[_sym]
        _results, _text = grid_search_as(_d["train"], _d["test"], _sym)
        _output_parts.append(_text)

        _best = _results[0]
        all_best_configs[_sym] = {
            "gamma": _best[0],
            "kappa": _best[1],
            "T_hours": _best[2],
            "inv_limit": _best[3],
        }

    mo.md("\n\n".join(_output_parts))
    return (all_best_configs,)


# ---------------------------------------------------------------------------
# Cell 10: Strategy comparison
# ---------------------------------------------------------------------------
@app.cell
def strategy_comparison(
    mo,
    np,
    pl,
    SYMBOLS,
    TRAIN_CUTOFF,
    datasets,
    all_best_configs,
    run_simulation,
    compute_metrics,
):
    _output_parts = ["## Strategy Comparison (Test Set)\n"]

    for _sym in SYMBOLS:
        _d = datasets[_sym]
        _test_df = _d["test"]
        _train_df = _d["train"]
        _initial_price = _test_df["close"][0]
        _cfg = all_best_configs[_sym]
        _g, _k, _t, _inv = _cfg["gamma"], _cfg["kappa"], _cfg["T_hours"], _cfg["inv_limit"]

        _vol_p70 = float(_train_df["rvol_24h"].quantile(0.70))

        _strategies = [
            ("Fixed 50bp", dict(strategy="fixed", fixed_spread_bps=50.0, inv_limit=_inv)),
            ("Fixed 100bp", dict(strategy="fixed", fixed_spread_bps=100.0, inv_limit=_inv)),
            ("AS Naive", dict(
                strategy="as_naive", gamma=_g, kappa=_k,
                T_hours=_t, inv_limit=_inv,
            )),
            ("AS Regime-Aware", dict(
                strategy="as_regime", gamma=_g, kappa=_k,
                T_hours=_t, inv_limit=_inv,
                regime_aware=True, vol_percentile_70=_vol_p70,
            )),
        ]

        _lines = [
            f"### {_sym}",
            f"Best AS params: gamma={_g}, kappa={_k}, T={_t}h, inv_limit={_inv}",
            f"Vol P70 threshold: {_vol_p70:.6f}\n",
            "```",
            f"{'Strategy':<20} | {'Sharpe':>8} {'PnL(bps)':>10} {'MaxDD%':>8} "
            f"{'Fills':>7} {'Both%':>7} {'None%':>7} {'AvgSprd':>8} "
            f"{'AvgInv':>7} {'PnL/Fill':>10} {'FinalInv':>9}",
            "-" * 120,
        ]

        for _name, _kwargs in _strategies:
            _state = run_simulation(_test_df, **_kwargs)
            _m = compute_metrics(_state, _initial_price)
            _lines.append(
                f"{_name:<20} | {_m.sharpe:8.2f} {_m.total_pnl_bps:10.1f} {_m.max_drawdown_pct:8.2f} "
                f"{_m.total_fills:7d} {_m.fill_rate_both*100:6.1f}% {_m.fill_rate_none*100:6.1f}% "
                f"{_m.avg_spread_bps:7.1f}bp {_m.avg_abs_inventory:6.1f} "
                f"{_m.pnl_per_fill:10.4f} {_state.inventory:9.1f}"
            )

        _lines.append("```\n")
        _output_parts.append("\n".join(_lines))

    mo.md("\n\n".join(_output_parts))
    return


# ---------------------------------------------------------------------------
# Cell 11: Detailed monthly breakdown
# ---------------------------------------------------------------------------
@app.cell
def monthly_breakdown(
    mo,
    np,
    SYMBOLS,
    datasets,
    all_best_configs,
    run_simulation,
):
    _output_parts = ["## Detailed Monthly Breakdown (Full Dataset)\n"]

    for _sym in SYMBOLS:
        _d = datasets[_sym]
        _df = _d["full"]
        _cfg = all_best_configs[_sym]
        _gamma, _kappa, _T_hours, _inv_limit = (
            _cfg["gamma"], _cfg["kappa"], _cfg["T_hours"], _cfg["inv_limit"]
        )

        _state = run_simulation(
            _df, strategy="as_naive",
            gamma=_gamma, kappa=_kappa, T_hours=_T_hours, inv_limit=_inv_limit,
        )

        _timestamps = _df["timestamp"].to_list()
        _closes = _df["close"].to_numpy()
        _initial_price = _closes[0]
        _pnl_arr = np.array(_state.pnl_history)
        _inv_arr = np.array(_state.inventory_history)
        _spread_arr = np.array(_state.spread_history)

        _monthly = {}
        _n = len(_pnl_arr)
        for _i in range(_n):
            _month_key = _timestamps[_i].strftime("%Y-%m")
            if _month_key not in _monthly:
                _monthly[_month_key] = {"n": 0, "start_idx": _i, "end_idx": _i}
            _monthly[_month_key]["n"] += 1
            _monthly[_month_key]["end_idx"] = _i

        _lines = [
            f"### {_sym}",
            f"gamma={_gamma}, kappa={_kappa}, T={_T_hours}h, inv_limit={_inv_limit}\n",
            "```",
            f"{'Month':<10} | {'Bars':>5} {'AvgInv':>7} {'MaxInv':>7} "
            f"{'AvgSprd':>8} {'MonthPnL':>12} {'CumPnL':>12} {'CumBps':>9}",
            "-" * 90,
        ]

        _sorted_months = sorted(_monthly.keys())
        for _month in _sorted_months:
            _md = _monthly[_month]
            _si, _ei = _md["start_idx"], _md["end_idx"]
            _month_inv = _inv_arr[_si:_ei+1]
            _month_spread = _spread_arr[_si:_ei+1]
            _month_pnl_start = _pnl_arr[_si - 1] if _si > 0 else 0.0
            _month_pnl_end = _pnl_arr[_ei]
            _month_pnl = _month_pnl_end - _month_pnl_start
            _cum_pnl_bps = _month_pnl_end / _initial_price * 10000.0

            _lines.append(
                f"{_month:<10} | {_md['n']:5d} {np.mean(np.abs(_month_inv)):6.1f} "
                f"{np.max(np.abs(_month_inv)):7.1f} {np.mean(_month_spread):7.1f}bp "
                f"{_month_pnl:12.2f} {_month_pnl_end:12.2f} {_cum_pnl_bps:8.1f}bp"
            )

        _lines.append("")
        _lines.append(f"  Full-period inventory stats:")
        _lines.append(f"    Mean absolute: {np.mean(np.abs(_inv_arr)):.2f}")
        _lines.append(f"    Max absolute:  {np.max(np.abs(_inv_arr)):.2f}")
        _lines.append(f"    Std:           {np.std(_inv_arr):.2f}")
        _lines.append(f"    Final:         {_state.inventory:.1f}")
        _lines.append(f"    Final PnL:     {_pnl_arr[-1]:.2f} ({_pnl_arr[-1]/_initial_price*10000:.1f} bps)")
        _lines.append(f"  Full-period spread stats:")
        _lines.append(f"    Mean:   {np.mean(_spread_arr):.1f} bp")
        _lines.append(f"    Median: {np.median(_spread_arr):.1f} bp")
        _lines.append(f"    P10:    {np.percentile(_spread_arr, 10):.1f} bp")
        _lines.append(f"    P90:    {np.percentile(_spread_arr, 90):.1f} bp")
        _lines.append(f"  Fill stats:")
        _lines.append(f"    Bid fills:  {_state.n_bid_fills}")
        _lines.append(f"    Ask fills:  {_state.n_ask_fills}")
        _lines.append(f"    Both:       {_state.n_both_fills} ({_state.n_both_fills/_state.n_periods*100:.1f}%)")
        _lines.append(f"    None:       {_state.n_no_fills} ({_state.n_no_fills/_state.n_periods*100:.1f}%)")
        _lines.append("```\n")
        _output_parts.append("\n".join(_lines))

    mo.md("\n\n".join(_output_parts))
    return


# ---------------------------------------------------------------------------
# Cell 12: Summary and key takeaways
# ---------------------------------------------------------------------------
@app.cell
def summary_cell(
    mo,
    np,
    pl,
    SYMBOLS,
    TRAIN_CUTOFF,
    datasets,
    all_best_configs,
    run_simulation,
    compute_metrics,
):
    _output_parts = ["## Final Summary\n"]

    for _sym in SYMBOLS:
        _d = datasets[_sym]
        _test_df = _d["test"]
        _train_df = _d["train"]
        _initial_price = _test_df["close"][0]
        _cfg = all_best_configs[_sym]
        _g, _k, _t, _inv = _cfg["gamma"], _cfg["kappa"], _cfg["T_hours"], _cfg["inv_limit"]

        _vol_p70 = float(_train_df["rvol_24h"].quantile(0.70))

        _configs = [
            ("Fixed 50bp", dict(strategy="fixed", fixed_spread_bps=50.0, inv_limit=_inv)),
            ("Fixed 100bp", dict(strategy="fixed", fixed_spread_bps=100.0, inv_limit=_inv)),
            ("AS Naive", dict(strategy="as_naive", gamma=_g, kappa=_k, T_hours=_t, inv_limit=_inv)),
            ("AS Regime", dict(strategy="as_regime", gamma=_g, kappa=_k, T_hours=_t, inv_limit=_inv,
                               regime_aware=True, vol_percentile_70=_vol_p70)),
        ]

        _lines = [
            f"### {_sym}",
            f"Best config: gamma={_g}, kappa={_k}, T={_t}h, inv_limit={_inv}\n",
            "```",
            f"  {'Strategy':<16} {'Sharpe':>8} {'PnL(bps)':>10} {'MaxDD%':>8} "
            f"{'Fills':>7} {'AvgSprd':>8} {'AvgInv':>7}",
        ]

        for _name, _kwargs in _configs:
            _state = run_simulation(_test_df, **_kwargs)
            _m = compute_metrics(_state, _initial_price)
            _lines.append(
                f"  {_name:<16} {_m.sharpe:8.2f} {_m.total_pnl_bps:10.1f} {_m.max_drawdown_pct:8.2f} "
                f"{_m.total_fills:7d} {_m.avg_spread_bps:7.1f}bp {_m.avg_abs_inventory:6.1f}"
            )

        _lines.append("```\n")
        _output_parts.append("\n".join(_lines))

    _takeaways = """
## Key Takeaways

1. **AS MODEL with price-space sigma** (sigma_price = rvol_24h * mid):
   - Converts hourly return vol into dollar-denominated spread
   - Low gamma + low kappa => wide spread, few fills, large inventory accumulation
   - High gamma + high kappa => tighter spread, more fills, better inventory control

2. **INVENTORY SKEW** (reservation price shift):
   - q * gamma * sigma^2 * tau shifts the mid to favor reducing inventory
   - Effective only when gamma * sigma_price^2 is meaningful relative to spread

3. **REGIME-AWARE** (3x gamma in high vol):
   - Widens spreads when vol > P70, reducing adverse selection risk
   - Incremental improvement over naive AS in volatile periods

4. **FIXED SPREAD baseline**:
   - 50bp: high fill rate, moderate inventory risk
   - 100bp: lower fill rate, less adverse selection
   - Simple but surprisingly competitive on 1h data

5. **LIMITATIONS of 1h OHLCV simulation**:
   - Fill logic: bid filled if next_low <= bid (optimistic, ignores queue position)
   - No intra-hour microstructure or adverse selection modelling
   - Both sides can fill in same bar (optimistic for round-trip capture)
   - Real MM would face: partial fills, latency, queue priority, toxic flow
   - Results are UPPER BOUND on realistic MM performance
"""

    _output_parts.append(_takeaways)
    mo.md("\n".join(_output_parts))
    return


if __name__ == "__main__":
    app.run()
