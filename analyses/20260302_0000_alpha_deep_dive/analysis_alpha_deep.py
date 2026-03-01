import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


# ============================================================
# Cell 1: setup
# ============================================================
@app.cell
def setup():
    from pathlib import Path

    import marimo as mo
    import matplotlib as mpl
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt
    import numpy as np
    import polars as pl
    from scipy import stats as sp_stats

    fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mpl.rcParams["font.family"] = "Noto Sans CJK JP"
    mpl.rcParams["axes.unicode_minus"] = False

    DATA_DIR = Path(__file__).parent.parent.parent / "data"
    TOKENS = ["BTC", "ETH", "SOL", "SUI"]
    COLORS = {"BTC": "tab:orange", "ETH": "tab:blue", "SOL": "tab:purple", "SUI": "tab:cyan"}
    FWD_HOURS = [4, 8, 24]
    TRAIN_END = "2025-09-01"

    return (
        COLORS,
        DATA_DIR,
        FWD_HOURS,
        TOKENS,
        TRAIN_END,
        mo,
        np,
        pl,
        plt,
        sp_stats,
    )


# ============================================================
# Cell 2: data_load — 全4トークン特徴量計算 + FR/OI読み込み
# ============================================================
@app.cell
def data_load(DATA_DIR, FWD_HOURS, TOKENS, TRAIN_END, np, pl):
    def _compute_features(sym, data_dir, fwd_hours, train_end):
        _raw = pl.read_parquet(data_dir / f"binance_{sym.lower()}usdt_1h.parquet").sort("timestamp")
        _range = pl.col("high") - pl.col("low")
        _body = (pl.col("close") - pl.col("open")).abs()
        _upper = pl.col("high") - pl.max_horizontal("open", "close")
        _lower = pl.min_horizontal("open", "close") - pl.col("low")

        _df = _raw.with_columns([
            ((pl.col("close") - pl.col("open")) / pl.col("open")).alias("candle_return"),
            (_body / _range).alias("body_ratio"),
            (_upper / _range).alias("upper_shadow_ratio"),
            (_lower / _range).alias("lower_shadow_ratio"),
            ((pl.col("close") - pl.col("low")) / _range).alias("close_position"),
            (_range / pl.col("open")).alias("range_pct"),
            (pl.col("volume") / pl.col("volume").rolling_mean(24)).alias("vol_ratio_24h"),
            (pl.col("volume") / pl.col("volume").rolling_mean(168)).alias("vol_ratio_7d"),
            pl.when(pl.col("close") > pl.col("open")).then(1).otherwise(-1).alias("candle_dir"),
            (pl.col("volume") * (pl.col("close") - pl.col("open")).sign()).alias("signed_volume"),
            (pl.col("close") / pl.col("close").shift(24) - 1).alias("momentum_24h"),
        ])

        _df = _df.with_columns([
            (pl.col("signed_volume").rolling_sum(8) /
             pl.col("volume").rolling_sum(8)).alias("vol_imbalance_8h"),
            (pl.col("signed_volume").rolling_sum(24) /
             pl.col("volume").rolling_sum(24)).alias("vol_imbalance_24h"),
        ])

        # PV divergence
        _df = _df.with_columns(
            (pl.col("momentum_24h") * -(pl.col("vol_ratio_24h") - 1)).alias("pv_divergence"),
        )

        # 将来リターン
        for _h in fwd_hours:
            _df = _df.with_columns(
                (pl.col("close").shift(-_h) / pl.col("close") - 1).alias(f"fwd_{_h}h"),
            )
        _df = _df.drop_nulls(f"fwd_{fwd_hours[-1]}h")

        # train/test split
        _ts_naive = pl.col("timestamp").dt.replace_time_zone(None)
        _df = _df.with_columns(
            pl.when(_ts_naive < pl.lit(train_end).str.to_datetime())
            .then(pl.lit("train"))
            .otherwise(pl.lit("test"))
            .alias("split"),
        )
        return _df

    ohlcv_features = {sym: _compute_features(sym, DATA_DIR, FWD_HOURS, TRAIN_END) for sym in TOKENS}

    # FR (Binance, 8h間隔) — timestampスキーマを統一してからconcat
    _fr_frames = []
    for _sym in TOKENS:
        _f = pl.read_parquet(DATA_DIR / f"binance_{_sym.lower()}usdt_funding_rate.parquet")
        _f = _f.with_columns(
            pl.col("timestamp").cast(pl.Datetime("ns", "UTC")),
            pl.lit(_sym).alias("symbol_name"),
        )
        _fr_frames.append(_f)
    fr_raw = pl.concat(_fr_frames).sort("timestamp")

    # OI (Bybit, 1年分) — timestampスキーマを統一してからconcat
    _oi_frames = []
    for _sym in TOKENS:
        _f = pl.read_parquet(DATA_DIR / f"bybit_{_sym.lower()}usdt_open_interest.parquet")
        _f = _f.with_columns(
            pl.col("timestamp").cast(pl.Datetime("ns", "UTC")),
            pl.lit(_sym).alias("symbol_name"),
        )
        _oi_frames.append(_f)
    oi_raw = pl.concat(_oi_frames).sort("timestamp")

    return fr_raw, oi_raw, ohlcv_features


