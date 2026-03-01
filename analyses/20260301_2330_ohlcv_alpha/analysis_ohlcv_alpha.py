import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


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
    FWD_HOURS = [1, 4, 8, 24]  # 予測ホライズン

    return (
        COLORS,
        DATA_DIR,
        FWD_HOURS,
        TOKENS,
        mo,
        np,
        pl,
        plt,
        sp_stats,
    )


# ============================================================
# 通貨選択 + データロード + 特徴量計算
# ============================================================
@app.cell
def data_load(TOKENS, DATA_DIR, FWD_HOURS, mo, np, pl):
    asset_dropdown = mo.ui.dropdown(options=TOKENS, value="BTC", label="分析対象")
    mo.vstack([
        mo.md("# OHLCV マイクロストラクチャー・アルファ分析"),
        mo.md("ローソク足の形状・出来高パターンからN時間後リターンの予測力を探る。"),
        asset_dropdown,
    ])
    return (asset_dropdown,)


@app.cell
def feature_engineering(asset_dropdown, DATA_DIR, FWD_HOURS, np, pl):
    _sym = asset_dropdown.value.lower()
    raw = pl.read_parquet(DATA_DIR / f"binance_{_sym}usdt_1h.parquet").sort("timestamp")

    _range = pl.col("high") - pl.col("low")
    _body = (pl.col("close") - pl.col("open")).abs()
    _upper = pl.col("high") - pl.max_horizontal("open", "close")
    _lower = pl.min_horizontal("open", "close") - pl.col("low")

    df = raw.with_columns([
        # --- ローソク足形状 ---
        ((pl.col("close") - pl.col("open")) / pl.col("open")).alias("candle_return"),
        (_body / _range).alias("body_ratio"),           # 実体/レンジ比
        (_upper / _range).alias("upper_shadow_ratio"),   # 上ヒゲ比
        (_lower / _range).alias("lower_shadow_ratio"),   # 下ヒゲ比
        ((pl.col("close") - pl.col("low")) / _range).alias("close_position"),  # 終値位置 (0=安値, 1=高値)
        (_range / pl.col("open")).alias("range_pct"),     # レンジ幅 (%)

        # --- 出来高 ---
        (pl.col("volume") / pl.col("volume").rolling_mean(24)).alias("vol_ratio_24h"),   # 直近24h平均比
        (pl.col("volume") / pl.col("volume").rolling_mean(168)).alias("vol_ratio_7d"),   # 7日平均比
        pl.col("volume").rolling_std(24).alias("vol_std_24h"),

        # --- 方向性 ---
        pl.when(pl.col("close") > pl.col("open")).then(1).otherwise(-1).alias("candle_dir"),

        # --- 出来高加重価格位置 (VWAP的) ---
        (pl.col("volume") * (pl.col("close") - pl.col("open")).sign()).alias("signed_volume"),

        # --- モメンタム ---
        (pl.col("close") / pl.col("close").shift(4) - 1).alias("momentum_4h"),
        (pl.col("close") / pl.col("close").shift(24) - 1).alias("momentum_24h"),

        # --- ボラティリティ ---
        (_range / pl.col("open")).rolling_mean(24).alias("atr_24h"),

        # --- 時間帯 ---
        pl.col("timestamp").dt.hour().alias("hour"),
    ])

    # candle_return を使う列は2段目で計算
    df = df.with_columns(
        pl.col("candle_return").rolling_std(24).alias("volatility_24h"),
    )

    # 連続陽線/陰線カウント
    _dir_col = pl.when(pl.col("close") > pl.col("open")).then(1).otherwise(-1)
    df = df.with_columns(_dir_col.alias("_dir"))
    _dirs = df["_dir"].to_numpy()
    _consec = np.zeros(len(_dirs), dtype=int)
    _consec[0] = _dirs[0]
    for _i in range(1, len(_dirs)):
        if _dirs[_i] == np.sign(_consec[_i - 1]):
            _consec[_i] = _consec[_i - 1] + _dirs[_i]
        else:
            _consec[_i] = _dirs[_i]
    df = df.with_columns(pl.Series("consecutive_candles", _consec))
    df = df.drop("_dir")

    # 出来高インバランス: 上昇volume vs 下降volume (過去N本)
    df = df.with_columns([
        (pl.col("signed_volume").rolling_sum(8) /
         pl.col("volume").rolling_sum(8)).alias("vol_imbalance_8h"),
        (pl.col("signed_volume").rolling_sum(24) /
         pl.col("volume").rolling_sum(24)).alias("vol_imbalance_24h"),
    ])

    # --- 将来リターン (ターゲット) ---
    for _h in FWD_HOURS:
        df = df.with_columns(
            (pl.col("close").shift(-_h) / pl.col("close") - 1).alias(f"fwd_{_h}h")
        )

    df = df.drop_nulls(f"fwd_{FWD_HOURS[-1]}h")

    return (df,)


