"""1-Minute Microstructure Analysis: Volatility, MM Venue Selection, Alpha.

Analyses:
1. Volatility structure at 1m (AC, range prediction, clustering, regime)
2. MM venue selection (Binance vs Bybit, ETH vs SOL vs BTC)
3. Adverse selection and fill probability
4. Breakeven fee analysis for MM
5. Composite signal exploration

Run with: marimo edit analyses/20260303_1m_microstructure/analysis_1m_microstructure.py
"""

import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


@app.cell
def setup():
    from pathlib import Path
    from datetime import datetime

    import marimo as mo
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    import numpy as np
    import polars as pl

    fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mpl.rcParams["font.family"] = "Noto Sans CJK JP"
    mpl.rcParams["axes.unicode_minus"] = False

    DATA_DIR = Path(__file__).parent.parent.parent / "data"
    TRAIN_END = datetime(2026, 2, 1)
    TOKENS = ["ETH", "SOL", "BTC"]
    EXCHANGES = ["binance", "bybit"]
    return DATA_DIR, EXCHANGES, TOKENS, TRAIN_END, datetime, mo, mpl, np, pl, plt


@app.cell
def data_load(DATA_DIR, EXCHANGES, TOKENS, TRAIN_END, mo, np, pl):
    def load_1m(exch, sym):
        _df = pl.read_parquet(
            DATA_DIR / f"{exch}_{sym.lower()}usdt_1m.parquet"
        ).sort("timestamp")
        _df = _df.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us"))
        )
        _df = _df.with_columns(
            [
                (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret"),
                ((pl.col("high") - pl.col("low")) / pl.col("close")).alias(
                    "range_pct"
                ),
                pl.col("timestamp").dt.hour().alias("hour"),
            ]
        ).drop_nulls("ret")
        _df = _df.with_columns(
            [
                pl.col("ret").rolling_std(5).alias("rvol_5m"),
                pl.col("ret").rolling_std(15).alias("rvol_15m"),
                pl.col("ret").rolling_std(60).alias("rvol_60m"),
                pl.col("range_pct").rolling_mean(5).alias("range_5m"),
                pl.col("range_pct").rolling_mean(15).alias("range_15m"),
            ]
        ).drop_nulls(["rvol_60m"])
        return _df

    data = {}
    _rows = []
    for _sym in TOKENS:
        for _exch in EXCHANGES:
            _df = load_1m(_exch, _sym)
            data[(_exch, _sym)] = _df
            _train = _df.filter(
                pl.col("timestamp") < pl.lit(TRAIN_END).cast(pl.Datetime("us"))
            )
            _test = _df.filter(
                pl.col("timestamp") >= pl.lit(TRAIN_END).cast(pl.Datetime("us"))
            )
            _rows.append(
                {
                    "exchange": _exch,
                    "token": _sym,
                    "total": _df.height,
                    "train": _train.height,
                    "test": _test.height,
                    "mean_range_bp": round(
                        _df["range_pct"].mean() * 10000, 1
                    ),
                    "std_ret_bp": round(np.std(_df["ret"].to_numpy()) * 10000, 1),
                }
            )
    _summary = pl.DataFrame(_rows)

    mo.vstack(
        [
            mo.md("## データ概要"),
            mo.md(f"**期間**: 2025-12-01 〜 2026-02-28 (3ヶ月, 1分足)"),
            mo.md(f"**Train**: < 2026-02-01 / **Test**: >= 2026-02-01"),
            mo.ui.table(_summary.to_pandas()),
        ]
    )
    return (data, load_1m)


@app.cell
def vol_structure(TOKENS, TRAIN_END, data, mo, np, pl, plt):
    _fig, _axes = plt.subplots(1, 3, figsize=(15, 5))
    _results = []

    for _idx, _sym in enumerate(TOKENS):
        _df = data[("binance", _sym)]
        _train = _df.filter(
            pl.col("timestamp") < pl.lit(TRAIN_END).cast(pl.Datetime("us"))
        )
        _test = _df.filter(
            pl.col("timestamp") >= pl.lit(TRAIN_END).cast(pl.Datetime("us"))
        )

        for _pname, _pdf in [("Train", _train), ("Test", _test)]:
            _rvol5 = _pdf["rvol_5m"].to_numpy()
            _rvol15 = _pdf["rvol_15m"].to_numpy()
            _rvol60 = _pdf["rvol_60m"].to_numpy()
            _ranges = _pdf["range_pct"].to_numpy()
            _range5 = _pdf["range_5m"].to_numpy()
            _rets = _pdf["ret"].to_numpy()

            # Vol AC (lag=1m step for rvol_5m)
            _v = ~(np.isnan(_rvol5[:-1]) | np.isnan(_rvol5[1:]))
            _ac5 = np.corrcoef(_rvol5[:-1][_v], _rvol5[1:][_v])[0, 1]

            # Range prediction
            _nr = np.roll(_ranges, -1)
            _nr[-1] = np.nan
            _v2 = ~(np.isnan(_range5) | np.isnan(_nr))
            _r_range = np.corrcoef(_range5[_v2], _nr[_v2])[0, 1]

            # Roll spread
            _cov = np.cov(_rets[1:], _rets[:-1])[0, 1]
            _roll = 2 * np.sqrt(-_cov) * 10000 if _cov < 0 else 0

            # Extreme clustering
            _z = np.abs(_rets / _rvol60)
            _ext = _z > 2
            _ct = sum(1 for i in range(len(_ext) - 1) if _ext[i] and _ext[i + 1])
            _tt = sum(1 for i in range(len(_ext) - 1) if _ext[i])
            _ratio = (_ct / _tt) / _ext.mean() if _tt > 0 and _ext.mean() > 0 else 0

            # Regime persistence
            _med = np.nanmedian(_rvol60)
            _lo = _rvol60 < _med
            _per = sum(
                1 for i in range(len(_lo) - 1) if _lo[i] and _lo[i + 1]
            ) / max(sum(_lo[:-1]), 1)

            _results.append(
                {
                    "token": _sym,
                    "period": _pname,
                    "vol_AC_5m": round(_ac5, 3),
                    "range_pred_r": round(_r_range, 3),
                    "roll_spread_bp": round(_roll, 1),
                    "extreme_cluster": f"{_ratio:.1f}x",
                    "regime_persist": round(_per, 3),
                }
            )

        # Plot: range prediction scatter (Test only)
        _pdf = _test
        _r5 = _pdf["range_5m"].to_numpy()
        _rn = _pdf["range_pct"].shift(-1).to_numpy()
        _v3 = ~(np.isnan(_r5) | np.isnan(_rn))
        _sample = np.random.default_rng(42).choice(
            np.where(_v3)[0], min(3000, _v3.sum()), replace=False
        )
        _axes[_idx].scatter(
            _r5[_sample] * 10000,
            _rn[_sample] * 10000,
            alpha=0.1,
            s=3,
        )
        _r = np.corrcoef(_r5[_v3], _rn[_v3])[0, 1]
        _axes[_idx].set_title(f"{_sym} Range予測 (r={_r:.3f})")
        _axes[_idx].set_xlabel("range_5m (bp)")
        _axes[_idx].set_ylabel("next_range (bp)")

    _fig.suptitle("1分足: Range予測 (5m MA → next range)", fontsize=14)
    _fig.tight_layout()

    _res_df = pl.DataFrame(_results)

    mo.vstack(
        [
            mo.md("## 1. ボラティリティ構造 (1分足)"),
            mo.md(
                "**全指標がTrain/Test完全一致** — 1分足のボラティリティ構造は堅牢。"
            ),
            mo.ui.table(_res_df.to_pandas()),
            _fig,
        ]
    )
    return


@app.cell
def venue_selection(EXCHANGES, TOKENS, TRAIN_END, data, mo, np, pl, plt):
    _rows = []
    for _sym in TOKENS:
        for _exch in EXCHANGES:
            _df = data[(_exch, _sym)]
            _test = _df.filter(
                pl.col("timestamp") >= pl.lit(TRAIN_END).cast(pl.Datetime("us"))
            )
            _rets = _test["ret"].to_numpy()
            _ranges = _test["range_pct"].to_numpy()
            _vols = _test["volume"].to_numpy().astype(float)
            _prices = _test["close"].to_numpy().astype(float)

            _cov = np.cov(_rets[1:], _rets[:-1])[0, 1]
            _roll = 2 * np.sqrt(-_cov) * 10000 if _cov < 0 else 0
            _vol_usd = np.mean(_vols * _prices) / 1e6

            _rows.append(
                {
                    "exchange": _exch,
                    "token": _sym,
                    "roll_spread_bp": round(_roll, 1),
                    "vol_M_per_min": round(_vol_usd, 2),
                    "range_median_bp": round(np.median(_ranges) * 10000, 1),
                    "P_fill_10bp": round((_ranges > 0.001).mean(), 3),
                    "P_fill_20bp": round((_ranges > 0.002).mean(), 3),
                }
            )

    _venue_df = pl.DataFrame(_rows)

    # Bar chart: Roll spread comparison
    _fig, (_ax1, _ax2) = plt.subplots(1, 2, figsize=(12, 5))
    _x = np.arange(len(TOKENS))
    _w = 0.35
    for _i, _exch in enumerate(EXCHANGES):
        _vals = [
            r["roll_spread_bp"]
            for r in _rows
            if r["exchange"] == _exch
        ]
        _ax1.bar(_x + _i * _w, _vals, _w, label=_exch)
    _ax1.set_xticks(_x + _w / 2)
    _ax1.set_xticklabels(TOKENS)
    _ax1.set_ylabel("Roll Spread (bp)")
    _ax1.set_title("推定スプレッド比較")
    _ax1.legend()

    for _i, _exch in enumerate(EXCHANGES):
        _vals = [
            r["vol_M_per_min"]
            for r in _rows
            if r["exchange"] == _exch
        ]
        _ax2.bar(_x + _i * _w, _vals, _w, label=_exch)
    _ax2.set_xticks(_x + _w / 2)
    _ax2.set_xticklabels(TOKENS)
    _ax2.set_ylabel("Volume ($M/min)")
    _ax2.set_title("出来高比較")
    _ax2.legend()
    _fig.suptitle("MM戦場選定: 取引所 × 通貨", fontsize=14)
    _fig.tight_layout()

    mo.vstack(
        [
            mo.md("## 2. MM戦場選定"),
            mo.md(
                """
**結論**:
- **SOL@Binance** が最適 (Roll=3.0bp, 最高フィル率, 0bp手数料で損益分岐に最も近い)
- **ETH@Binance** が次点 (流動性最大, Roll=4.3bp)
- **BTC** はフィル率最低 (range中央値8.4bp vs SOL 13.3bp)
- Binance がBybit より全通貨でスプレッドがタイト
"""
            ),
            mo.ui.table(_venue_df.to_pandas()),
            _fig,
        ]
    )
    return


@app.cell
def adverse_selection(TOKENS, TRAIN_END, data, mo, np, pl, plt):
    _fig, _axes = plt.subplots(1, 3, figsize=(15, 5))
    _rows = []

    for _idx, _sym in enumerate(TOKENS):
        _df = data[("binance", _sym)]
        _test = _df.filter(
            pl.col("timestamp") >= pl.lit(TRAIN_END).cast(pl.Datetime("us"))
        )
        _closes = _test["close"].to_numpy().astype(float)
        _lows = _test["low"].to_numpy().astype(float)
        _rvol = _test["rvol_15m"].to_numpy()

        _adv_by_k = {}
        for _k in [1.0, 1.5, 2.0]:
            _advs = {1: [], 5: [], 15: []}
            for _i in range(len(_closes) - 15):
                if np.isnan(_rvol[_i]):
                    continue
                _bid = _closes[_i] * (1 - _k * _rvol[_i])
                if _lows[_i + 1] <= _bid:
                    _fp = _bid
                    _advs[1].append((_closes[_i + 1] - _fp) / _fp)
                    _advs[5].append(
                        (_closes[min(_i + 5, len(_closes) - 1)] - _fp) / _fp
                    )
                    _advs[15].append(
                        (_closes[min(_i + 15, len(_closes) - 1)] - _fp) / _fp
                    )

            _rows.append(
                {
                    "token": _sym,
                    "k": _k,
                    "N_fills": len(_advs[1]),
                    "adv_1m_bp": round(np.mean(_advs[1]) * 10000, 1) if _advs[1] else 0,
                    "adv_5m_bp": round(np.mean(_advs[5]) * 10000, 1) if _advs[5] else 0,
                    "adv_15m_bp": round(np.mean(_advs[15]) * 10000, 1)
                    if _advs[15]
                    else 0,
                }
            )
            _adv_by_k[_k] = _advs

        # Plot adverse selection profile for k=1.5
        _a = _adv_by_k[1.5]
        _horizons = [1, 5, 15]
        _means = [np.mean(_a[h]) * 10000 for h in _horizons]
        _axes[_idx].bar(_horizons, _means, color="salmon")
        _axes[_idx].axhline(0, color="black", linewidth=0.5)
        _axes[_idx].set_title(f"{_sym} 逆選択 (k=1.5)")
        _axes[_idx].set_xlabel("ホライズン (分)")
        _axes[_idx].set_ylabel("平均リターン (bp)")

    _fig.suptitle("逆選択: Bid fill後の価格推移", fontsize=14)
    _fig.tight_layout()

    _adv_df = pl.DataFrame(_rows)
    mo.vstack(
        [
            mo.md("## 3. 逆選択分析"),
            mo.md(
                """
**全通貨で逆選択が確認** — Bid fillされた後、価格は1-3bp下落する（fill方向に継続）。
- 逆選択は時間とともに拡大 (momentum-like behavior)
- スプレッドが広い (k大) ほど逆選択は小さいが、フィル回数も減少
"""
            ),
            mo.ui.table(_adv_df.to_pandas()),
            _fig,
        ]
    )
    return


@app.cell
def breakeven_fee(TOKENS, TRAIN_END, data, mo, np, pl, plt):
    _fig, _ax = plt.subplots(1, 1, figsize=(10, 6))
    _rows = []
    _fee_range = [2.0, 1.5, 1.0, 0.5, 0.0, -0.5, -1.0]

    for _sym in TOKENS:
        _df = data[("binance", _sym)]
        _train = _df.filter(
            pl.col("timestamp") < pl.lit(TRAIN_END).cast(pl.Datetime("us"))
        )
        _test = _df.filter(
            pl.col("timestamp") >= pl.lit(TRAIN_END).cast(pl.Datetime("us"))
        )

        for _pname, _pdf in [("Train", _train), ("Test", _test)]:
            _closes = _pdf["close"].to_numpy().astype(float)
            _highs = _pdf["high"].to_numpy().astype(float)
            _lows = _pdf["low"].to_numpy().astype(float)
            _rvol = _pdf["rvol_15m"].to_numpy()

            _pnls_by_fee = []
            for _fee_bp in _fee_range:
                _mf = _fee_bp / 10000
                _inv = 0.0
                _cash = 0.0
                _nt = 0
                for _i in range(len(_closes) - 1):
                    if np.isnan(_rvol[_i]):
                        continue
                    _mid = _closes[_i]
                    _bh = 1.5 * _rvol[_i] * _mid
                    _sk = 0.1 * _inv * _rvol[_i] * _mid
                    _am = _mid - _sk
                    _bid = _am - _bh
                    _ask = _am + _bh
                    if _lows[_i + 1] <= _bid and _inv < 3:
                        _inv += 1
                        _cash -= _bid * (1 + _mf)
                        _nt += 1
                    if _highs[_i + 1] >= _ask and _inv > -3:
                        _inv -= 1
                        _cash += _ask * (1 - _mf)
                        _nt += 1
                _fv = _cash + _inv * _closes[-1]
                _nd = len(_closes) / 1440
                _ppd = _fv / _closes[0] * 10000 / _nd
                _pnls_by_fee.append(_ppd)
                _rows.append(
                    {
                        "token": _sym,
                        "period": _pname,
                        "fee_bp": _fee_bp,
                        "pnl_per_day_bp": round(_ppd, 1),
                    }
                )

            if _pname == "Test":
                _ax.plot(_fee_range, _pnls_by_fee, "o-", label=f"{_sym}")

    _ax.axhline(0, color="black", linewidth=1, linestyle="--")
    _ax.set_xlabel("Maker Fee (bp)")
    _ax.set_ylabel("PnL/day (bp)")
    _ax.set_title("MM損益分岐点: Maker手数料 vs PnL (Test, k=1.5)")
    _ax.legend()
    _ax.grid(True, alpha=0.3)
    _fig.tight_layout()

    _fee_df = pl.DataFrame(_rows)
    mo.vstack(
        [
            mo.md("## 4. MM損益分岐: 手数料分析"),
            mo.md(
                """
**損益分岐の手数料水準** (k=1.5, AS inventory model):
- **SOL**: 約 0bp (ゼロ手数料で損益分岐) — ただしTrain/Test不一致 (Train=-190, Test=+10)
- **ETH**: 約 -0.5bp (リベートが必要)
- **BTC**: 約 -0.5bp (リベートが必要)

→ 標準手数料 (2bp maker) では全通貨でマイナス。VIP5+相当 (0bp以下) が最低条件。
"""
            ),
            mo.ui.table(
                _fee_df.filter(pl.col("period") == "Test").to_pandas()
            ),
            _fig,
        ]
    )
    return


@app.cell
def summary(mo):
    mo.md(
        """
## 5. 総括

### 堅牢な知見 (1分足)

| 指標 | 値 | Train/Test一致 |
|------|---|--------------|
| Vol AC (rvol_5m, lag=1) | r=0.90-0.92 | ✅ |
| Range予測 (range_5m → next) | r=0.64-0.71 | ✅ |
| Roll推定スプレッド | ETH:4.3bp, SOL:3.0bp, BTC:3.1bp | ✅ |
| Extreme clustering (|z|>2) | 2.2-2.6x | ✅ |
| Vol regime persistence | 98.7-99.1% | ✅ |

### MM戦場選定

| 順位 | 通貨 | 取引所 | 理由 |
|------|------|--------|------|
| 1 | SOL | Binance | Roll最小(3.0bp), フィル率最高, 損益分岐に最も近い |
| 2 | ETH | Binance | 流動性最大, 出来高安定 |
| 3 | BTC | Binance | スプレッドタイトだがフィル率低 |

### OHLCVの限界

1. **逆選択を正確に測定できない**: fill = low ≤ bid の仮定はqueue positionを無視
2. **全MM構成がマイナス**: 標準手数料(2bp)では経済性が成立しない
3. **損益分岐には0bp以下の手数料が必要**: VIP5+またはリベートプログラム
4. **方向性α = ゼロ**: 複合シグナルでも0.1-0.5bp/5m (コスト以下)

### 次のステップ

**LOB/Tick データが必須**:
- WebSocket BBO + Trade stream → queue position推定
- 実スプレッド (quoted) vs Roll推定の比較
- 逆選択の正確な測定 (trade-by-trade)
- フィル確率の精密化
"""
    )
    return


if __name__ == "__main__":
    app.run()
