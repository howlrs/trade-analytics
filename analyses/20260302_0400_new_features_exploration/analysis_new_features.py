import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


# ============================================================
# Cell 1: setup — imports, 定数, フォント設定
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
    TRAIN_END = "2025-09-01"

    return (
        COLORS,
        DATA_DIR,
        TOKENS,
        TRAIN_END,
        mo,
        np,
        pl,
        plt,
        sp_stats,
    )


# ============================================================
# Cell 2: data_load — 全データ読み込み + フィーチャー算出
# ============================================================
@app.cell
def data_load(DATA_DIR, TOKENS, TRAIN_END, np, pl):
    def _norm_ts(df):
        """datetime[ms]等をdatetime[ns, UTC]に統一"""
        _ts = df.get_column("timestamp")
        if _ts.dtype != pl.Datetime("ns", "UTC"):
            df = df.with_columns(
                pl.col("timestamp")
                .cast(pl.Datetime("ns"))
                .dt.replace_time_zone("UTC")
            )
        return df

    def _to_naive(df):
        """Remove timezone and cast to datetime[ns] for consistent joins."""
        return df.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns"))
        )

    # --- Fear & Greed Index (daily) ---
    fg_raw = pl.read_parquet(DATA_DIR / "fear_greed_index.parquet")
    fg_raw = fg_raw.with_columns(
        pl.col("value").cast(pl.Float64),
        pl.col("value").rolling_mean(7).alias("fg_ma7"),
    ).with_columns(
        (pl.col("value") - pl.col("value").shift(7)).alias("fg_change_7d"),
    ).sort("timestamp")

    # --- Stablecoin data (daily) ---
    sc_mcap = pl.read_parquet(DATA_DIR / "defillama_stablecoin_mcap.parquet")
    sc_mcap = sc_mcap.with_columns(
        pl.col("total_mcap_usd").cast(pl.Float64),
    ).with_columns(
        (pl.col("total_mcap_usd") / pl.col("total_mcap_usd").shift(7) - 1).alias("sc_mcap_chg_7d"),
    ).sort("timestamp")

    usdt_mcap = pl.read_parquet(DATA_DIR / "defillama_usdt_mcap.parquet")
    usdt_mcap = usdt_mcap.with_columns(
        pl.col("circulating_usd").cast(pl.Float64),
    ).with_columns(
        (pl.col("circulating_usd") / pl.col("circulating_usd").shift(7) - 1).alias("usdt_chg_7d"),
    ).sort("timestamp")

    usdc_mcap = pl.read_parquet(DATA_DIR / "defillama_usdc_mcap.parquet")
    usdc_mcap = usdc_mcap.with_columns(
        pl.col("circulating_usd").cast(pl.Float64),
    ).with_columns(
        (pl.col("circulating_usd") / pl.col("circulating_usd").shift(7) - 1).alias("usdc_chg_7d"),
    ).sort("timestamp")

    # --- Basis 1h (per token) ---
    basis_data = {}
    for _sym in TOKENS:
        _basis = _norm_ts(
            pl.read_parquet(DATA_DIR / f"binance_{_sym.lower()}usdt_basis_1h.parquet")
        ).sort("timestamp")
        _basis = _basis.with_columns(
            pl.col("basis_rate").rolling_mean(24).alias("basis_ma_24h"),
            pl.col("basis_rate").rolling_std(24).alias("basis_std_24h"),
        ).with_columns(
            (
                (pl.col("basis_rate") - pl.col("basis_ma_24h")) / pl.col("basis_std_24h")
            ).alias("basis_zscore_24h"),
            (pl.col("basis_rate") - pl.col("basis_ma_24h")).alias("basis_ma_diff"),
        )
        basis_data[_sym] = _basis

    # --- OHLCV 1h (for return computation) ---
    ohlcv_data = {}
    for _sym in TOKENS:
        _ohlcv = _norm_ts(
            pl.read_parquet(DATA_DIR / f"binance_{_sym.lower()}usdt_1h.parquet")
        ).sort("timestamp")
        ohlcv_data[_sym] = _ohlcv

    # --- Merged hourly data (basis + OHLCV + daily features via join_asof) ---
    merged_data = {}
    for _sym in TOKENS:
        _basis = _to_naive(basis_data[_sym]).sort("timestamp")
        _ohlcv = _to_naive(ohlcv_data[_sym]).select(
            ["timestamp", "close", "volume"]
        ).sort("timestamp")

        _m = _basis.join(_ohlcv, on="timestamp", how="inner").sort("timestamp")

        # Forward returns
        for _h in [1, 8, 24]:
            _m = _m.with_columns(
                (pl.col("close").shift(-_h) / pl.col("close") - 1).alias(f"fwd_{_h}h"),
            )

        # Join daily F&G
        _fg_naive = _to_naive(fg_raw).select(
            ["timestamp", "value", "fg_change_7d"]
        ).rename({"value": "fg_value"}).sort("timestamp")
        _m = _m.join_asof(_fg_naive, on="timestamp", strategy="backward")

        # Join daily stablecoin
        _sc_naive = _to_naive(sc_mcap).select(
            ["timestamp", "sc_mcap_chg_7d"]
        ).sort("timestamp")
        _m = _m.join_asof(_sc_naive, on="timestamp", strategy="backward")

        _usdt_naive = _to_naive(usdt_mcap).select(
            ["timestamp", "usdt_chg_7d"]
        ).sort("timestamp")
        _m = _m.join_asof(_usdt_naive, on="timestamp", strategy="backward")

        _usdc_naive = _to_naive(usdc_mcap).select(
            ["timestamp", "usdc_chg_7d"]
        ).sort("timestamp")
        _m = _m.join_asof(_usdc_naive, on="timestamp", strategy="backward")

        # Train/Test split
        _m = _m.with_columns(
            pl.when(pl.col("timestamp") < pl.lit(TRAIN_END).str.to_datetime())
            .then(pl.lit("train"))
            .otherwise(pl.lit("test"))
            .alias("split"),
        )

        merged_data[_sym] = _m

    return basis_data, fg_raw, merged_data, ohlcv_data, sc_mcap, usdc_mcap, usdt_mcap