# ============================================================
# ヘルパー
# ============================================================
@app.cell
def helpers(np, plt, sp_stats):
    def scatter_reg(ax, x, y, xlabel, title, color="tab:blue"):
        _mask = np.isfinite(x) & np.isfinite(y)
        x, y = x[_mask], y[_mask]
        if len(x) < 20:
            ax.set_title(f"{title} (データ不足)")
            return None
        _s, _i, _r, _p, _se = sp_stats.linregress(x, y)
        ax.scatter(x, y, alpha=0.15, s=5, color=color, edgecolors="none")
        _xl = np.linspace(np.percentile(x, 1), np.percentile(x, 99), 100)
        ax.plot(_xl, _s * _xl + _i, color="red", linewidth=1.5, label="回帰直線")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("将来リターン")
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        _sig = "***" if _p < 0.001 else "**" if _p < 0.01 else "*" if _p < 0.05 else ""
        ax.annotate(
            f"傾き={_s:.6f}\nr={_r:.4f}  p={_p:.4f} {_sig}",
            xy=(0.05, 0.95), xycoords="axes fraction", fontsize=9,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
        )
        return {"特徴量": xlabel, "ホライズン": title.split("→")[-1].strip() if "→" in title else title,
                "傾き": _s, "r": _r, "R²": _r**2, "p値": _p, "N": len(x)}

    def quintile_analysis(ax, feature, fwd_ret, xlabel, title):
        """5分位に分けて各分位の平均将来リターンを棒グラフで表示"""
        _mask = np.isfinite(feature) & np.isfinite(fwd_ret)
        _f, _r = feature[_mask], fwd_ret[_mask]
        if len(_f) < 50:
            return None
        _q = np.percentile(_f, [20, 40, 60, 80])
        _bins = np.digitize(_f, _q)
        _labels = ["Q1\n(低)", "Q2", "Q3", "Q4", "Q5\n(高)"]
        _means, _stds, _counts = [], [], []
        for _b in range(5):
            _subset = _r[_bins == _b]
            _means.append(np.mean(_subset) if len(_subset) > 0 else 0)
            _stds.append(np.std(_subset) / np.sqrt(len(_subset)) if len(_subset) > 1 else 0)
            _counts.append(len(_subset))
        _colors = ["tab:red" if _m < 0 else "tab:green" for _m in _means]
        _bars = ax.bar(_labels, _means, yerr=_stds, capsize=3, color=_colors, alpha=0.7)
        ax.set_ylabel("平均リターン")
        ax.set_title(title)
        ax.axhline(0, color="black", linewidth=0.5)
        ax.grid(True, alpha=0.3)
        for _bar, _c in zip(_bars, _counts):
            ax.text(_bar.get_x() + _bar.get_width() / 2, _bar.get_height(),
                   f"n={_c}", ha="center", va="bottom", fontsize=7)
        # 単調性テスト (Q1→Q5の平均が単調増加/減少か)
        _monotone = np.corrcoef(range(5), _means)[0, 1]
        ax.annotate(f"単調性 r={_monotone:.3f}", xy=(0.05, 0.05), xycoords="axes fraction",
                   fontsize=9, bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.5))
        return {"特徴量": xlabel, "Q1平均": _means[0], "Q5平均": _means[4],
                "Q5-Q1スプレッド": _means[4] - _means[0], "単調性r": _monotone}

    return quintile_analysis, scatter_reg