# ============================================================
# Cell 3: helpers
# ============================================================
@app.cell
def helpers(np, plt, sp_stats):
    def quintile_analysis_bps(feature, fwd_ret, split_mask=None):
        """5分位分析。戻り値は各分位の平均リターン(bps)のdict。"""
        _mask = np.isfinite(feature) & np.isfinite(fwd_ret)
        if split_mask is not None:
            _mask = _mask & split_mask
        _f, _r = feature[_mask], fwd_ret[_mask]
        if len(_f) < 50:
            return None
        _q = np.percentile(_f, [20, 40, 60, 80])
        _bins = np.digitize(_f, _q)
        _means = []
        _counts = []
        for _b in range(5):
            _subset = _r[_bins == _b]
            _means.append(np.mean(_subset) * 10000 if len(_subset) > 0 else 0)
            _counts.append(len(_subset))
        _spread = _means[4] - _means[0]
        _monotone = np.corrcoef(range(5), _means)[0, 1] if len(_means) == 5 else 0
        return {
            "Q1": round(_means[0], 2), "Q2": round(_means[1], 2),
            "Q3": round(_means[2], 2), "Q4": round(_means[3], 2),
            "Q5": round(_means[4], 2),
            "Q5-Q1": round(_spread, 2), "単調性r": round(_monotone, 3),
            "N": sum(_counts),
        }

    def compute_equity_curve(positions, returns):
        """positions: +1/-1/0 の配列, returns: 同長の将来リターン配列 → 累積リターン"""
        _mask = np.isfinite(positions) & np.isfinite(returns)
        _pos = positions[_mask]
        _ret = returns[_mask]
        _pnl = _pos * _ret
        _cum = np.cumsum(_pnl)
        return _cum, _pnl

    def sharpe_ratio(pnl, periods_per_year=365 * 3):
        """8h保有 = 1日3回 → 年換算"""
        if len(pnl) == 0 or np.std(pnl) == 0:
            return 0.0
        return np.mean(pnl) / np.std(pnl) * np.sqrt(periods_per_year)

    def max_drawdown(cum_pnl):
        _peak = np.maximum.accumulate(cum_pnl)
        _dd = cum_pnl - _peak
        return np.min(_dd) if len(_dd) > 0 else 0.0

    def win_rate(pnl):
        if len(pnl) == 0:
            return 0.0
        return np.mean(pnl > 0)

    def plot_quintile_bars(ax, quint_dict, title):
        """5分位棒グラフを描画"""
        if quint_dict is None:
            ax.set_title(f"{title} (データ不足)")
            return
        _labels = ["Q1", "Q2", "Q3", "Q4", "Q5"]
        _vals = [quint_dict[q] for q in _labels]
        _colors = ["tab:red" if v < 0 else "tab:green" for v in _vals]
        ax.bar(_labels, _vals, color=_colors, alpha=0.7)
        ax.set_ylabel("平均リターン (bps)")
        ax.set_title(title)
        ax.axhline(0, color="black", linewidth=0.5)
        ax.grid(True, alpha=0.3)
        ax.annotate(f"Q5-Q1={quint_dict['Q5-Q1']:.1f}bps\n単調性r={quint_dict['単調性r']:.3f}",
                    xy=(0.05, 0.95), xycoords="axes fraction", fontsize=8,
                    va="top", bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.5))

    return compute_equity_curve, max_drawdown, plot_quintile_bars, quintile_analysis_bps, sharpe_ratio, win_rate