# ============================================================
# Cell 3: helpers — 分析ヘルパー関数
# ============================================================
@app.cell
def helpers(np, pl, plt, sp_stats):
    def quantile_analysis(df, feature_col, fwd_col, n_quantiles=5, split=None):
        """五分位分析: 特徴量のquantileごとに将来リターンを集計"""
        _df = df.drop_nulls(subset=[feature_col, fwd_col])
        if split is not None:
            _df = _df.filter(pl.col("split") == split)
        if _df.height < n_quantiles * 5:
            return pl.DataFrame()
        _df = _df.with_columns(
            pl.col(feature_col).qcut(n_quantiles, labels=[str(i+1) for i in range(n_quantiles)]).alias("quantile"),
        )
        _result = _df.group_by("quantile").agg([
            pl.col(fwd_col).mean().alias("mean_ret"),
            pl.col(fwd_col).std().alias("std_ret"),
            pl.col(fwd_col).count().alias("N"),
            pl.col(feature_col).mean().alias("feature_mean"),
        ]).sort("quantile")
        return _result

    def event_stats(rets):
        """イベントリターンの統計"""
        if len(rets) < 3:
            return {"N": len(rets), "mean_bps": 0, "t": 0, "p": 1.0, "win": 0}
        _t, _p = sp_stats.ttest_1samp(rets, 0)
        return {
            "N": len(rets),
            "mean_bps": round(float(np.mean(rets)) * 10000, 2),
            "t": round(float(_t), 2),
            "p": round(float(_p), 4),
            "win": round(float(np.mean(rets > 0)), 3),
        }

    def plot_quantile_bar(ax, q_df, title, ylabel="平均リターン (bps)"):
        """五分位棒グラフ"""
        if q_df.is_empty():
            ax.set_title(f"{title}\n(データ不足)")
            return
        _labels = q_df.get_column("quantile").to_list()
        _means = (q_df.get_column("mean_ret") * 10000).to_list()
        _colors = ["tab:green" if m > 0 else "tab:red" for m in _means]
        _bars = ax.bar(_labels, _means, color=_colors, alpha=0.7)
        ax.axhline(0, color="black", linewidth=0.5)
        ax.set_xlabel("Quantile (1=Low, 5=High)")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        for _bar, _n in zip(_bars, q_df.get_column("N").to_list()):
            ax.annotate(
                f"N={_n}",
                xy=(_bar.get_x() + _bar.get_width() / 2, _bar.get_height()),
                ha="center", va="bottom" if _bar.get_height() >= 0 else "top",
                fontsize=7,
            )

    def monotonicity_score(q_df):
        """五分位のmean_retが単調増加/減少かを-1〜1で評価"""
        if q_df.is_empty() or q_df.height < 3:
            return 0.0
        _means = q_df.get_column("mean_ret").to_numpy()
        _n = len(_means)
        _concordant = 0
        _total = 0
        for _i in range(_n):
            for _j in range(_i + 1, _n):
                _total += 1
                if _means[_j] > _means[_i]:
                    _concordant += 1
                elif _means[_j] < _means[_i]:
                    _concordant -= 1
        return _concordant / _total if _total > 0 else 0.0

    return event_stats, monotonicity_score, plot_quantile_bar, quantile_analysis