# ============================================================
# 1. ローソク足形状 → 将来リターン
# ============================================================
@app.cell
def sec_candle_shape(asset_dropdown, df, FWD_HOURS, mo, np, plt, scatter_reg, quintile_analysis):
    _sym = asset_dropdown.value
    _features = [
        ("body_ratio", "実体比率"),
        ("upper_shadow_ratio", "上ヒゲ比率"),
        ("lower_shadow_ratio", "下ヒゲ比率"),
        ("close_position", "終値位置"),
        ("range_pct", "レンジ幅"),
    ]
    _all_regs = []
    _all_quint = []
    _figs = []

    for _fname, _flabel in _features:
        _fig, _axes = plt.subplots(1, len(FWD_HOURS), figsize=(5 * len(FWD_HOURS), 4))
        for _idx, _h in enumerate(FWD_HOURS):
            _x = df[_fname].to_numpy()
            _y = df[f"fwd_{_h}h"].to_numpy()
            _reg = scatter_reg(_axes[_idx], _x, _y, _flabel, f"{_sym} {_flabel} → {_h}h後")
            if _reg:
                _all_regs.append(_reg)
        _fig.suptitle(f"{_flabel} vs 将来リターン", fontsize=13)
        _fig.tight_layout()
        _figs.append(_fig)

    # 5分位分析 (24h後リターン)
    _fig_q, _axes_q = plt.subplots(1, len(_features), figsize=(5 * len(_features), 4))
    for _idx, (_fname, _flabel) in enumerate(_features):
        _q = quintile_analysis(_axes_q[_idx], df[_fname].to_numpy(),
                              df["fwd_24h"].to_numpy(), _flabel, f"{_flabel} → 24h後")
        if _q:
            _all_quint.append(_q)
    _fig_q.suptitle("ローソク足形状 5分位分析 (24h後リターン)", fontsize=13)
    _fig_q.tight_layout()

    _elements = [
        mo.md("## 1. ローソク足形状 → 将来リターン"),
        mo.md("""
**検証する特徴量:**
- **実体比率**: 実体/レンジ。1に近い=ヒゲなし(強いトレンド)、0に近い=十字線(迷い)
- **上ヒゲ比率**: 上ヒゲ/レンジ。高い=上値抑制(売り圧力)
- **下ヒゲ比率**: 下ヒゲ/レンジ。高い=下値支持(買い支え)
- **終値位置**: (close-low)/(high-low)。1=高値引け、0=安値引け
- **レンジ幅**: (high-low)/open。ボラティリティの代理変数
"""),
    ]
    for _f in _figs:
        _elements.append(_f)
    _elements.append(mo.md("### 5分位分析"))
    _elements.append(_fig_q)

    candle_regs = _all_regs
    candle_quints = _all_quint
    mo.vstack(_elements)
    return candle_quints, candle_regs


# ============================================================
# 2. 出来高パターン → 将来リターン
# ============================================================
@app.cell
def sec_volume(asset_dropdown, df, FWD_HOURS, mo, np, plt, scatter_reg, quintile_analysis):
    _sym = asset_dropdown.value
    _features = [
        ("vol_ratio_24h", "出来高比(24h平均比)"),
        ("vol_ratio_7d", "出来高比(7日平均比)"),
        ("vol_imbalance_8h", "出来高インバランス(8h)"),
        ("vol_imbalance_24h", "出来高インバランス(24h)"),
    ]
    _all_regs = []

    _fig_scatter, _axes = plt.subplots(len(_features), len(FWD_HOURS),
                                        figsize=(5 * len(FWD_HOURS), 4 * len(_features)))
    for _fi, (_fname, _flabel) in enumerate(_features):
        for _hi, _h in enumerate(FWD_HOURS):
            _reg = scatter_reg(_axes[_fi, _hi], df[_fname].to_numpy(),
                              df[f"fwd_{_h}h"].to_numpy(), _flabel,
                              f"{_flabel} → {_h}h後")
            if _reg:
                _all_regs.append(_reg)
    _fig_scatter.tight_layout()

    # 5分位
    _fig_q, _axes_q = plt.subplots(1, len(_features), figsize=(5 * len(_features), 4))
    _all_quint = []
    for _idx, (_fname, _flabel) in enumerate(_features):
        _q = quintile_analysis(_axes_q[_idx], df[_fname].to_numpy(),
                              df["fwd_24h"].to_numpy(), _flabel, f"{_flabel} → 24h後")
        if _q:
            _all_quint.append(_q)
    _fig_q.suptitle("出来高パターン 5分位分析 (24h後リターン)", fontsize=13)
    _fig_q.tight_layout()

    vol_regs = _all_regs
    vol_quints = _all_quint
    mo.vstack([
        mo.md("## 2. 出来高パターン → 将来リターン"),
        mo.md("""
- **出来高比(24h/7d平均)**: スパイク = 注目イベント。スパイク後は反転 or 継続？
- **出来高インバランス**: 過去N時間の(上昇volume - 下降volume)/total。偏り = 方向性？
"""),
        _fig_scatter,
        mo.md("### 5分位分析"),
        _fig_q,
    ])
    return vol_quints, vol_regs