# ============================================================
# Cell 4: title_cell — タイトル + データ分割サマリー
# ============================================================
@app.cell
def title_cell(TOKENS, TRAIN_END, mo, ohlcv_features, pl):
    _rows = []
    for _sym in TOKENS:
        _df = ohlcv_features[_sym]
        _train = _df.filter(pl.col("split") == "train")
        _test = _df.filter(pl.col("split") == "test")
        _rows.append({
            "通貨": _sym,
            "全データ": _df.height,
            "Train": _train.height,
            "Test": _test.height,
            "Train期間": f"{_train['timestamp'].min()} ~ {_train['timestamp'].max()}",
            "Test期間": f"{_test['timestamp'].min()} ~ {_test['timestamp'].max()}",
        })
    _summary_df = pl.DataFrame(_rows)

    mo.vstack([
        mo.md("# OHLCV アルファ深掘り分析"),
        mo.md(f"""
前回発見のTier-1シグナルをウォークフォワード検証・複合化・エクイティカーブで評価。

**Tier-1シグナル:**
- **Vol Ratio 7d**: 出来高/7日平均比 → 24h後リターン (+46bps)
- **PV Divergence**: 価格-出来高ダイバージェンス → 4-8h後 (-16〜-19bps)
- **Vol Imbalance 8h**: 出来高方向インバランス → 4-8h後 (+20〜30bps, ETH/SUI)

**データ分割:** Train < {TRAIN_END} / Test >= {TRAIN_END}
"""),
        mo.md("### データ概要"),
        mo.ui.table(_summary_df.to_pandas()),
    ])
    return


# ============================================================
# Cell 5: sec_walkforward — ウォークフォワード検証
# ============================================================
@app.cell
def sec_walkforward(TOKENS, COLORS, FWD_HOURS, mo, np, ohlcv_features, pl, plt, plot_quintile_bars, quintile_analysis_bps):
    _signals = [
        ("vol_ratio_7d", "Vol Ratio 7d"),
        ("pv_divergence", "PV Divergence"),
        ("vol_imbalance_8h", "Vol Imbalance 8h"),
    ]
    _wf_results = []

    # 3 signals × 2 panels (train/test) = 3 figures
    _figs = []
    for _sig_col, _sig_label in _signals:
        _fig, _axes = plt.subplots(2, len(TOKENS), figsize=(5 * len(TOKENS), 8))
        _fig.suptitle(f"{_sig_label}: Train vs Test 5分位分析", fontsize=14)
        for _ti, _sym in enumerate(TOKENS):
            _df = ohlcv_features[_sym]
            _feat = _df[_sig_col].to_numpy()
            _train_mask = np.array(_df["split"].to_list()) == "train"
            _test_mask = ~_train_mask

            for _hi, _h in enumerate(FWD_HOURS):
                _fwd = _df[f"fwd_{_h}h"].to_numpy()

                _q_train = quintile_analysis_bps(_feat, _fwd, _train_mask)
                _q_test = quintile_analysis_bps(_feat, _fwd, _test_mask)

                if _q_train:
                    _wf_results.append({
                        "シグナル": _sig_label, "通貨": _sym, "ホライズン": f"{_h}h",
                        "期間": "Train", **{f"Q{i+1}": _q_train[f"Q{i+1}"] for i in range(5)},
                        "Q5-Q1(bps)": _q_train["Q5-Q1"], "単調性r": _q_train["単調性r"],
                    })
                if _q_test:
                    _wf_results.append({
                        "シグナル": _sig_label, "通貨": _sym, "ホライズン": f"{_h}h",
                        "期間": "Test", **{f"Q{i+1}": _q_test[f"Q{i+1}"] for i in range(5)},
                        "Q5-Q1(bps)": _q_test["Q5-Q1"], "単調性r": _q_test["単調性r"],
                    })

            # 8h後の5分位を可視化 (train / test)
            _fwd_8h = _df[f"fwd_8h"].to_numpy()
            _q_tr = quintile_analysis_bps(_feat, _fwd_8h, _train_mask)
            _q_te = quintile_analysis_bps(_feat, _fwd_8h, _test_mask)
            plot_quintile_bars(_axes[0, _ti], _q_tr, f"{_sym} Train (8h後)")
            plot_quintile_bars(_axes[1, _ti], _q_te, f"{_sym} Test (8h後)")

        _fig.tight_layout()
        _figs.append(_fig)

    wf_results_df = pl.DataFrame(_wf_results).sort(["シグナル", "通貨", "ホライズン", "期間"]) if _wf_results else pl.DataFrame()

    _elements = [
        mo.md("## 1. ウォークフォワード検証"),
        mo.md("Train期間で見つけたシグナルがTest期間でも機能するか。5分位の形状・Q5-Q1スプレッドの維持を確認。"),
    ]
    for _f in _figs:
        _elements.append(_f)
    _elements.extend([
        mo.md("### 全結果テーブル"),
        mo.ui.table(wf_results_df.to_pandas()) if not wf_results_df.is_empty() else mo.md("結果なし"),
    ])

    mo.vstack(_elements)
    return (wf_results_df,)