# ============================================================
# Cell 4: title — データ概要
# ============================================================
@app.cell
def title_cell(TOKENS, fg_raw, merged_data, mo, pl, sc_mcap, usdc_mcap, usdt_mcap):
    _data_summary = []
    _data_summary.append({
        "データ": "Fear & Greed Index",
        "件数": fg_raw.height,
        "期間開始": str(fg_raw["timestamp"].min()),
        "期間終了": str(fg_raw["timestamp"].max()),
        "頻度": "日次",
    })
    _data_summary.append({
        "データ": "Stablecoin MCap",
        "件数": sc_mcap.height,
        "期間開始": str(sc_mcap["timestamp"].min()),
        "期間終了": str(sc_mcap["timestamp"].max()),
        "頻度": "日次",
    })
    _data_summary.append({
        "データ": "USDT供給",
        "件数": usdt_mcap.height,
        "期間開始": str(usdt_mcap["timestamp"].min()),
        "期間終了": str(usdt_mcap["timestamp"].max()),
        "頻度": "日次",
    })
    _data_summary.append({
        "データ": "USDC供給",
        "件数": usdc_mcap.height,
        "期間開始": str(usdc_mcap["timestamp"].min()),
        "期間終了": str(usdc_mcap["timestamp"].max()),
        "頻度": "日次",
    })
    for _sym in TOKENS:
        _m = merged_data[_sym]
        _data_summary.append({
            "データ": f"Basis+OHLCV {_sym}",
            "件数": _m.height,
            "期間開始": str(_m["timestamp"].min()),
            "期間終了": str(_m["timestamp"].max()),
            "頻度": "1h",
        })

    _summary_df = pl.DataFrame(_data_summary)

    mo.vstack([
        mo.md("# 新規データソース × 次世代フィーチャー探索"),
        mo.md("""
**背景**: OHLCV線形アルファ・デリバティブ主導シグナルともにウォークフォワードで崩壊。
方針転換: 「同じデータの加工方法を変える」→「新しい情報源を投入する」

**3つの新データソース**:
1. **Fear & Greed Index** — 群衆心理の極端値（逆張りシグナル）
2. **Stablecoin供給** — 資金フローの先行指標
3. **先物ベーシス** — 連続的なレバレッジ状態（FRの高解像度版）

- Train: 〜2025-09-01 / Test: 2025-09-01〜
"""),
        mo.md("### データ概要"),
        mo.ui.table(_summary_df.to_pandas()),
    ])