# ============================================================
# 3. 連続陽線/陰線 → 将来リターン
# ============================================================
@app.cell
def sec_consecutive(asset_dropdown, df, mo, np, pl, plt, quintile_analysis):
    _sym = asset_dropdown.value
    _consec = df["consecutive_candles"].to_numpy()
    _fwd_cols = ["fwd_1h", "fwd_4h", "fwd_8h", "fwd_24h"]

    _fig, _axes = plt.subplots(1, len(_fwd_cols), figsize=(5 * len(_fwd_cols), 5))
    _all_quint = []

    for _idx, _fc in enumerate(_fwd_cols):
        # 連続数ごとの平均リターン
        _merged = df.select("consecutive_candles", _fc).drop_nulls()
        _grouped = (
            _merged.group_by("consecutive_candles")
            .agg([
                pl.col(_fc).mean().alias("mean_ret"),
                pl.col(_fc).std().alias("std_ret"),
                pl.col(_fc).len().alias("count"),
            ])
            .filter(pl.col("count") >= 10)
            .sort("consecutive_candles")
        )

        _x = _grouped["consecutive_candles"].to_numpy()
        _y = _grouped["mean_ret"].to_numpy()
        _se = _grouped["std_ret"].to_numpy() / np.sqrt(_grouped["count"].to_numpy())
        _colors = ["tab:green" if _v > 0 else "tab:red" for _v in _y]

        _axes[_idx].bar(_x, _y, yerr=_se, capsize=2, color=_colors, alpha=0.7)
        _axes[_idx].set_xlabel("連続陽線(+)/陰線(-) 本数")
        _axes[_idx].set_ylabel("平均リターン")
        _axes[_idx].set_title(f"連続本数 → {_fc.replace('fwd_', '')}後")
        _axes[_idx].axhline(0, color="black", linewidth=0.5)
        _axes[_idx].grid(True, alpha=0.3)

    _fig.suptitle(f"{_sym}: 連続陽線/陰線 → 将来リターン", fontsize=13)
    _fig.tight_layout()

    consec_fig = _fig
    mo.vstack([
        mo.md("## 3. 連続陽線/陰線パターン"),
        mo.md("""
連続N本の陽線(+)/陰線(-)の後、将来リターンに偏りがあるか検証。
- **正の棒**: そのパターンの後、上昇傾向
- **負の棒**: そのパターンの後、下落傾向
- 3本以上連続 → **ミーンリバージョン**(反転) or **モメンタム**(継続)?
"""),
        consec_fig,
    ])
    return


# ============================================================
# 4. 時間帯効果
# ============================================================
@app.cell
def sec_hourly(asset_dropdown, df, mo, np, pl, plt):
    _sym = asset_dropdown.value

    _hourly = (
        df.group_by("hour")
        .agg([
            pl.col("candle_return").mean().alias("mean_return"),
            pl.col("candle_return").std().alias("std_return"),
            pl.col("candle_return").len().alias("count"),
            pl.col("range_pct").mean().alias("mean_range"),
            pl.col("volume").mean().alias("mean_volume"),
        ])
        .sort("hour")
    )

    _fig, _axes = plt.subplots(1, 3, figsize=(18, 5))

    # 時間帯別リターン
    _hours = _hourly["hour"].to_numpy()
    _means = _hourly["mean_return"].to_numpy()
    _se = _hourly["std_return"].to_numpy() / np.sqrt(_hourly["count"].to_numpy())
    _colors = ["tab:green" if _m > 0 else "tab:red" for _m in _means]
    _axes[0].bar(_hours, _means * 100, yerr=_se * 100, capsize=2, color=_colors, alpha=0.7)
    _axes[0].set_xlabel("時刻 (UTC)")
    _axes[0].set_ylabel("平均リターン (%)")
    _axes[0].set_title(f"{_sym}: 時間帯別 平均リターン")
    _axes[0].axhline(0, color="black", linewidth=0.5)
    _axes[0].grid(True, alpha=0.3)
    _axes[0].set_xticks(range(0, 24, 2))

    # 時間帯別ボラティリティ
    _axes[1].bar(_hours, _hourly["mean_range"].to_numpy() * 100, color="tab:purple", alpha=0.6)
    _axes[1].set_xlabel("時刻 (UTC)")
    _axes[1].set_ylabel("平均レンジ (%)")
    _axes[1].set_title(f"{_sym}: 時間帯別 ボラティリティ")
    _axes[1].grid(True, alpha=0.3)
    _axes[1].set_xticks(range(0, 24, 2))

    # 時間帯別出来高
    _axes[2].bar(_hours, _hourly["mean_volume"].to_numpy(), color="tab:blue", alpha=0.6)
    _axes[2].set_xlabel("時刻 (UTC)")
    _axes[2].set_ylabel("平均出来高")
    _axes[2].set_title(f"{_sym}: 時間帯別 出来高")
    _axes[2].grid(True, alpha=0.3)
    _axes[2].set_xticks(range(0, 24, 2))

    _fig.tight_layout()

    mo.vstack([
        mo.md("## 4. 時間帯効果 (UTC)"),
        mo.md("特定の時刻にリターン・ボラティリティ・出来高の偏りがあるか。東京(UTC+9)、ロンドン(UTC+0)、NY(UTC-5)のオープン前後に注目。"),
        _fig,
    ])
    return