# ============================================================
# Cell 6: sec_composite — 複合シグナル
# ============================================================
@app.cell
def sec_composite(TOKENS, COLORS, FWD_HOURS, mo, np, ohlcv_features, pl, plt, plot_quintile_bars, quintile_analysis_bps):
    def _zscore(arr):
        _mask = np.isfinite(arr)
        _m = np.nanmean(arr)
        _s = np.nanstd(arr)
        if _s == 0:
            return np.zeros_like(arr)
        return (arr - _m) / _s

    _comp_results = []
    _fig, _axes = plt.subplots(len(TOKENS), len(FWD_HOURS),
                                figsize=(5 * len(FWD_HOURS), 4 * len(TOKENS)))

    composite_features = {}
    for _ti, _sym in enumerate(TOKENS):
        _df = ohlcv_features[_sym]
        _z_vol = _zscore(_df["vol_ratio_7d"].to_numpy())
        _z_pv = _zscore(_df["pv_divergence"].to_numpy())
        _z_imb = _zscore(_df["vol_imbalance_8h"].to_numpy())

        # composite = z(vol_ratio_7d) - z(pv_divergence) + z(vol_imbalance_8h)
        _composite = _z_vol - _z_pv + _z_imb
        composite_features[_sym] = _composite

        for _hi, _h in enumerate(FWD_HOURS):
            _fwd = _df[f"fwd_{_h}h"].to_numpy()
            _q = quintile_analysis_bps(_composite, _fwd)
            plot_quintile_bars(_axes[_ti, _hi], _q, f"{_sym} Composite → {_h}h後")
            if _q:
                _comp_results.append({
                    "通貨": _sym, "ホライズン": f"{_h}h",
                    "Q5-Q1(bps)": _q["Q5-Q1"], "単調性r": _q["単調性r"],
                })

    _fig.suptitle("複合シグナル = z(Vol Ratio 7d) − z(PV Divergence) + z(Vol Imbalance 8h)", fontsize=13)
    _fig.tight_layout()

    # 個別 vs 複合の比較テーブル
    _compare_rows = []
    for _sym in TOKENS:
        _df = ohlcv_features[_sym]
        _fwd8 = _df["fwd_8h"].to_numpy()
        for _sig, _label in [("vol_ratio_7d", "Vol Ratio 7d"),
                              ("pv_divergence", "PV Divergence"),
                              ("vol_imbalance_8h", "Vol Imbalance 8h")]:
            _q = quintile_analysis_bps(_df[_sig].to_numpy(), _fwd8)
            if _q:
                _compare_rows.append({"通貨": _sym, "シグナル": _label,
                                      "Q5-Q1(bps)": _q["Q5-Q1"], "単調性r": _q["単調性r"]})
        _q_comp = quintile_analysis_bps(composite_features[_sym], _fwd8)
        if _q_comp:
            _compare_rows.append({"通貨": _sym, "シグナル": "**Composite**",
                                  "Q5-Q1(bps)": _q_comp["Q5-Q1"], "単調性r": _q_comp["単調性r"]})

    _compare_df = pl.DataFrame(_compare_rows) if _compare_rows else pl.DataFrame()

    mo.vstack([
        mo.md("## 2. 複合シグナル構築"),
        mo.md("""
**Composite = z(Vol Ratio 7d) − z(PV Divergence) + z(Vol Imbalance 8h)**

PV Divergenceの符号を反転（負のスプレッド → 正の予測方向に変換）して加算。
個別シグナルより複合の方がQ5-Q1スプレッドが拡大するか検証。
"""),
        _fig,
        mo.md("### 個別 vs 複合比較 (8h後リターン)"),
        mo.ui.table(_compare_df.to_pandas()) if not _compare_df.is_empty() else mo.md("結果なし"),
    ])
    return (composite_features,)