# ============================================================
# Cell 5: sec_fear_greed — Fear & Greed 分析
# ============================================================
@app.cell
def sec_fear_greed(COLORS, TOKENS, event_stats, merged_data, mo, monotonicity_score, np, pl, plot_quantile_bar, plt, quantile_analysis):
    # 五分位分析: fg_value → 将来リターン
    _fig_q, _axes_q = plt.subplots(3, 4, figsize=(18, 12))
    _fwd_cols = ["fwd_1h", "fwd_8h", "fwd_24h"]
    _fwd_labels = ["1h", "8h", "24h"]

    _mono_rows = []
    for _ci, (_fwd, _label) in enumerate(zip(_fwd_cols, _fwd_labels)):
        for _ti, _sym in enumerate(TOKENS):
            for _split in ["train", "test"]:
                _q = quantile_analysis(merged_data[_sym], "fg_value", _fwd, split=_split)
                _ms = monotonicity_score(_q)
                _mono_rows.append({
                    "トークン": _sym, "ホライズン": _label, "Split": _split,
                    "単調性": round(_ms, 3),
                })
            # Plot test
            _q_test = quantile_analysis(merged_data[_sym], "fg_value", _fwd, split="test")
            plot_quantile_bar(_axes_q[_ci, _ti], _q_test, f"{_sym} F&G → {_label} (Test)")
    _fig_q.suptitle("Fear & Greed 五分位 → 将来リターン (Test)", fontsize=14, y=1.02)
    _fig_q.tight_layout()

    # 極端値イベント分析: F&G < 20 (Extreme Fear) → Long
    _extreme_rows = []
    for _sym in TOKENS:
        _m = merged_data[_sym]
        for _split in ["train", "test"]:
            _sub = _m.filter((pl.col("fg_value") < 20) & (pl.col("split") == _split))
            for _fwd, _label in zip(_fwd_cols, _fwd_labels):
                _rets = _sub.get_column(_fwd).drop_nulls().to_numpy()
                _s = event_stats(_rets)
                _extreme_rows.append({
                    "トークン": _sym, "条件": "F&G<20 (Extreme Fear)",
                    "ホライズン": _label, "Split": _split, **_s,
                })

            # F&G > 80 (Extreme Greed) → Short
            _sub_greed = _m.filter((pl.col("fg_value") > 80) & (pl.col("split") == _split))
            for _fwd, _label in zip(_fwd_cols, _fwd_labels):
                _rets = _sub_greed.get_column(_fwd).drop_nulls().to_numpy()
                _s = event_stats(_rets)
                _extreme_rows.append({
                    "トークン": _sym, "条件": "F&G>80 (Extreme Greed)",
                    "ホライズン": _label, "Split": _split, **_s,
                })

    fg_extreme_df = pl.DataFrame(_extreme_rows)
    fg_mono_df = pl.DataFrame(_mono_rows)

    mo.vstack([
        mo.md("## 1. Fear & Greed Index 分析"),
        mo.md("""
**仮説**: F&G極端値は逆張りシグナル。Extreme Fear (<20) でロング、Extreme Greed (>80) でショート。
日次データなのでノイズが少なく、レジーム分類に有用な可能性。
"""),
        _fig_q,
        mo.md("### 五分位 単調性スコア (-1=完全逆相関, +1=完全順相関)"),
        mo.ui.table(fg_mono_df.to_pandas()),
        mo.md("### F&G 極端値イベント統計"),
        mo.ui.table(fg_extreme_df.to_pandas()),
    ])
    return (fg_extreme_df, fg_mono_df)


# ============================================================
# Cell 6: sec_stablecoin — ステーブルコイン供給変化分析
# ============================================================
@app.cell
def sec_stablecoin(COLORS, TOKENS, event_stats, merged_data, mo, monotonicity_score, np, pl, plot_quantile_bar, plt, quantile_analysis):
    # 五分位分析: sc_mcap_chg_7d → 将来リターン
    _fig_q, _axes_q = plt.subplots(3, 4, figsize=(18, 12))
    _fwd_cols = ["fwd_1h", "fwd_8h", "fwd_24h"]
    _fwd_labels = ["1h", "8h", "24h"]

    _mono_rows = []
    for _ci, (_fwd, _label) in enumerate(zip(_fwd_cols, _fwd_labels)):
        for _ti, _sym in enumerate(TOKENS):
            for _split in ["train", "test"]:
                _q = quantile_analysis(merged_data[_sym], "sc_mcap_chg_7d", _fwd, split=_split)
                _ms = monotonicity_score(_q)
                _mono_rows.append({
                    "トークン": _sym, "特徴量": "SC MCap 7d chg", "ホライズン": _label,
                    "Split": _split, "単調性": round(_ms, 3),
                })
            _q_test = quantile_analysis(merged_data[_sym], "sc_mcap_chg_7d", _fwd, split="test")
            plot_quantile_bar(_axes_q[_ci, _ti], _q_test, f"{_sym} SC MCap 7d → {_label} (Test)")
    _fig_q.suptitle("ステーブルコイン MCap 7日変化率 五分位 → 将来リターン (Test)", fontsize=14, y=1.02)
    _fig_q.tight_layout()

    # USDT vs USDC 乖離
    _fig_div, _axes_div = plt.subplots(3, 4, figsize=(18, 12))
    for _ci, (_fwd, _label) in enumerate(zip(_fwd_cols, _fwd_labels)):
        for _ti, _sym in enumerate(TOKENS):
            _m = merged_data[_sym].with_columns(
                (pl.col("usdt_chg_7d") - pl.col("usdc_chg_7d")).alias("usdt_usdc_div"),
            )
            for _split in ["train", "test"]:
                _q = quantile_analysis(_m, "usdt_usdc_div", _fwd, split=_split)
                _ms = monotonicity_score(_q)
                _mono_rows.append({
                    "トークン": _sym, "特徴量": "USDT-USDC div", "ホライズン": _label,
                    "Split": _split, "単調性": round(_ms, 3),
                })
            _q_test = quantile_analysis(_m, "usdt_usdc_div", _fwd, split="test")
            plot_quantile_bar(_axes_div[_ci, _ti], _q_test, f"{_sym} USDT-USDC → {_label} (Test)")
    _fig_div.suptitle("USDT-USDC供給変化率乖離 五分位 → 将来リターン (Test)", fontsize=14, y=1.02)
    _fig_div.tight_layout()

    sc_mono_df = pl.DataFrame(_mono_rows)

    mo.vstack([
        mo.md("## 2. ステーブルコイン供給変化 分析"),
        mo.md("""
**仮説**: ステーブルコイン供給増加 = 新規資金流入 → 価格上昇先行指標？
- SC MCap 7日変化率の五分位分析
- USDT vs USDC 供給変化の乖離（USDT増加+USDC減少 = リスクオン？）
"""),
        _fig_q,
        _fig_div,
        mo.md("### 単調性スコア"),
        mo.ui.table(sc_mono_df.to_pandas()),
    ])
    return (sc_mono_df,)


