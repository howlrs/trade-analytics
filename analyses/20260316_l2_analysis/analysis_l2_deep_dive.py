import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


# --- Cell 1: Setup + data load ---
@app.cell
def setup():
    from pathlib import Path

    import marimo as mo
    import matplotlib as mpl
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt
    import numpy as np
    import polars as pl

    fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mpl.rcParams["font.family"] = "Noto Sans CJK JP"
    mpl.rcParams["axes.unicode_minus"] = False

    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from src.l2_analysis import (
        load_l2_data,
        load_trades,
        load_candles_and_fr,
        compute_spread_distribution,
        compute_oracle_divergence_dynamics,
        compute_book_shape,
        estimate_fill_probability,
        measure_adverse_selection,
        recommend_parameters,
    )

    DATA_DIR = Path(__file__).parent.parent.parent / "data"

    l2 = load_l2_data(DATA_DIR)
    trades = load_trades(DATA_DIR)
    candles, fr = load_candles_and_fr(DATA_DIR)

    return (
        DATA_DIR, mo, mpl, plt, np, pl,
        l2, trades, candles, fr,
        compute_spread_distribution,
        compute_oracle_divergence_dynamics,
        compute_book_shape,
        estimate_fill_probability,
        measure_adverse_selection,
        recommend_parameters,
    )


# --- Cell 2: Data overview ---
@app.cell
def overview(mo, l2, trades, candles, fr):
    _l2_start = l2["timestamp"].min()
    _l2_end = l2["timestamp"].max()
    _t_start = trades["timestamp"].min()
    _t_end = trades["timestamp"].max()
    _null_pct = l2.null_count().sum_horizontal().item() / (len(l2) * len(l2.columns)) * 100

    mo.vstack([
        mo.md("## 1. Data Overview"),
        mo.md(f"""
| Item | Value |
|------|-------|
| L2 snapshots | {len(l2):,} |
| Trades | {len(trades):,} |
| Candles (1h) | {len(candles):,} |
| Funding rates | {len(fr):,} |
| L2 period | {_l2_start} → {_l2_end} |
| Trade period | {_t_start} → {_t_end} |
| Null % (L2) | {_null_pct:.2f}% |
"""),
    ])
    return