# ============================================================
# Cell 7: sec_equity_curve — エクイティカーブ
# ============================================================
@app.cell
def sec_equity_curve(TOKENS, COLORS, mo, np, ohlcv_features, pl, plt,
                     composite_features, compute_equity_curve, sharpe_ratio, max_drawdown, win_rate):
    _fig, _axes = plt.subplots(2, 2, figsize=(16, 10))
    _axes = _axes.flatten()
    _perf_rows = []

    for _ti, _sym in enumerate(TOKENS):
        _df = ohlcv_features[_sym]
        _composite = composite_features[_sym]
        _fwd_8h = _df["fwd_8h"].to_numpy()
        _splits = np.array(_df["split"].to_list())

        # 5分位ポジション: Q5=long(+1), Q1=short(-1), others=0
        _mask = np.isfinite(_composite) & np.isfinite(_fwd_8h)
        _q = np.percentile(_composite[_mask], [20, 40, 60, 80])
        _bins = np.digitize(_composite, _q)
        _positions = np.zeros(len(_composite))
        _positions[_bins == 4] = 1.0   # Q5 = long
        _positions[_bins == 0] = -1.0  # Q1 = short

        # Train/Test に分けてエクイティカーブ
        for _period, _period_mask in [("train", _splits == "train"), ("test", _splits == "test")]:
            _pos_p = _positions[_period_mask]
            _ret_p = _fwd_8h[_period_mask]
            _cum, _pnl = compute_equity_curve(_pos_p, _ret_p)
            if len(_cum) > 0:
                _perf_rows.append({
                    "通貨": _sym, "期間": _period,
                    "Sharpe": round(sharpe_ratio(_pnl), 2),
                    "MaxDD": round(max_drawdown(_cum) * 100, 2),
                    "勝率": round(win_rate(_pnl) * 100, 1),
                    "取引数": int(np.sum(np.abs(_pos_p) > 0)),
                    "累積リターン(%)": round(_cum[-1] * 100, 2),
                })

        # プロット(全期間)
        _cum_all, _pnl_all = compute_equity_curve(_positions, _fwd_8h)
        if len(_cum_all) > 0:
            _ts = _df["timestamp"].to_list()
            _valid = np.isfinite(_positions) & np.isfinite(_fwd_8h)
            _ts_valid = [t for t, v in zip(_ts, _valid) if v]
            _train_end_idx = sum(1 for s in _splits[_valid] if s == "train")
            _axes[_ti].plot(_ts_valid, _cum_all * 100, color=COLORS[_sym], linewidth=1)
            if _train_end_idx < len(_ts_valid):
                _axes[_ti].axvline(_ts_valid[_train_end_idx], color="red",
                                   linestyle="--", alpha=0.7, label="Train/Test境界")
            _axes[_ti].set_title(f"{_sym}: Q5 Long / Q1 Short (8h保有)")
            _axes[_ti].set_ylabel("累積リターン (%)")
            _axes[_ti].grid(True, alpha=0.3)
            _axes[_ti].legend(fontsize=8)

    _fig.suptitle("Composite シグナル エクイティカーブ", fontsize=14)
    _fig.tight_layout()

    _perf_df = pl.DataFrame(_perf_rows) if _perf_rows else pl.DataFrame()

    mo.vstack([
        mo.md("## 3. エクイティカーブ"),
        mo.md("""
Compositeシグナルの5分位に基づくロング/ショート戦略:
- **Q5 (上位20%)**: ロング (+1)
- **Q1 (下位20%)**: ショート (-1)
- **Q2-Q4**: ノーポジション
- **保有期間**: 8h（次の8hリターンで決済）
- 赤点線: Train/Test境界
"""),
        _fig,
        mo.md("### パフォーマンスサマリー"),
        mo.ui.table(_perf_df.to_pandas()) if not _perf_df.is_empty() else mo.md("結果なし"),
    ])
    return


# ============================================================
# Cell 8: sec_cross_asset — クロスアセット一貫性
# ============================================================
@app.cell
def sec_cross_asset(TOKENS, COLORS, FWD_HOURS, mo, np, ohlcv_features, pl, plt, quintile_analysis_bps):
    _signals = [
        ("vol_ratio_7d", "Vol Ratio 7d"),
        ("pv_divergence", "PV Divergence"),
        ("vol_imbalance_8h", "Vol Imbalance 8h"),
    ]

    _fig, _axes = plt.subplots(len(_signals), len(FWD_HOURS),
                                figsize=(6 * len(FWD_HOURS), 4 * len(_signals)))

    _cross_rows = []
    for _si, (_sig_col, _sig_label) in enumerate(_signals):
        for _hi, _h in enumerate(FWD_HOURS):
            _ax = _axes[_si, _hi]
            _spreads = []
            for _sym in TOKENS:
                _df = ohlcv_features[_sym]
                _q = quintile_analysis_bps(_df[_sig_col].to_numpy(), _df[f"fwd_{_h}h"].to_numpy())
                if _q:
                    _spreads.append(_q["Q5-Q1"])
                    _cross_rows.append({
                        "シグナル": _sig_label, "ホライズン": f"{_h}h",
                        "通貨": _sym, "Q5-Q1(bps)": _q["Q5-Q1"], "単調性r": _q["単調性r"],
                    })
                else:
                    _spreads.append(0)

            _colors = [COLORS[s] for s in TOKENS]
            _bars = _ax.bar(TOKENS, _spreads, color=_colors, alpha=0.7)
            _ax.set_title(f"{_sig_label} → {_h}h後")
            _ax.set_ylabel("Q5-Q1 (bps)")
            _ax.axhline(0, color="black", linewidth=0.5)
            _ax.grid(True, alpha=0.3)

            # 一貫性: 全通貨同符号か
            _signs = [np.sign(s) for s in _spreads if s != 0]
            _consistent = len(set(_signs)) <= 1 if _signs else False
            if _consistent:
                _ax.annotate("✓ 一貫", xy=(0.95, 0.95), xycoords="axes fraction",
                             fontsize=10, ha="right", va="top", color="green",
                             bbox=dict(boxstyle="round", facecolor="lightgreen", alpha=0.5))

    _fig.suptitle("クロスアセット一貫性: シグナル別 Q5-Q1 スプレッド", fontsize=14)
    _fig.tight_layout()

    _cross_df = pl.DataFrame(_cross_rows) if _cross_rows else pl.DataFrame()

    mo.vstack([
        mo.md("## 4. クロスアセット一貫性"),
        mo.md("同じシグナルが4通貨で同方向のスプレッドを示すか。一貫性があれば構造的なアルファの可能性が高い。"),
        _fig,
        mo.ui.table(_cross_df.to_pandas()) if not _cross_df.is_empty() else mo.md("結果なし"),
    ])
    return