# ============================================================
# Cell 7: sec_basis — ベーシスレート分析
# ============================================================
@app.cell
def sec_basis(COLORS, TOKENS, event_stats, merged_data, mo, monotonicity_score, np, pl, plot_quantile_bar, plt, quantile_analysis):
    # 五分位分析: basis_rate → 将来リターン
    _fig_q, _axes_q = plt.subplots(3, 4, figsize=(18, 12))
    _fwd_cols = ["fwd_1h", "fwd_8h", "fwd_24h"]
    _fwd_labels = ["1h", "8h", "24h"]

    _basis_mono_rows = []
    for _ci, (_fwd, _label) in enumerate(zip(_fwd_cols, _fwd_labels)):
        for _ti, _sym in enumerate(TOKENS):
            for _split in ["train", "test"]:
                _q = quantile_analysis(merged_data[_sym], "basis_rate", _fwd, split=_split)
                _ms = monotonicity_score(_q)
                _basis_mono_rows.append({
                    "トークン": _sym, "特徴量": "basis_rate", "ホライズン": _label,
                    "Split": _split, "単調性": round(_ms, 3),
                })
            _q_test = quantile_analysis(merged_data[_sym], "basis_rate", _fwd, split="test")
            plot_quantile_bar(_axes_q[_ci, _ti], _q_test, f"{_sym} basis → {_label} (Test)")
    _fig_q.suptitle("ベーシスレート 五分位 → 将来リターン (Test)", fontsize=14, y=1.02)
    _fig_q.tight_layout()

    # Z-score ±2σ イベント分析
    _zscore_rows = []
    for _sym in TOKENS:
        _m = merged_data[_sym]
        for _split in ["train", "test"]:
            # High basis (zscore > 2): レバロング過熱 → ショート
            _high = _m.filter((pl.col("basis_zscore_24h") > 2) & (pl.col("split") == _split))
            for _fwd, _label in zip(_fwd_cols, _fwd_labels):
                _rets = _high.get_column(_fwd).drop_nulls().to_numpy()
                _s = event_stats(_rets)
                _zscore_rows.append({
                    "トークン": _sym, "条件": "Z>2 (高ベーシス)",
                    "ホライズン": _label, "Split": _split, **_s,
                })

            # Low basis (zscore < -2): パニック → ロング
            _low = _m.filter((pl.col("basis_zscore_24h") < -2) & (pl.col("split") == _split))
            for _fwd, _label in zip(_fwd_cols, _fwd_labels):
                _rets = _low.get_column(_fwd).drop_nulls().to_numpy()
                _s = event_stats(_rets)
                _zscore_rows.append({
                    "トークン": _sym, "条件": "Z<-2 (低ベーシス)",
                    "ホライズン": _label, "Split": _split, **_s,
                })

    basis_zscore_df = pl.DataFrame(_zscore_rows)
    basis_mono_df = pl.DataFrame(_basis_mono_rows)

    # Basis zscore 五分位
    _fig_z, _axes_z = plt.subplots(3, 4, figsize=(18, 12))
    for _ci, (_fwd, _label) in enumerate(zip(_fwd_cols, _fwd_labels)):
        for _ti, _sym in enumerate(TOKENS):
            for _split in ["train", "test"]:
                _q = quantile_analysis(merged_data[_sym], "basis_zscore_24h", _fwd, split=_split)
                _ms = monotonicity_score(_q)
                _basis_mono_rows.append({
                    "トークン": _sym, "特徴量": "basis_zscore_24h", "ホライズン": _label,
                    "Split": _split, "単調性": round(_ms, 3),
                })
            _q_test = quantile_analysis(merged_data[_sym], "basis_zscore_24h", _fwd, split="test")
            plot_quantile_bar(_axes_z[_ci, _ti], _q_test, f"{_sym} Z-score → {_label} (Test)")
    _fig_z.suptitle("ベーシス Zスコア 五分位 → 将来リターン (Test)", fontsize=14, y=1.02)
    _fig_z.tight_layout()

    mo.vstack([
        mo.md("## 3. 先物ベーシスレート 分析"),
        mo.md("""
**仮説**:
- 高ベーシス = レバロング過熱 → ショート方向
- 負ベーシス/パニック = ショート過熱 → ロング方向
- FRは8h間隔のスナップショットだが、basisは連続的にレバレッジ状態を反映
"""),
        _fig_q,
        _fig_z,
        mo.md("### ベーシス Z-score 極端値イベント統計"),
        mo.ui.table(basis_zscore_df.to_pandas()),
        mo.md("### 単調性スコア"),
        mo.ui.table(basis_mono_df.to_pandas()),
    ])
    return (basis_mono_df, basis_zscore_df)