# ============================================================
# 5. ボラティリティ圧縮 → ブレイクアウト
# ============================================================
@app.cell
def sec_vol_squeeze(asset_dropdown, df, FWD_HOURS, mo, np, pl, plt, scatter_reg, quintile_analysis):
    _sym = asset_dropdown.value

    # ATR比率: 現在ATR / 過去168h(7日)ATR → 圧縮度
    _atr_24 = df["atr_24h"].to_numpy()
    _atr_7d = df.with_columns(
        ((pl.col("high") - pl.col("low")).rolling_mean(168) / pl.col("open")).alias("atr_7d")
    )["atr_7d"].to_numpy()

    _squeeze = np.where(_atr_7d > 0, _atr_24 / _atr_7d, np.nan)
    _df2 = df.with_columns(pl.Series("vol_squeeze", _squeeze))

    # 将来のレンジ幅(ボラ拡大の検証)
    _df2 = _df2.with_columns([
        (pl.col("range_pct").shift(-_h).rolling_mean(_h)).alias(f"fwd_range_{_h}h")
        for _h in FWD_HOURS
    ])

    _fig, _axes = plt.subplots(2, len(FWD_HOURS), figsize=(5 * len(FWD_HOURS), 8))
    _all_regs = []

    for _idx, _h in enumerate(FWD_HOURS):
        # スクイーズ → 方向(リターン)
        _reg = scatter_reg(_axes[0, _idx], _df2["vol_squeeze"].to_numpy(),
                          _df2[f"fwd_{_h}h"].to_numpy(),
                          "ボラ圧縮度", f"圧縮度 → {_h}h後リターン")
        if _reg:
            _all_regs.append(_reg)

        # スクイーズ → ボラ拡大
        _reg2 = scatter_reg(_axes[1, _idx], _df2["vol_squeeze"].to_numpy(),
                           _df2[f"fwd_range_{_h}h"].to_numpy(),
                           "ボラ圧縮度", f"圧縮度 → {_h}h後レンジ",
                           color="tab:red")
        if _reg2:
            _all_regs.append(_reg2)

    _fig.suptitle(f"{_sym}: ボラティリティ圧縮 → ブレイクアウト分析", fontsize=13)
    _fig.tight_layout()

    squeeze_regs = _all_regs
    mo.vstack([
        mo.md("## 5. ボラティリティ圧縮 → ブレイクアウト"),
        mo.md("""
**ボラ圧縮度** = 直近24h ATR / 7日ATR。
- < 1: ボラ圧縮中(スクイーズ) → ブレイクアウト前兆？
- 上段: 圧縮度 → 将来リターン(方向予測)
- 下段: 圧縮度 → 将来レンジ幅(ボラ拡大予測)
"""),
        _fig,
    ])
    return (squeeze_regs,)


