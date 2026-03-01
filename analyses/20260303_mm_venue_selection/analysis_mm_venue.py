"""Market Making 戦場選定 & Avellaneda-Stoikov モデル分析."""
import marimo

__generated_with = "0.11.0"
app = marimo.App(width="medium")


@app.cell
def setup():
    import sys
    sys.path.insert(0, ".")

    import marimo as mo
    import polars as pl
    import numpy as np
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    from pathlib import Path
    from scipy import stats

    fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mpl.rcParams["font.family"] = "Noto Sans CJK JP"
    mpl.rcParams["figure.figsize"] = (14, 5)

    DATA_DIR = Path("data")
    TOKENS = ["BTC", "ETH", "SOL", "SUI"]
    TAKER_FEE_BPS = 8  # 片道0.04% × 2 = 0.08% round trip
    TRAIN_END = np.datetime64("2025-09-01")

    return DATA_DIR, TOKENS, TAKER_FEE_BPS, TRAIN_END, mo, pl, np, plt, mpl, stats, Path


@app.cell
def load_data(DATA_DIR, TOKENS, pl, np):
    """全トークン・取引所のOHLCVを読み込み、MM関連指標を算出."""

    _exchanges = ["binance", "bybit"]
    panel = {}

    for _ex in _exchanges:
        for _sym in TOKENS:
            _fpath = DATA_DIR / f"{_ex}_{_sym.lower()}usdt_1h.parquet"
            if not _fpath.exists():
                continue
            _df = pl.read_parquet(_fpath).sort("timestamp")
            _df = _df.with_columns([
                pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")),
            ])
            _df = _df.with_columns([
                ((pl.col("high") - pl.col("low")) / pl.col("close") * 10000).alias("range_bps"),
                (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
                (pl.col("close") * pl.col("volume")).alias("volume_usd"),
                pl.col("timestamp").dt.hour().alias("hour"),
            ])
            _df = _df.with_columns([
                pl.col("ret_1h").rolling_std(24).alias("rvol_24h"),
                pl.col("range_bps").rolling_mean(24).alias("range_ma24"),
            ])
            panel[f"{_ex}_{_sym.lower()}usdt"] = _df

    return (panel,)


@app.cell
def venue_metrics(panel, TOKENS, mo, pl, np):
    """取引所×トークン別のMM適性指標."""

    _rows = []
    for _key, _df in sorted(panel.items()):
        # key format: binance_btcusdt, bybit_ethusdt, etc.
        _ex = _key.split("_")[0]
        _sym = _key.split("_")[1].replace("usdt", "").upper()

        _range_arr = _df["range_bps"].drop_nulls().to_numpy()
        _ac1 = np.corrcoef(_range_arr[:-1], _range_arr[1:])[0, 1] if len(_range_arr) > 100 else 0

        _rows.append({
            "Exchange": _ex.capitalize(),
            "Token": _sym,
            "Avg Range (bp)": round(_df["range_bps"].mean(), 1),
            "Med Range (bp)": round(_df["range_bps"].median(), 1),
            "Avg Vol ($M/h)": round(_df["volume_usd"].mean() / 1e6, 1),
            "RVol 24h (bp)": round(_df.drop_nulls("rvol_24h")["rvol_24h"].mean() * 10000, 1),
            "Range AC(1)": round(_ac1, 3),
        })

    _metrics_df = pl.DataFrame(_rows)

    mo.md("""## 1. 取引所×トークン別 MM適性指標

- **Range**: 1h足のHigh-Low幅 (スプレッド上限の代理変数)
- **Vol ($M)**: 1時間あたり平均出来高 (約定機会)
- **RVol**: 24h実現ボラティリティ (在庫リスク)
- **AC(1)**: Range自己相関 (スプレッド予測可能性)
""")
    mo.ui.table(_metrics_df.to_pandas())
    return (_metrics_df,)


@app.cell
def vol_range_analysis(panel, TOKENS, mo, plt, np, pl):
    """ボラティリティとレンジの関係 — MMスプレッド設定の根拠."""

    _fig, _axes = plt.subplots(1, 4, figsize=(16, 4))
    _results = []

    for _i, _sym in enumerate(TOKENS):
        _key = f"binance_{_sym.lower()}usdt"
        if _key not in panel:
            continue
        _df = panel[_key].drop_nulls("rvol_24h")
        _df = _df.with_columns(pl.col("range_bps").shift(-1).alias("next_range")).drop_nulls("next_range")

        _vol = _df["rvol_24h"].to_numpy()
        _nxt = _df["next_range"].to_numpy()
        _r = np.corrcoef(_vol, _nxt)[0, 1]

        _axes[_i].scatter(_vol * 10000, _nxt, alpha=0.05, s=1)
        _axes[_i].set_title(f"{_sym} (r={_r:.3f})")
        _axes[_i].set_xlabel("RVol 24h (bps)")
        _axes[_i].set_ylabel("Next Hour Range (bps)")

        _results.append({"token": _sym, "corr": _r})

    _fig.suptitle("ボラティリティ → 次時間足レンジの予測力", fontsize=14)
    plt.tight_layout()

    mo.md("""## 2. ボラティリティ → レンジ予測

rvol_24h で次の1時間のH-L幅を予測できるか。
MMではこの予測力でスプレッド幅を動的に設定する。
""")
    _fig
    return


@app.cell
def hourly_pattern(panel, TOKENS, mo, plt, pl):
    """時間帯別スプレッドパターン."""

    _fig, _axes = plt.subplots(1, 4, figsize=(16, 4), sharey=False)
    for _i, _sym in enumerate(TOKENS):
        _df = panel[f"binance_{_sym.lower()}usdt"]
        _hourly = _df.group_by("hour").agg([
            pl.col("range_bps").mean().alias("avg_range"),
            pl.col("volume_usd").mean().alias("avg_vol"),
        ]).sort("hour")

        _ax = _axes[_i]
        _ax.bar(_hourly["hour"].to_list(), _hourly["avg_range"].to_list(), alpha=0.7)
        _ax.set_title(f"{_sym}")
        _ax.set_xlabel("Hour (UTC)")
        _ax.set_ylabel("Avg Range (bps)")
        _ax.axhline(y=_df["range_bps"].median(), color="red", linestyle="--", alpha=0.5, label="Median")

    _fig.suptitle("時間帯別スプレッド幅 (14-16h UTC = 米国セッション開始で最大)", fontsize=14)
    plt.tight_layout()
    _fig
    return


@app.cell
def mm_simulation(panel, TOKENS, TAKER_FEE_BPS, TRAIN_END, mo, pl, np, plt):
    """簡易Market Making シミュレーション.

    Strategy:
    - 毎時、close ± half_spread で指値を配置
    - half_spread = α × rvol_24h × sqrt(1/24) × 10000 (ボラスケール)
    - 次時間のH-L幅がspreadを超えたら両サイド約定 → スプレッド獲得
    - 片方のみ約定 → 在庫累積
    """

    results_all = {}

    for _sym in TOKENS:
        _df = panel[f"binance_{_sym.lower()}usdt"].drop_nulls("rvol_24h")

        _close = _df["close"].to_numpy()
        _high = _df["high"].to_numpy()
        _low = _df["low"].to_numpy()
        _vol = _df["rvol_24h"].to_numpy()
        _ts = _df["timestamp"].to_numpy()
        _n = len(_close)

        _results = {}
        for _alpha_label, _alpha in [("Fixed 50bp", None), ("Vol×1.0", 1.0), ("Vol×1.5", 1.5), ("Vol×2.0", 2.0)]:
            _inventory = 0.0
            _cash = 0.0
            _pnl_series = []
            _inv_series = []
            _spread_series = []
            _fills_both = 0
            _fills_one = 0

            for _t in range(_n - 1):
                # Set spread
                if _alpha is None:
                    _half_spread_pct = 50 / 10000 / 2  # fixed 50bp total → 25bp each side
                else:
                    _predicted_vol = _vol[_t] * np.sqrt(1 / 24)  # 1h vol from 24h
                    _half_spread_pct = _alpha * _predicted_vol

                _mid = _close[_t]
                _bid = _mid * (1 - _half_spread_pct)
                _ask = _mid * (1 + _half_spread_pct)
                _spread_bps = (_ask - _bid) / _mid * 10000

                _next_low = _low[_t + 1]
                _next_high = _high[_t + 1]

                _bid_filled = _next_low <= _bid
                _ask_filled = _next_high >= _ask

                if _bid_filled and _ask_filled:
                    # Both filled: buy at bid, sell at ask → net zero inventory change, capture spread
                    _cash += (_ask - _bid)  # spread profit
                    _fills_both += 1
                elif _bid_filled:
                    # Only bid filled: bought, inventory increases
                    _inventory += 1
                    _cash -= _bid
                    _fills_one += 1
                elif _ask_filled:
                    # Only ask filled: sold, inventory decreases
                    _inventory -= 1
                    _cash += _ask
                    _fills_one += 1

                # Mark-to-market PnL
                _mtm = _cash + _inventory * _close[_t + 1]
                _pnl_series.append(_mtm)
                _inv_series.append(_inventory)
                _spread_series.append(_spread_bps)

            _pnl_arr = np.array(_pnl_series)
            # Normalize to bps of initial price
            _pnl_bps = _pnl_arr / _close[0] * 10000

            _results[_alpha_label] = {
                "pnl_bps": _pnl_bps,
                "inventory": np.array(_inv_series),
                "spreads": np.array(_spread_series),
                "fills_both": _fills_both,
                "fills_one": _fills_one,
                "total_hours": _n - 1,
            }

        results_all[_sym] = _results

    # Summary table
    _rows = []
    for _sym in TOKENS:
        for _label, _r in results_all[_sym].items():
            _pnl = _r["pnl_bps"]
            _inv = _r["inventory"]
            _final = _pnl[-1]
            _max_dd = np.min(_pnl - np.maximum.accumulate(_pnl))
            _sharpe = (np.mean(np.diff(_pnl)) / np.std(np.diff(_pnl))) * np.sqrt(8760) if np.std(np.diff(_pnl)) > 0 else 0
            _avg_spread = np.mean(_r["spreads"])

            _rows.append({
                "Token": _sym,
                "Strategy": _label,
                "Final PnL (bp)": round(_final, 0),
                "MaxDD (bp)": round(_max_dd, 0),
                "Sharpe (ann)": round(_sharpe, 2),
                "Avg Spread (bp)": round(_avg_spread, 1),
                "Both Fills": _r["fills_both"],
                "One Fill": _r["fills_one"],
                "Max |Inv|": round(np.max(np.abs(_inv)), 0),
            })

    _summary = pl.DataFrame(_rows)

    # Plot equity curves
    _fig, _axes = plt.subplots(2, 4, figsize=(18, 8))
    for _i, _sym in enumerate(TOKENS):
        _ax_pnl = _axes[0, _i]
        _ax_inv = _axes[1, _i]
        for _label, _r in results_all[_sym].items():
            _ax_pnl.plot(_r["pnl_bps"], label=_label, alpha=0.7)
            _ax_inv.plot(_r["inventory"], label=_label, alpha=0.5)
        _ax_pnl.set_title(f"{_sym} PnL")
        _ax_pnl.legend(fontsize=7)
        _ax_pnl.set_ylabel("Cum PnL (bps)")
        _ax_inv.set_title(f"{_sym} Inventory")
        _ax_inv.set_ylabel("Position (units)")
        _ax_inv.set_xlabel("Hours")

    _fig.suptitle("MM Simulation: Fixed vs Vol-Adaptive Spread", fontsize=14)
    plt.tight_layout()

    mo.md("""## 3. 簡易MM シミュレーション

**戦略**: 毎時 close ± half_spread で指値配置
- Fixed: 50bp固定スプレッド
- Vol×α: rvol_24h に比例したスプレッド (α=1.0, 1.5, 2.0)

**約定ルール**: 次時間のH/Lが指値に到達したら約定
""")
    mo.ui.table(_summary.to_pandas())
    _fig

    return (results_all,)


@app.cell
def cost_analysis(mo, results_all, TOKENS, TAKER_FEE_BPS, np, plt):
    """取引コスト込みのP&L分析."""

    # Maker fee is typically 0.01-0.02%, vs taker 0.04%
    # MM uses limit orders → maker fee applies
    MAKER_FEE_BPS = 2  # 0.01% per side × 2 = 0.02% round trip = 2bp

    _rows = []
    for _sym in TOKENS:
        for _label, _r in results_all[_sym].items():
            _total_fills = _r["fills_both"] * 2 + _r["fills_one"]  # each fill = one side
            _total_cost_bps = _total_fills * MAKER_FEE_BPS  # maker cost per fill
            _gross = _r["pnl_bps"][-1]
            _net = _gross - _total_cost_bps
            _rows.append({
                "Token": _sym,
                "Strategy": _label,
                "Gross PnL": round(_gross, 0),
                "Total Fills": _total_fills,
                "Cost (bp)": round(_total_cost_bps, 0),
                "Net PnL (bp)": round(_net, 0),
            })

    mo.md(f"""## 4. 取引コスト分析

Maker fee = {MAKER_FEE_BPS}bp/fill (指値注文想定)
""")
    import polars as _pl
    mo.ui.table(_pl.DataFrame(_rows).to_pandas())
    return


@app.cell
def train_test_split(mo, results_all, TOKENS, TRAIN_END, np, panel, plt):
    """Train/Test期間別の検証."""

    _fig, _axes = plt.subplots(1, 4, figsize=(16, 4))
    _rows = []

    for _i, _sym in enumerate(TOKENS):
        _df = panel[f"binance_{_sym.lower()}usdt"]
        _ts = _df["timestamp"].to_numpy()
        _train_mask = _ts[:-1] < TRAIN_END  # exclude last row used for next-bar
        _test_mask = ~_train_mask

        for _label, _r in results_all[_sym].items():
            _pnl_diff = np.diff(np.concatenate([[0], _r["pnl_bps"]]))

            for _period, _mask in [("Train", _train_mask[:len(_pnl_diff)]), ("Test", _test_mask[:len(_pnl_diff)])]:
                _p = _pnl_diff[_mask]
                if len(_p) < 100:
                    continue
                _sharpe = (np.mean(_p) / np.std(_p)) * np.sqrt(8760) if np.std(_p) > 0 else 0
                _cum = np.sum(_p)
                _rows.append({
                    "Token": _sym, "Strategy": _label, "Period": _period,
                    "Cum PnL (bp)": round(_cum, 0),
                    "Sharpe (ann)": round(_sharpe, 2),
                })

        # Plot test period equity
        _best = "Vol×1.5"
        _pnl = results_all[_sym][_best]["pnl_bps"]
        _test_start = np.sum(_train_mask[:len(_pnl)])
        if _test_start < len(_pnl):
            _test_pnl = _pnl[_test_start:] - _pnl[_test_start]
            _axes[_i].plot(_test_pnl, label=_best)
            _axes[_i].set_title(f"{_sym} Test PnL ({_best})")
            _axes[_i].set_ylabel("PnL (bps)")

    _fig.suptitle("Test Period Equity Curves (Vol×1.5 strategy)", fontsize=14)
    plt.tight_layout()

    import polars as _pl
    mo.md("## 5. Train/Test 分割検証")
    mo.ui.table(_pl.DataFrame(_rows).to_pandas())
    _fig
    return


@app.cell
def summary(mo):
    mo.md("""## 6. 戦場選定サマリー

### MM適性ランキング

| 順位 | トークン | 理由 |
|------|---------|------|
| 1 | **ETH** | スプレッド十分 (86bp), 高流動性 ($654M/h), 予測性良好 (AC=0.49) |
| 2 | **SOL** | 最広スプレッド/流動性バランス (100bp, $164M/h) |
| 3 | **BTC** | 最高予測性 (AC=0.58) だが狭スプレッド (49bp) |
| 4 | **SUI** | 最広スプレッド (125bp) だが低流動性 ($28M/h), 在庫リスク大 |

### ボラティリティ予測の優位性

- rvol_24h → 次時間レンジの相関: **r=0.26-0.46**
- vol-adaptive spread は fixed spread を Sharpe で改善
- 高ボラ時にスプレッド拡大 → リスク調整後リターン向上

### 課題

- 1h OHLCVベースのシミュレーションは楽観的（実際のスリッページ、queue priority未考慮）
- Maker fee 前提だがqueue priority 確保が必要
- 在庫リスク管理（max position limit）が重要
""")
    return


if __name__ == "__main__":
    app.run()