# ============================================================
# Cell 8: sec_cross_asset — クロスアセット分析
# ============================================================
@app.cell
def sec_cross_asset(COLORS, TOKENS, merged_data, mo, monotonicity_score, np, pl, plot_quantile_bar, plt, quantile_analysis, sp_stats):
    # BTC basis → 他アセットリターン
    _btc_basis = merged_data["BTC"].select([
        "timestamp", "basis_rate", "basis_zscore_24h"
    ]).rename({
        "basis_rate": "btc_basis_rate",
        "basis_zscore_24h": "btc_basis_zscore",
    }).sort("timestamp")

    _cross_rows = []
    _fig, _axes = plt.subplots(3, 3, figsize=(14, 12))  # ETH, SOL, SUI × 3 horizons
    _other_tokens = ["ETH", "SOL", "SUI"]
    _fwd_cols = ["fwd_1h", "fwd_8h", "fwd_24h"]
    _fwd_labels = ["1h", "8h", "24h"]

    for _ti, _sym in enumerate(_other_tokens):
        _m = merged_data[_sym].sort("timestamp")
        _cross = _m.join_asof(_btc_basis, on="timestamp", strategy="backward")
        for _ci, (_fwd, _label) in enumerate(zip(_fwd_cols, _fwd_labels)):
            for _split in ["train", "test"]:
                _q = quantile_analysis(_cross, "btc_basis_rate", _fwd, split=_split)
                _ms = monotonicity_score(_q)
                _cross_rows.append({
                    "ターゲット": _sym, "特徴量": "BTC basis_rate",
                    "ホライズン": _label, "Split": _split, "単調性": round(_ms, 3),
                })
            _q_test = quantile_analysis(_cross, "btc_basis_rate", _fwd, split="test")
            plot_quantile_bar(_axes[_ci, _ti], _q_test, f"BTC basis → {_sym} {_label} (Test)")
    _fig.suptitle("クロスアセット: BTC ベーシス → ALTリターン (Test)", fontsize=14, y=1.02)
    _fig.tight_layout()

    # F&G × basis クロス効果
    _cross_fg_rows = []
    for _sym in TOKENS:
        _m = merged_data[_sym]
        for _split in ["train", "test"]:
            _sub = _m.filter(pl.col("split") == _split).drop_nulls(subset=["fg_value", "basis_zscore_24h", "fwd_24h"])
            if _sub.height < 20:
                continue

            # F&G low + basis low → strong long
            _ll = _sub.filter((pl.col("fg_value") < 30) & (pl.col("basis_zscore_24h") < -1))
            _rets_ll = _ll.get_column("fwd_24h").drop_nulls().to_numpy()

            # F&G high + basis high → strong short
            _hh = _sub.filter((pl.col("fg_value") > 70) & (pl.col("basis_zscore_24h") > 1))
            _rets_hh = _hh.get_column("fwd_24h").drop_nulls().to_numpy()

            from collections import OrderedDict
            for _label, _rets in [("Fear+LowBasis", _rets_ll), ("Greed+HighBasis", _rets_hh)]:
                if len(_rets) < 3:
                    _cross_fg_rows.append({
                        "トークン": _sym, "条件": _label, "Split": _split,
                        "N": len(_rets), "mean_bps": 0, "t": 0, "p": 1.0, "win": 0,
                    })
                else:
                    _t, _p = sp_stats.ttest_1samp(_rets, 0)
                    _cross_fg_rows.append({
                        "トークン": _sym, "条件": _label, "Split": _split,
                        "N": len(_rets),
                        "mean_bps": round(float(np.mean(_rets)) * 10000, 2),
                        "t": round(float(_t), 2),
                        "p": round(float(_p), 4),
                        "win": round(float(np.mean(_rets > 0)), 3),
                    })

    _cross_df = pl.DataFrame(_cross_rows)
    _cross_fg_df = pl.DataFrame(_cross_fg_rows)

    mo.vstack([
        mo.md("## 4. クロスアセット分析"),
        mo.md("""
**BTC先行指標仮説**: BTCのベーシスが他アルトのリターンを予測する？
**F&G × Basis クロス効果**: Fear + 低ベーシス = 強い逆張りロング？ Greed + 高ベーシス = 強い逆張りショート？
"""),
        _fig,
        mo.md("### クロスアセット 単調性スコア"),
        mo.ui.table(_cross_df.to_pandas()),
        mo.md("### F&G × Basis クロス効果 (24h fwd)"),
        mo.ui.table(_cross_fg_df.to_pandas()),
    ])