# ============================================================
# 6. 価格-出来高ダイバージェンス
# ============================================================
@app.cell
def sec_pv_divergence(asset_dropdown, df, FWD_HOURS, mo, np, pl, plt, scatter_reg, quintile_analysis):
    _sym = asset_dropdown.value

    # 価格モメンタム(24h) vs 出来高トレンド(24h)
    _price_mom = df["momentum_24h"].to_numpy()
    _vol_trend = (df["vol_ratio_24h"].to_numpy() - 1)  # 1超=出来高増加

    # ダイバージェンス: 価格上昇 but 出来高減少、またはその逆
    _divergence = _price_mom * (-_vol_trend)  # 正=ダイバージェンス(反転示唆)

    _df2 = df.with_columns(pl.Series("pv_divergence", _divergence))

    _fig, _axes = plt.subplots(1, len(FWD_HOURS), figsize=(5 * len(FWD_HOURS), 4))
    _all_regs = []

    for _idx, _h in enumerate(FWD_HOURS):
        _reg = scatter_reg(_axes[_idx], _divergence,
                          df[f"fwd_{_h}h"].to_numpy(),
                          "PVダイバージェンス", f"PV乖離 → {_h}h後")
        if _reg:
            _all_regs.append(_reg)

    _fig.suptitle(f"{_sym}: 価格-出来高ダイバージェンス", fontsize=13)
    _fig.tight_layout()

    # 5分位
    _fig_q, _axes_q = plt.subplots(1, len(FWD_HOURS), figsize=(5 * len(FWD_HOURS), 4))
    _quint_regs = []
    for _idx, _h in enumerate(FWD_HOURS):
        _q = quintile_analysis(_axes_q[_idx], _divergence,
                              df[f"fwd_{_h}h"].to_numpy(),
                              "PVダイバージェンス", f"PV乖離 5分位 → {_h}h後")
        if _q:
            _quint_regs.append(_q)
    _fig_q.tight_layout()

    pv_regs = _all_regs
    mo.vstack([
        mo.md("## 6. 価格-出来高ダイバージェンス"),
        mo.md("""
**PVダイバージェンス** = 価格モメンタム(24h) × (−出来高トレンド)
- 正値: 価格上昇 + 出来高減少 → **弱い上昇**(反転リスク)
- 負値: 価格下落 + 出来高増加 → **セリングクライマックス**(反転機会?)
"""),
        _fig,
        mo.md("### 5分位分析"),
        _fig_q,
    ])
    return (pv_regs,)