# ============================================================
# Cell 9: sec_derivative_combo — FR/OI追加効果
# ============================================================
@app.cell
def sec_derivative_combo(TOKENS, COLORS, mo, np, ohlcv_features, fr_raw, oi_raw, pl, plt,
                          composite_features, plot_quintile_bars, quintile_analysis_bps):
    def _zscore(arr):
        _m = np.nanmean(arr)
        _s = np.nanstd(arr)
        if _s == 0:
            return np.zeros_like(arr)
        return (arr - _m) / _s

    _combo_results = []
    _fig, _axes = plt.subplots(len(TOKENS), 3, figsize=(18, 4 * len(TOKENS)))

    composite_plus = {}
    for _ti, _sym in enumerate(TOKENS):
        _df = ohlcv_features[_sym]
        # timestampをnaive datetime[ns]に統一してjoin_asof
        _df_naive = _df.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_naive"),
        )

        # FR (8h間隔) → join_asof
        _fr = fr_raw.filter(pl.col("symbol_name") == _sym).select(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_naive"),
            pl.col("funding_rate"),
        ).sort("ts_naive")

        _merged = _df_naive.sort("ts_naive").join_asof(
            _fr, on="ts_naive", strategy="backward",
        )

        # OI (Bybit) → join_asof
        _oi = oi_raw.filter(pl.col("symbol_name") == _sym).select(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_naive"),
            pl.col("open_interest"),
        ).sort("ts_naive")

        _merged = _merged.sort("ts_naive").join_asof(
            _oi, on="ts_naive", strategy="backward",
        )

        # OI変化率(4h rolling)
        _merged = _merged.with_columns(
            (pl.col("open_interest").pct_change(4)).alias("oi_change_4h"),
        )

        # シグナル化
        _fr_arr = _merged["funding_rate"].to_numpy().astype(float)
        _oi_arr = _merged["oi_change_4h"].to_numpy().astype(float)
        _z_fr = _zscore(_fr_arr)
        _z_oi = _zscore(_oi_arr)

        _comp_base = composite_features[_sym]
        # composite_plus = composite - z(FR) + z(OI change)
        # FR高い → 過熱 → ショート方向 → マイナス
        # OI増加 → 関心増 → 方向維持
        _comp_plus = _comp_base - _z_fr + _z_oi
        composite_plus[_sym] = _comp_plus

        _fwd_8h = _merged["fwd_8h"].to_numpy()

        # 比較: base vs plus
        _q_base = quintile_analysis_bps(_comp_base, _fwd_8h)
        _q_plus = quintile_analysis_bps(_comp_plus, _fwd_8h)
        _q_fr = quintile_analysis_bps(-_z_fr, _fwd_8h)  # FR反転

        plot_quintile_bars(_axes[_ti, 0], _q_base, f"{_sym} Composite (base)")
        plot_quintile_bars(_axes[_ti, 1], _q_plus, f"{_sym} Composite+ (FR/OI付)")
        plot_quintile_bars(_axes[_ti, 2], _q_fr, f"{_sym} -z(FR) 単体")

        if _q_base and _q_plus:
            _combo_results.append({
                "通貨": _sym,
                "Base Q5-Q1(bps)": _q_base["Q5-Q1"],
                "Plus Q5-Q1(bps)": _q_plus["Q5-Q1"],
                "改善(bps)": round(_q_plus["Q5-Q1"] - _q_base["Q5-Q1"], 2),
                "Base 単調性r": _q_base["単調性r"],
                "Plus 単調性r": _q_plus["単調性r"],
            })

    _fig.suptitle("Composite vs Composite+ (FR/OI追加)", fontsize=14)
    _fig.tight_layout()

    _combo_df = pl.DataFrame(_combo_results) if _combo_results else pl.DataFrame()

    mo.vstack([
        mo.md("## 5. デリバティブデータ追加効果"),
        mo.md("""
**Composite+** = Composite − z(FR) + z(OI変化率4h)

- FR (Funding Rate): 高い → ロング過熱 → 反転シグナル(マイナス方向)
- OI変化率: 増加 → ポジション構築中 → モメンタム維持
- FR/OI はBybit OI + Binance FRを `join_asof` で1h足に結合
"""),
        _fig,
        mo.md("### Base vs Plus 比較"),
        mo.ui.table(_combo_df.to_pandas()) if not _combo_df.is_empty() else mo.md("結果なし"),
    ])
    return (composite_plus,)