# ============================================================
# Cell 9: sec_walkforward — ウォークフォワード検証
# ============================================================
@app.cell
def sec_walkforward(TOKENS, merged_data, mo, monotonicity_score, np, pl, plt, quantile_analysis, sp_stats):
    # 全特徴量のTrain/Test 単調性・p値比較
    _features = ["fg_value", "fg_change_7d", "sc_mcap_chg_7d", "usdt_chg_7d",
                  "basis_rate", "basis_zscore_24h", "basis_ma_diff"]
    _fwd_cols = ["fwd_1h", "fwd_8h", "fwd_24h"]
    _fwd_labels = ["1h", "8h", "24h"]

    _wf_rows = []
    for _sym in TOKENS:
        _m = merged_data[_sym]
        for _feat in _features:
            for _fwd, _label in zip(_fwd_cols, _fwd_labels):
                _q_train = quantile_analysis(_m, _feat, _fwd, split="train")
                _q_test = quantile_analysis(_m, _feat, _fwd, split="test")
                _ms_train = monotonicity_score(_q_train)
                _ms_test = monotonicity_score(_q_test)

                # p値: Q1 vs Q5の差の検定
                _p_val = 1.0
                if not _q_test.is_empty() and _q_test.height >= 5:
                    _q1_mean = _q_test.filter(pl.col("quantile") == "1").get_column("mean_ret").to_list()
                    _q5_mean = _q_test.filter(pl.col("quantile") == "5").get_column("mean_ret").to_list()
                    if _q1_mean and _q5_mean:
                        _spread = _q5_mean[0] - _q1_mean[0]
                    else:
                        _spread = 0
                else:
                    _spread = 0

                _wf_rows.append({
                    "トークン": _sym, "特徴量": _feat, "ホライズン": _label,
                    "Train単調性": round(_ms_train, 3),
                    "Test単調性": round(_ms_test, 3),
                    "符号一致": "○" if _ms_train * _ms_test > 0 else "×",
                    "Q5-Q1 (Test, bps)": round(_spread * 10000, 2),
                })

    wf_df = pl.DataFrame(_wf_rows)

    # 可視化: Train vs Test 単調性 散布図
    _fig, _ax = plt.subplots(1, 1, figsize=(10, 8))
    _train_mono = wf_df.get_column("Train単調性").to_numpy()
    _test_mono = wf_df.get_column("Test単調性").to_numpy()
    _symbols = wf_df.get_column("トークン").to_list()
    _feats = wf_df.get_column("特徴量").to_list()

    for _sym in TOKENS:
        _mask = wf_df.get_column("トークン") == _sym
        _t = _train_mono[_mask.to_numpy()]
        _te = _test_mono[_mask.to_numpy()]
        _ax.scatter(_t, _te, label=_sym, s=40, alpha=0.6)

    _ax.axhline(0, color="gray", linewidth=0.5)
    _ax.axvline(0, color="gray", linewidth=0.5)
    _ax.plot([-1, 1], [-1, 1], "k--", alpha=0.3, label="y=x")
    _ax.set_xlabel("Train 単調性")
    _ax.set_ylabel("Test 単調性")
    _ax.set_title("ウォークフォワード: Train vs Test 五分位単調性")
    _ax.legend()
    _ax.grid(True, alpha=0.3)
    _ax.set_xlim(-1.1, 1.1)
    _ax.set_ylim(-1.1, 1.1)
    _fig.tight_layout()

    # 符号一致率
    _agree = len(wf_df.filter(pl.col("符号一致") == "○"))
    _total = len(wf_df)

    mo.vstack([
        mo.md("## 5. ウォークフォワード検証"),
        mo.md(f"""
全特徴量 × 全トークン × 全ホライズンの五分位単調性を Train/Test で比較。

- **符号一致率**: {_agree}/{_total} ({_agree/_total*100:.0f}%)
- y=x 線上にあれば Train→Test 持続性あり
"""),
        _fig,
        mo.md("### 全特徴量 Train/Test 統計"),
        mo.ui.table(wf_df.to_pandas()),
    ])
    return (wf_df,)