# ============================================================
# 7. 複合シグナル: ピンバー + 出来高スパイク
# ============================================================
@app.cell
def sec_composite(asset_dropdown, df, mo, np, pl, plt, sp_stats):
    _sym = asset_dropdown.value
    _fwd_cols = ["fwd_1h", "fwd_4h", "fwd_8h", "fwd_24h"]

    # ピンバー検出: 長い下ヒゲ + 小さい実体
    _bullish_pin = (
        (pl.col("lower_shadow_ratio") > 0.6) &
        (pl.col("body_ratio") < 0.25)
    )
    # 長い上ヒゲ + 小さい実体
    _bearish_pin = (
        (pl.col("upper_shadow_ratio") > 0.6) &
        (pl.col("body_ratio") < 0.25)
    )
    # 出来高スパイク
    _vol_spike = pl.col("vol_ratio_24h") > 2.0

    _df2 = df.with_columns([
        _bullish_pin.alias("bullish_pin"),
        _bearish_pin.alias("bearish_pin"),
        _vol_spike.alias("vol_spike"),
        (_bullish_pin & _vol_spike).alias("bullish_pin_vol"),
        (_bearish_pin & _vol_spike).alias("bearish_pin_vol"),
    ])

    _patterns = [
        ("bullish_pin", "強気ピンバー"),
        ("bearish_pin", "弱気ピンバー"),
        ("bullish_pin_vol", "強気ピンバー+出来高"),
        ("bearish_pin_vol", "弱気ピンバー+出来高"),
    ]

    _fig, _axes = plt.subplots(len(_patterns), len(_fwd_cols),
                                figsize=(5 * len(_fwd_cols), 4 * len(_patterns)))
    _results = []

    for _pi, (_pname, _plabel) in enumerate(_patterns):
        for _fi, _fc in enumerate(_fwd_cols):
            _signal = _df2.filter(pl.col(_pname))
            _no_signal = _df2.filter(~pl.col(_pname))
            _h = _fc.replace("fwd_", "").replace("h", "")

            if _signal.height < 5:
                _axes[_pi, _fi].set_title(f"{_plabel} → {_h}h後 (n<5)")
                continue

            _sig_ret = _signal[_fc].to_numpy()
            _no_ret = _no_signal[_fc].to_numpy()

            _axes[_pi, _fi].hist(_no_ret, bins=50, alpha=0.4, density=True, label="通常", color="gray")
            _axes[_pi, _fi].hist(_sig_ret, bins=30, alpha=0.6, density=True, label=_plabel, color="tab:orange")
            _axes[_pi, _fi].axvline(np.mean(_sig_ret), color="red", linewidth=1.5, linestyle="--", label=f"平均={np.mean(_sig_ret):.5f}")
            _axes[_pi, _fi].axvline(np.mean(_no_ret), color="gray", linewidth=1, linestyle="--")
            _axes[_pi, _fi].set_title(f"{_plabel} → {_h}h後 (n={_signal.height})")
            _axes[_pi, _fi].legend(fontsize=7)
            _axes[_pi, _fi].grid(True, alpha=0.3)

            # t検定
            _t, _p = sp_stats.ttest_ind(_sig_ret, _no_ret, equal_var=False)
            _results.append({
                "パターン": _plabel,
                "ホライズン": f"{_h}h",
                "シグナル平均": round(np.mean(_sig_ret), 6),
                "通常平均": round(np.mean(_no_ret), 6),
                "差分": round(np.mean(_sig_ret) - np.mean(_no_ret), 6),
                "t値": round(_t, 4),
                "p値": round(_p, 4),
                "シグナル数": _signal.height,
            })

    _fig.tight_layout()
    _results_df = pl.DataFrame(_results).sort("p値") if _results else pl.DataFrame()

    composite_results = _results
    mo.vstack([
        mo.md("## 7. 複合シグナル: ピンバー + 出来高スパイク"),
        mo.md("""
ローソク足パターンと出来高の組み合わせ:
- **強気ピンバー**: 下ヒゲ60%超 + 実体25%未満 → 下値支持
- **弱気ピンバー**: 上ヒゲ60%超 + 実体25%未満 → 上値抑制
- **+出来高スパイク**: 上記 + 出来高が24h平均の2倍超 → より信頼性の高いシグナル

ヒストグラムでシグナル発生時 vs 通常時のリターン分布を比較。t検定で有意差を検証。
"""),
        _fig,
        mo.md("### t検定結果"),
        mo.ui.table(_results_df.to_pandas()) if not _results_df.is_empty() else mo.md("結果なし"),
    ])
    return


# ============================================================
# 8. 総合スクリーニング
# ============================================================
@app.cell
def sec_summary(
    asset_dropdown, candle_regs, candle_quints, vol_regs, vol_quints,
    squeeze_regs, pv_regs,
    mo, pl,
):
    _sym = asset_dropdown.value
    _all_regs = candle_regs + vol_regs + squeeze_regs + pv_regs
    _all_quint = candle_quints + vol_quints

    _elements = [mo.md(f"## 8. 総合スクリーニング ({_sym})")]

    if _all_regs:
        _df = pl.DataFrame(_all_regs).sort("p値")
        _df = _df.with_columns([
            pl.col("傾き").round(8),
            pl.col("r").round(4),
            pl.col("R²").round(4),
            pl.col("p値").round(4),
        ])
        _sig = _df.filter(pl.col("p値") < 0.05)
        _elements.extend([
            mo.md(f"### 回帰分析 全{_df.height}指標 (p値昇順)"),
            mo.md(f"統計的に有意 (p<0.05): **{_sig.height}個**"),
            mo.ui.table(_df.to_pandas()),
        ])

    if _all_quint:
        _qdf = pl.DataFrame(_all_quint).sort("Q5-Q1スプレッド", descending=True)
        _qdf = _qdf.with_columns([
            pl.col("Q1平均").round(6),
            pl.col("Q5平均").round(6),
            pl.col("Q5-Q1スプレッド").round(6),
            pl.col("単調性r").round(4),
        ])
        _elements.extend([
            mo.md("### 5分位分析 (Q5-Q1スプレッド順)"),
            mo.md("|単調性r| > 0.8 かつ スプレッドが大きい特徴量が有望。"),
            mo.ui.table(_qdf.to_pandas()),
        ])

    if len(_elements) == 1:
        _elements.append(mo.md("結果なし"))

    mo.vstack(_elements)
    return


if __name__ == "__main__":
    app.run()