# ============================================================
# Cell 10: sec_robustness — シグナル減衰 + ターンオーバー
# ============================================================
@app.cell
def sec_robustness(TOKENS, COLORS, mo, np, ohlcv_features, pl, plt, composite_features, quintile_analysis_bps):
    # --- シグナル減衰: シグナル計算後、N時間遅延させた場合のスプレッド変化 ---
    _delays = [0, 1, 2, 4, 8, 12, 24]
    _decay_results = []

    _fig_decay, _axes_decay = plt.subplots(1, len(TOKENS), figsize=(5 * len(TOKENS), 4))

    for _ti, _sym in enumerate(TOKENS):
        _df = ohlcv_features[_sym]
        _comp = composite_features[_sym]
        _fwd_8h = _df["fwd_8h"].to_numpy()
        _spreads = []

        for _delay in _delays:
            if _delay == 0:
                _comp_delayed = _comp
            else:
                _comp_delayed = np.roll(_comp, _delay)
                _comp_delayed[:_delay] = np.nan

            _q = quintile_analysis_bps(_comp_delayed, _fwd_8h)
            _spread = _q["Q5-Q1"] if _q else 0
            _spreads.append(_spread)
            _decay_results.append({"通貨": _sym, "遅延(h)": _delay, "Q5-Q1(bps)": _spread})

        _axes_decay[_ti].plot(_delays, _spreads, marker="o", color=COLORS[_sym], linewidth=2)
        _axes_decay[_ti].set_title(f"{_sym}: シグナル減衰")
        _axes_decay[_ti].set_xlabel("遅延 (時間)")
        _axes_decay[_ti].set_ylabel("Q5-Q1 (bps)")
        _axes_decay[_ti].axhline(0, color="black", linewidth=0.5)
        _axes_decay[_ti].grid(True, alpha=0.3)
        if _spreads[0] != 0:
            _half = [d for d, s in zip(_delays, _spreads) if abs(s) < abs(_spreads[0]) / 2]
            if _half:
                _axes_decay[_ti].annotate(f"半減期≈{_half[0]}h",
                    xy=(0.6, 0.9), xycoords="axes fraction", fontsize=9,
                    bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.5))

    _fig_decay.suptitle("Composite シグナル減衰カーブ", fontsize=14)
    _fig_decay.tight_layout()

    # --- ターンオーバー: ポジション変更頻度 ---
    _fig_to, _axes_to = plt.subplots(1, len(TOKENS), figsize=(5 * len(TOKENS), 4))
    _to_results = []

    for _ti, _sym in enumerate(TOKENS):
        _comp = composite_features[_sym]
        _mask = np.isfinite(_comp)
        _comp_valid = _comp[_mask]
        _q = np.percentile(_comp_valid, [20, 40, 60, 80])
        _bins = np.digitize(_comp, _q)
        _positions = np.zeros(len(_comp))
        _positions[_bins == 4] = 1.0
        _positions[_bins == 0] = -1.0

        # ポジション変更
        _changes = np.diff(_positions)
        _turnover = np.abs(_changes)
        _daily_turnover = np.mean(_turnover) * 24  # 1時間足 → 日次換算

        # ローリングターンオーバー(168h=7日窓)
        _rolling_to = np.convolve(_turnover, np.ones(168) / 168, mode="valid")

        _axes_to[_ti].plot(_rolling_to, color=COLORS[_sym], linewidth=1, alpha=0.7)
        _axes_to[_ti].set_title(f"{_sym}: ターンオーバー (7日移動平均)")
        _axes_to[_ti].set_ylabel("ポジション変更/h")
        _axes_to[_ti].grid(True, alpha=0.3)
        _axes_to[_ti].annotate(f"日次TO={_daily_turnover:.2f}",
            xy=(0.05, 0.95), xycoords="axes fraction", fontsize=9, va="top",
            bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.5))

        _to_results.append({
            "通貨": _sym,
            "日次ターンオーバー": round(_daily_turnover, 3),
            "ポジション比率(Long%)": round(np.mean(_positions == 1) * 100, 1),
            "ポジション比率(Short%)": round(np.mean(_positions == -1) * 100, 1),
            "ノーポジション%": round(np.mean(_positions == 0) * 100, 1),
        })

    _fig_to.suptitle("ポジション ターンオーバー", fontsize=14)
    _fig_to.tight_layout()

    _decay_df = pl.DataFrame(_decay_results)
    _to_df = pl.DataFrame(_to_results)

    mo.vstack([
        mo.md("## 6. ロバストネス検証"),
        mo.md("""
### シグナル減衰
Compositeシグナルを遅延させた場合、予測力がどの程度残るか。
半減期が短い = 情報の鮮度が重要 = 執行スピードが必須。

### ターンオーバー
ポジション変更の頻度。高すぎると取引コストで利益が消える。
"""),
        _fig_decay,
        mo.md("### 減衰テーブル"),
        mo.ui.table(_decay_df.to_pandas()),
        _fig_to,
        mo.md("### ターンオーバーテーブル"),
        mo.ui.table(_to_df.to_pandas()),
    ])
    return