# ============================================================
# Cell 10: sec_summary — サマリー + 次ステップ
# ============================================================
@app.cell
def sec_summary(TOKENS, basis_mono_df, basis_zscore_df, fg_extreme_df, fg_mono_df, mo, pl, sc_mono_df, wf_df):
    # 有望フィーチャーの抽出: Test期間で符号一致 & |単調性| > 0.3
    _promising = wf_df.filter(
        (pl.col("符号一致") == "○") & (pl.col("Test単調性").abs() > 0.3)
    ).sort(pl.col("Test単調性").abs(), descending=True)

    _n_promising = len(_promising)
    _n_total = len(wf_df)

    # F&G 極端値で有意なもの
    _fg_sig = fg_extreme_df.filter(
        (pl.col("Split") == "test") & (pl.col("p") < 0.1) & (pl.col("N") >= 10)
    )

    # Basis Z-score で有意なもの
    _basis_sig = basis_zscore_df.filter(
        (pl.col("Split") == "test") & (pl.col("p") < 0.1) & (pl.col("N") >= 10)
    )

    mo.vstack([
        mo.md("## 6. 総合サマリー"),
        mo.md(f"""
### 結果概要

- **有望フィーチャー (符号一致 & |単調性| > 0.3)**: {_n_promising}/{_n_total}
- **F&G極端値 Test有意 (p<0.1, N≥10)**: {len(_fg_sig)} 件
- **Basis Z-score Test有意 (p<0.1, N≥10)**: {len(_basis_sig)} 件

### データソース評価

| データ | 仮説 | 結果 |
|--------|------|------|
| Fear & Greed | 極端値は逆張りシグナル | 五分位単調性・イベント統計で評価 |
| Stablecoin供給 | 資金フロー先行指標 | 五分位単調性で評価 |
| Basis spread | FR連続版・高解像度 | Z-scoreイベント + 五分位で評価 |

### 次ステップ

1. **有望フィーチャーの深掘り**: 有意なシグナルの保有期間最適化
2. **複合シグナル**: F&G × Basis の複合条件ルール策定
3. **レジーム分類**: F&Gレベルに基づくトレーディングレジーム分割
4. **バックテスト**: 有望シグナルの実戦的バックテスト（手数料・スリッページ込み）
5. **追加データ検討**: Google Trends, ETFフロー等
"""),
        mo.md("### 有望フィーチャー一覧"),
        mo.ui.table(_promising.to_pandas()) if _n_promising > 0 else mo.md("*該当なし*"),
        mo.md("### F&G 極端値 有意シグナル (Test)"),
        mo.ui.table(_fg_sig.to_pandas()) if len(_fg_sig) > 0 else mo.md("*該当なし*"),
        mo.md("### Basis Z-score 有意シグナル (Test)"),
        mo.ui.table(_basis_sig.to_pandas()) if len(_basis_sig) > 0 else mo.md("*該当なし*"),
    ])


if __name__ == "__main__":
    app.run()