# --- Cell 3: Spread distribution ---
@app.cell
def spread_analysis(mo, plt, np, pl, l2, compute_spread_distribution):
    _spread = compute_spread_distribution(l2)
    _s = _spread["overall"]

    _fig, (_ax1, _ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Histogram
    _vals = l2["drift_spread_bp"].drop_nulls().to_numpy()
    _ax1.hist(_vals, bins=100, alpha=0.7, edgecolor="black", linewidth=0.3)
    _ax1.axvline(_s["median"], color="red", linestyle="--", label=f'Median: {_s["median"]:.1f} bp')
    _ax1.axvline(_s["mean"], color="orange", linestyle="--", label=f'Mean: {_s["mean"]:.1f} bp')
    _ax1.set_xlabel("Spread (bp)")
    _ax1.set_ylabel("Count")
    _ax1.set_title("Spread Distribution")
    _ax1.legend()

    # Hourly heatmap
    _by_hour = _spread["by_hour"]
    _hours = _by_hour["hour"].to_numpy()
    _means = _by_hour["mean_spread"].to_numpy()
    _ax2.bar(_hours, _means, color="steelblue", alpha=0.8)
    _ax2.set_xlabel("Hour (UTC)")
    _ax2.set_ylabel("Mean Spread (bp)")
    _ax2.set_title("Spread by Hour")
    _ax2.set_xticks(range(0, 24, 2))

    _fig.tight_layout()

    mo.vstack([
        mo.md("## 2. Spread Distribution"),
        mo.md(f"Mean={_s['mean']:.1f}bp, Median={_s['median']:.1f}bp, "
               f"P5={_s['p5']:.1f}bp, P95={_s['p95']:.1f}bp"),
        _fig,
        mo.md("### By Regime"),
        mo.ui.table(_spread["by_regime"].to_pandas()),
    ])
    return


# --- Cell 4: Oracle divergence ---
@app.cell
def divergence_analysis(mo, plt, np, l2, compute_oracle_divergence_dynamics):
    _div = compute_oracle_divergence_dynamics(l2)
    _d = _div["distribution"]

    _fig, (_ax1, _ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Time series (subsample for plotting)
    _ts = l2["timestamp"].to_numpy()
    _vals = l2["oracle_div_bp"].to_numpy()
    _step = max(1, len(_ts) // 5000)
    _ax1.plot(_ts[::_step], _vals[::_step], alpha=0.5, linewidth=0.5)
    _ax1.axhline(0, color="red", linestyle="--", alpha=0.5)
    _ax1.set_ylabel("Oracle Divergence (bp)")
    _ax1.set_title("Oracle Divergence Time Series")

    # ACF plot
    _lags = [a[0] for a in _div["acf"]]
    _acf_vals = [a[1] for a in _div["acf"]]
    _ax2.bar(_lags, _acf_vals, width=[l * 0.3 for l in _lags], color="steelblue", alpha=0.8)
    _ax2.axhline(0.5, color="red", linestyle="--", alpha=0.5, label="0.5")
    _ax2.set_xlabel("Lag (seconds)")
    _ax2.set_ylabel("Autocorrelation")
    _ax2.set_title(f"Oracle Div ACF (half-life ≈ {_div['half_life_seconds']}s)")
    _ax2.set_xscale("log")
    _ax2.legend()

    _fig.tight_layout()

    mo.vstack([
        mo.md("## 3. Oracle Divergence Dynamics"),
        mo.md(f"Mean={_d['mean']:.3f}bp, |Mean|={_d['abs_mean']:.3f}bp, "
               f"Half-life={_div['half_life_seconds']}s"),
        _fig,
    ])
    return


# --- Cell 5: Book shape ---
@app.cell
def book_analysis(mo, plt, np, l2, compute_book_shape):
    _book = compute_book_shape(l2)
    _ss = _book["source_shares"]
    _dbl = _book["depth_by_level"]

    _fig, (_ax1, _ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Source shares stacked bar
    _labels = ["Bid", "Ask"]
    _vamm = [_ss.get("bid_vamm_pct", 0), _ss.get("ask_vamm_pct", 0)]
    _dlob = [_ss.get("bid_dlob_pct", 0), _ss.get("ask_dlob_pct", 0)]
    _x = np.arange(len(_labels))
    _ax1.bar(_x, _vamm, label="vAMM", color="steelblue")
    _ax1.bar(_x, _dlob, bottom=_vamm, label="DLOB", color="coral")
    _ax1.set_xticks(_x)
    _ax1.set_xticklabels(_labels)
    _ax1.set_ylabel("%")
    _ax1.set_title("Source Shares (Best Level)")
    _ax1.legend()

    # Depth profile
    _levels = _dbl["level"].to_numpy()
    _bid_sizes = _dbl["mean_bid_size"].to_numpy()
    _ask_sizes = _dbl["mean_ask_size"].to_numpy()
    _width = 0.35
    _ax2.bar(_levels - _width / 2, _bid_sizes, _width, label="Bid", color="green", alpha=0.7)
    _ax2.bar(_levels + _width / 2, _ask_sizes, _width, label="Ask", color="red", alpha=0.7)
    _ax2.set_xlabel("Level")
    _ax2.set_ylabel("Mean Size (SOL)")
    _ax2.set_title("Depth by Level")
    _ax2.legend()

    _fig.tight_layout()

    mo.vstack([
        mo.md("## 4. Book Shape"),
        mo.md(f"Bid vAMM: {_ss.get('bid_vamm_pct', 0):.1f}%, "
               f"Ask vAMM: {_ss.get('ask_vamm_pct', 0):.1f}%"),
        _fig,
        mo.md("### Depth by Level"),
        mo.ui.table(_dbl.to_pandas()),
    ])
    return


# --- Cell 6: Fill probability ---
@app.cell
def fill_analysis(mo, plt, np, l2, trades, estimate_fill_probability):
    _fill = estimate_fill_probability(l2, trades)

    _fig, (_ax1, _ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # By spread bucket
    _by_spread = _fill["by_spread_bucket"]
    if not _by_spread.is_empty():
        _buckets = _by_spread["spread_bucket"].to_list()
        _rates = _by_spread["fill_rate"].to_numpy()
        _ax1.bar(_buckets, _rates * 100, color="steelblue", alpha=0.8)
        _ax1.set_xlabel("Spread Bucket")
        _ax1.set_ylabel("Fill Rate (%)")
        _ax1.set_title("Fill Rate by Spread")

    # By hour
    _by_hour = _fill["by_hour"]
    if not _by_hour.is_empty():
        _hours = _by_hour["hour"].to_numpy()
        _rates_h = _by_hour["fill_rate"].to_numpy()
        _ax2.bar(_hours, _rates_h * 100, color="coral", alpha=0.8)
        _ax2.set_xlabel("Hour (UTC)")
        _ax2.set_ylabel("Fill Rate (%)")
        _ax2.set_title("Fill Rate by Hour")
        _ax2.set_xticks(range(0, 24, 2))

    _fig.tight_layout()

    mo.vstack([
        mo.md("## 5. Fill Probability"),
        mo.md(f"Overall fill rate (1min window): {_fill['overall_fill_rate']:.1%}"),
        _fig,
    ])
    return


# --- Cell 7: Adverse selection ---
@app.cell
def adverse_analysis(mo, plt, np, l2, trades, measure_adverse_selection):
    _as = measure_adverse_selection(l2, trades)

    _fig, _ax = plt.subplots(figsize=(10, 5))

    _horizons = [h["horizon_s"] for h in _as["by_horizon"]]
    _means = [h["mean_move_bp"] for h in _as["by_horizon"]]
    _stds = [h["std_move_bp"] for h in _as["by_horizon"]]

    _ax.bar(range(len(_horizons)), _means, yerr=_stds, capsize=5,
            color="steelblue", alpha=0.8)
    _ax.set_xticks(range(len(_horizons)))
    _ax.set_xticklabels([f"{h}s" for h in _horizons])
    _ax.set_xlabel("Horizon")
    _ax.set_ylabel("Adverse Selection (bp)")
    _ax.set_title("Post-Trade Oracle Move (Adverse Selection)")
    _ax.axhline(0, color="red", linestyle="--", alpha=0.5)

    _fig.tight_layout()

    mo.vstack([
        mo.md("## 6. Adverse Selection"),
        mo.md("Price move after trade (positive = adverse for MM)"),
        _fig,
        mo.md("### By Hour (60s horizon)"),
        mo.ui.table(_as["by_hour"].to_pandas()) if not _as["by_hour"].is_empty() else mo.md("No data"),
    ])
    return


# --- Cell 8: Parameter recommendations ---
@app.cell
def param_recommendations(
    mo, l2, trades,
    compute_spread_distribution,
    compute_oracle_divergence_dynamics,
    compute_book_shape,
    estimate_fill_probability,
    measure_adverse_selection,
    recommend_parameters,
):
    _spread = compute_spread_distribution(l2)
    _div = compute_oracle_divergence_dynamics(l2)
    _book = compute_book_shape(l2)
    _fill = estimate_fill_probability(l2, trades)
    _adverse = measure_adverse_selection(l2, trades)

    rec = recommend_parameters(_spread, _div, _book, _fill, _adverse)

    _rows = [
        {"Parameter": "half_spread_bps", "Value": f"{rec['half_spread_bps']:.1f}"},
        {"Parameter": "gamma", "Value": f"{rec['gamma']}"},
        {"Parameter": "active_hours", "Value": f"{rec['active_start']}-{rec['active_end']} UTC"},
        {"Parameter": "max_inventory", "Value": f"{rec['max_inventory']}"},
        {"Parameter": "oracle_div_alpha", "Value": f"{rec['oracle_div_alpha']}"},
    ]

    _reasoning = "\n".join(f"- {r}" for r in rec["reasoning"])

    mo.vstack([
        mo.md("## 7. Parameter Recommendations"),
        mo.ui.table(_rows),
        mo.md(f"### Reasoning\n{_reasoning}"),
    ])
    return (rec,)


# --- Cell 9: Backtest results ---
@app.cell
def backtest_results(mo, plt, np, pl, l2, trades, fr, rec):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "systems" / "drift-mm"))
    from l2_backtester import L2PaperTrader, L2MMConfig

    _l2_1m = L2PaperTrader.resample_l2(l2, seconds=60)

    _configs = [
        ("Recommended", L2MMConfig(
            gamma=rec["gamma"],
            inv_limit=rec["max_inventory"],
            active_start=rec["active_start"],
            active_end=rec["active_end"],
            oracle_div_alpha=rec["oracle_div_alpha"],
        )),
        ("Baseline", L2MMConfig(
            gamma=rec["gamma"],
            inv_limit=rec["max_inventory"],
            oracle_div_alpha=0.0,
            adverse_selection_guard_bps=0.0,
        )),
    ]

    _results = {}
    for _name, _cfg in _configs:
        _trader = L2PaperTrader(_cfg)
        _results[_name] = _trader.run(_l2_1m, trades, fr)

    # Equity curves
    _fig, (_ax1, _ax2) = plt.subplots(2, 1, figsize=(14, 8))

    for _name, _m in _results.items():
        _pnl = np.array(_m["pnl_history"])
        _ax1.plot(_pnl, label=f"{_name} (Sharpe={_m['sharpe']:.2f})", alpha=0.8)
    _ax1.set_ylabel("Cumulative PnL ($)")
    _ax1.set_title("Equity Curves")
    _ax1.legend()
    _ax1.grid(True, alpha=0.3)

    # Inventory for recommended
    _rec_m = _results.get("Recommended", list(_results.values())[0])
    _inv = np.array(_rec_m["inventory_history"])
    _ax2.plot(_inv, color="purple", alpha=0.7)
    _ax2.axhline(0, color="red", linestyle="--", alpha=0.3)
    _ax2.set_ylabel("Inventory (SOL)")
    _ax2.set_title("Inventory (Recommended)")
    _ax2.grid(True, alpha=0.3)

    _fig.tight_layout()

    # Summary table
    _summary = []
    for _name, _m in _results.items():
        _summary.append({
            "Config": _name,
            "Sharpe": f"{_m['sharpe']:.2f}",
            "PnL ($)": f"{_m['total_pnl']:.2f}",
            "Max DD ($)": f"{_m['max_dd']:.2f}",
            "Fills": _m["n_fills"],
            "Avg Spread (bp)": f"{_m['avg_spread_bps']:.1f}",
            "FR Earnings ($)": f"{_m['fr_earnings']:.2f}",
        })

    mo.vstack([
        mo.md("## 8. Backtest Results"),
        _fig,
        mo.ui.table(_summary),
    ])
    return


if __name__ == "__main__":
    app.run()