# ============================================================
# Cell 11: sec_final_summary — まとめ
# ============================================================
@app.cell
def sec_final_summary(TOKENS, mo, ohlcv_features, pl, np,
                      composite_features, wf_results_df,
                      quintile_analysis_bps, sharpe_ratio, compute_equity_curve, max_drawdown, win_rate):
    _summary_rows = []
    for _sym in TOKENS:
        _df = ohlcv_features[_sym]
        _comp = composite_features[_sym]
        _fwd_8h = _df["fwd_8h"].to_numpy()
        _splits = np.array(_df["split"].to_list())

        # 全期間
        _q_all = quintile_analysis_bps(_comp, _fwd_8h)
        # Test期間のみ
        _test_mask = _splits == "test"
        _q_test = quintile_analysis_bps(_comp, _fwd_8h, _test_mask)

        # エクイティ (Test期間)
        _mask_fin = np.isfinite(_comp) & np.isfinite(_fwd_8h)
        _q_thresh = np.percentile(_comp[_mask_fin], [20, 80])
        _bins = np.digitize(_comp, _q_thresh)
        _pos = np.zeros(len(_comp))
        _pos[_bins == 2] = 1.0
        _pos[_bins == 0] = -1.0

        _pos_test = _pos[_test_mask]
        _ret_test = _fwd_8h[_test_mask]
        _cum, _pnl = compute_equity_curve(_pos_test, _ret_test)

        _summary_rows.append({
            "通貨": _sym,
            "全期間 Q5-Q1(bps)": _q_all["Q5-Q1"] if _q_all else 0,
            "Test Q5-Q1(bps)": _q_test["Q5-Q1"] if _q_test else 0,
            "Test Sharpe": round(sharpe_ratio(_pnl), 2) if len(_pnl) > 0 else 0,
            "Test MaxDD(%)": round(max_drawdown(_cum) * 100, 2) if len(_cum) > 0 else 0,
            "Test 勝率(%)": round(win_rate(_pnl) * 100, 1) if len(_pnl) > 0 else 0,
            "Test 累積(%)": round(_cum[-1] * 100, 2) if len(_cum) > 0 else 0,
        })

    _summary_df = pl.DataFrame(_summary_rows)

    # ウォークフォワード安定性
    _wf_stable = ""
    if not wf_results_df.is_empty():
        _train_df = wf_results_df.filter(pl.col("期間") == "Train")
        _test_df = wf_results_df.filter(pl.col("期間") == "Test")
        if not _train_df.is_empty() and not _test_df.is_empty():
            _train_avg = _train_df["Q5-Q1(bps)"].mean()
            _test_avg = _test_df["Q5-Q1(bps)"].mean()
            _ratio = _test_avg / _train_avg if _train_avg != 0 else 0
            _wf_stable = f"- **WF比率** (Test平均/Train平均): {_ratio:.2f} (1.0が理想, >0.5で合格)\n"

    mo.vstack([
        mo.md("## 7. 総合まとめ"),
        mo.md(f"""
### Composite シグナル (8h後リターン) の評価

{_wf_stable}

**判定基準:**
- Q5-Q1 > 20bps: 実用レベルのスプレッド
- Test Sharpe > 0.5: 最低限の収益性
- WF比率 > 0.5: アウトオブサンプルで維持
- 4通貨中3通貨以上で一貫: 構造的アルファ

### 次のステップ
1. Sharpe > 1.0 を狙うならFR/OI複合 or 時間帯フィルタ追加
2. 取引コスト (片道0.04%) を考慮したネットリターンの検証
3. リアルタイムシグナル生成パイプラインの構築
"""),
        mo.md("### 通貨別パフォーマンスサマリー"),
        mo.ui.table(_summary_df.to_pandas()),
    ])
    return


if __name__ == "__main__":
    app.run()
