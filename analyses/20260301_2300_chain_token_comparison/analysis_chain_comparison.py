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

    # 全トークンの1h OHLCVを読み込み
    ohlcv = {}
    for _t in TOKENS:
        ohlcv[_t] = pl.read_parquet(DATA_DIR / f"binance_{_t.lower()}usdt_1h.parquet")

    # 日次集計
    daily = {}
    for _t in TOKENS:
        daily[_t] = (
            ohlcv[_t].sort("timestamp")
            .group_by_dynamic("timestamp", every="1d")
            .agg([
                pl.col("open").first(),
                pl.col("high").max(),
                pl.col("low").min(),
                pl.col("close").last(),
                pl.col("volume").sum(),
            ])
            .with_columns([
                pl.col("close").pct_change().alias("return_1d"),
                (pl.col("close") / pl.col("close").first()).alias("cumulative"),
            ])
            .with_columns(pl.col("timestamp").cast(pl.Date).alias("date"))
        )

    return (
        COLORS,
        DATA_DIR,
        TOKENS,
        daily,
        mo,
        np,
        ohlcv,
        pl,
        plt,
        sp_stats,
    )


# ============================================================
# 1. 正規化価格チャート (基準日=1.0)
# ============================================================
@app.cell
def sec_normalized(TOKENS, COLORS, daily, mo, plt):
    _fig, _axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})

    for _t in TOKENS:
        _d = daily[_t].drop_nulls("return_1d")
        _axes[0].plot(_d["timestamp"].to_list(), _d["cumulative"].to_list(),
                     linewidth=1.0, label=_t, color=COLORS[_t])

    _axes[0].set_title("チェーントークン価格推移 (正規化, 基準日=1.0)")
    _axes[0].set_ylabel("累積リターン (倍率)")
    _axes[0].legend(fontsize=11)
    _axes[0].grid(True, alpha=0.3)
    _axes[0].axhline(1.0, color="gray", linewidth=0.5, linestyle="--")

    # ドローダウン
    for _t in TOKENS:
        _d = daily[_t].drop_nulls("return_1d")
        _cum = _d["cumulative"].to_numpy()
        _peak = _d["cumulative"].cum_max().to_numpy()
        _dd = (_cum - _peak) / _peak
        _axes[1].plot(_d["timestamp"].to_list(), _dd, linewidth=0.8, label=_t, color=COLORS[_t])

    _axes[1].set_title("最大ドローダウン")
    _axes[1].set_ylabel("DD (%)")
    _axes[1].legend(fontsize=9)
    _axes[1].grid(True, alpha=0.3)
    _axes[1].axhline(0, color="gray", linewidth=0.5)
    _fig.tight_layout()

    mo.vstack([
        mo.md("## 1. 正規化価格推移 & ドローダウン"),
        mo.md("2025-03-01を基準(1.0)とした累積リターン。下段は高値からのドローダウン。"),
        _fig,
    ])
    return


# ============================================================
# 2. パフォーマンス比較テーブル
# ============================================================
@app.cell
def sec_performance(TOKENS, daily, mo, np, pl):
    _rows = []
    for _t in TOKENS:
        _d = daily[_t].drop_nulls("return_1d")
        _ret = _d["return_1d"]
        _close = _d["close"]
        _cum = _d["cumulative"]
        _peak = _cum.cum_max()
        _dd = ((_cum - _peak) / _peak).min()

        # 月次リターン集計
        _monthly = (
            _d.with_columns(pl.col("timestamp").dt.strftime("%Y-%m").alias("month"))
            .group_by("month")
            .agg(pl.col("return_1d").map_batches(lambda s: (1 + s).product() - 1).alias("monthly_ret"))
        )
        _best_month = _monthly["monthly_ret"].max()
        _worst_month = _monthly["monthly_ret"].min()

        _rows.append({
            "トークン": _t,
            "現在価格": round(float(_close.to_list()[-1]), 2),
            "期間リターン": round(float(_cum.to_list()[-1] - 1), 4),
            "年率ボラティリティ": round(float(_ret.std() * np.sqrt(365)), 4),
            "シャープ比(年率)": round(float(_ret.mean() / _ret.std() * np.sqrt(365)), 4) if _ret.std() > 0 else 0,
            "最大ドローダウン": round(float(_dd), 4),
            "最良月": round(float(_best_month), 4),
            "最悪月": round(float(_worst_month), 4),
            "正リターン日率": round(float((_ret > 0).mean()), 4),
        })

    _perf_df = pl.DataFrame(_rows)

    mo.vstack([
        mo.md("## 2. パフォーマンス比較"),
        mo.ui.table(_perf_df.to_pandas()),
    ])
    return


# ============================================================
# 3. 日次リターン分布
# ============================================================
@app.cell
def sec_return_dist(TOKENS, COLORS, daily, mo, np, pl, plt, sp_stats):
    _fig, _axes = plt.subplots(1, 2, figsize=(14, 5))

    _stats_rows = []
    for _t in TOKENS:
        _ret = daily[_t].drop_nulls("return_1d")["return_1d"].to_numpy()
        _axes[0].hist(_ret, bins=60, alpha=0.5, label=_t, color=COLORS[_t], density=True)
        _stats_rows.append({
            "トークン": _t,
            "平均": round(float(np.mean(_ret)), 6),
            "中央値": round(float(np.median(_ret)), 6),
            "標準偏差": round(float(np.std(_ret)), 6),
            "歪度": round(float(sp_stats.skew(_ret)), 4),
            "尖度": round(float(sp_stats.kurtosis(_ret)), 4),
            "最大日次リターン": round(float(np.max(_ret)), 4),
            "最小日次リターン": round(float(np.min(_ret)), 4),
        })

    _axes[0].set_title("日次リターン分布")
    _axes[0].set_xlabel("日次リターン")
    _axes[0].set_ylabel("密度")
    _axes[0].legend()
    _axes[0].grid(True, alpha=0.3)

    # ボックスプロット
    _box_data = [daily[_t].drop_nulls("return_1d")["return_1d"].to_numpy() for _t in TOKENS]
    _bp = _axes[1].boxplot(_box_data, labels=TOKENS, patch_artist=True)
    for _patch, _t in zip(_bp["boxes"], TOKENS):
        _patch.set_facecolor(COLORS[_t])
        _patch.set_alpha(0.6)
    _axes[1].set_title("日次リターン分布 (箱ひげ図)")
    _axes[1].set_ylabel("日次リターン")
    _axes[1].grid(True, alpha=0.3)
    _axes[1].axhline(0, color="gray", linewidth=0.5)
    _fig.tight_layout()

    _stats_df = pl.DataFrame(_stats_rows)

    mo.vstack([
        mo.md("## 3. 日次リターン分布"),
        _fig,
        mo.md("### 分布統計"),
        mo.ui.table(_stats_df.to_pandas()),
    ])
    return


# ============================================================
# 4. 相関マトリクス
# ============================================================
@app.cell
def sec_correlation(TOKENS, COLORS, daily, mo, np, pl, plt):
    # 日次リターンを結合
    _base = daily[TOKENS[0]].drop_nulls("return_1d").select("date", pl.col("return_1d").alias(TOKENS[0]))
    for _t in TOKENS[1:]:
        _other = daily[_t].drop_nulls("return_1d").select("date", pl.col("return_1d").alias(_t))
        _base = _base.join(_other, on="date", how="inner")

    _corr_matrix = np.corrcoef([_base[_t].to_numpy() for _t in TOKENS])

    _fig, _axes = plt.subplots(1, 2, figsize=(14, 5))

    # ヒートマップ
    _im = _axes[0].imshow(_corr_matrix, cmap="RdYlBu_r", vmin=0, vmax=1)
    _axes[0].set_xticks(range(len(TOKENS)))
    _axes[0].set_yticks(range(len(TOKENS)))
    _axes[0].set_xticklabels(TOKENS)
    _axes[0].set_yticklabels(TOKENS)
    for _i in range(len(TOKENS)):
        for _j in range(len(TOKENS)):
            _axes[0].text(_j, _i, f"{_corr_matrix[_i, _j]:.3f}",
                         ha="center", va="center", fontsize=11,
                         color="white" if _corr_matrix[_i, _j] > 0.7 else "black")
    _axes[0].set_title("日次リターン相関マトリクス")
    plt.colorbar(_im, ax=_axes[0], shrink=0.8)

    # ローリング相関 (BTC vs 各トークン, 30日)
    for _t in TOKENS[1:]:
        _rolling = (
            _base.select("date", TOKENS[0], _t)
            .with_columns(
                pl.corr(TOKENS[0], _t).over(pl.col("date")).alias("dummy")  # placeholder
            )
        )
        # 手動ローリング計算
        _btc = _base[TOKENS[0]].to_numpy()
        _other = _base[_t].to_numpy()
        _window = 30
        _corrs = []
        _dates = _base["date"].to_list()
        for _k in range(_window - 1):
            _corrs.append(np.nan)
        for _k in range(_window - 1, len(_btc)):
            _c = np.corrcoef(_btc[_k - _window + 1:_k + 1], _other[_k - _window + 1:_k + 1])[0, 1]
            _corrs.append(_c)
        _axes[1].plot(_dates, _corrs, linewidth=0.8, label=f"BTC vs {_t}", color=COLORS[_t])

    _axes[1].set_title("BTC vs 各トークン ローリング相関 (30日)")
    _axes[1].set_ylabel("相関係数")
    _axes[1].legend()
    _axes[1].grid(True, alpha=0.3)
    _axes[1].axhline(0, color="gray", linewidth=0.5)
    _fig.tight_layout()

    # 相関テーブル
    _corr_rows = []
    for _i, _t1 in enumerate(TOKENS):
        for _j, _t2 in enumerate(TOKENS):
            if _i < _j:
                _corr_rows.append({
                    "ペア": f"{_t1}/{_t2}",
                    "相関係数": round(_corr_matrix[_i, _j], 4),
                })
    _corr_df = pl.DataFrame(_corr_rows).sort("相関係数", descending=True)

    mo.vstack([
        mo.md("## 4. 相関分析"),
        _fig,
        mo.md("### 相関係数ペア一覧"),
        mo.ui.table(_corr_df.to_pandas()),
    ])
    return


# ============================================================
# 5. 月次リターンヒートマップ
# ============================================================
@app.cell
def sec_monthly(TOKENS, COLORS, daily, mo, np, pl, plt):
    _fig, _axes = plt.subplots(len(TOKENS), 1, figsize=(14, 3 * len(TOKENS)), sharex=True)

    for _idx, _t in enumerate(TOKENS):
        _d = daily[_t].drop_nulls("return_1d")
        _monthly = (
            _d.with_columns([
                pl.col("timestamp").dt.year().alias("year"),
                pl.col("timestamp").dt.month().alias("month"),
            ])
            .group_by(["year", "month"])
            .agg(pl.col("return_1d").map_batches(lambda s: (1 + s).product() - 1).alias("monthly_ret"))
            .sort(["year", "month"])
        )

        _months = [f"{_r['year']}-{_r['month']:02d}" for _r in _monthly.iter_rows(named=True)]
        _rets = _monthly["monthly_ret"].to_numpy()
        _bar_colors = ["tab:green" if _r > 0 else "tab:red" for _r in _rets]
        _axes[_idx].bar(_months, _rets, color=_bar_colors, alpha=0.7)
        _axes[_idx].set_ylabel(f"{_t}")
        _axes[_idx].axhline(0, color="black", linewidth=0.5)
        _axes[_idx].grid(True, alpha=0.3)
        _axes[_idx].tick_params(axis="x", rotation=45)

    _axes[0].set_title("月次リターン比較")
    _fig.tight_layout()

    mo.vstack([
        mo.md("## 5. 月次リターン比較"),
        _fig,
    ])
    return


# ============================================================
# 6. ボラティリティ比較
# ============================================================
@app.cell
def sec_volatility(TOKENS, COLORS, daily, mo, np, plt):
    _fig, _axes = plt.subplots(1, 2, figsize=(14, 5))

    # ローリングボラティリティ (30日)
    for _t in TOKENS:
        _ret = daily[_t].drop_nulls("return_1d")["return_1d"].to_numpy()
        _dates = daily[_t].drop_nulls("return_1d")["timestamp"].to_list()
        _window = 30
        _vols = [np.nan] * (_window - 1)
        for _k in range(_window - 1, len(_ret)):
            _vols.append(np.std(_ret[_k - _window + 1:_k + 1]) * np.sqrt(365))
        _axes[0].plot(_dates, _vols, linewidth=0.8, label=_t, color=COLORS[_t])

    _axes[0].set_title("年率ボラティリティ (30日ローリング)")
    _axes[0].set_ylabel("年率ボラティリティ")
    _axes[0].legend()
    _axes[0].grid(True, alpha=0.3)

    # ボラティリティ vs リターン散布図
    for _t in TOKENS:
        _ret = daily[_t].drop_nulls("return_1d")["return_1d"]
        _ann_ret = float(_ret.mean()) * 365
        _ann_vol = float(_ret.std()) * np.sqrt(365)
        _axes[1].scatter(_ann_vol, _ann_ret, s=120, color=COLORS[_t], zorder=5)
        _axes[1].annotate(_t, (_ann_vol, _ann_ret), fontsize=12, fontweight="bold",
                         xytext=(8, 8), textcoords="offset points")

    _axes[1].set_title("リスク・リターン マップ")
    _axes[1].set_xlabel("年率ボラティリティ")
    _axes[1].set_ylabel("年率リターン")
    _axes[1].grid(True, alpha=0.3)
    _axes[1].axhline(0, color="gray", linewidth=0.5)
    _axes[1].axvline(0, color="gray", linewidth=0.5)
    _fig.tight_layout()

    mo.vstack([
        mo.md("## 6. ボラティリティ分析"),
        _fig,
    ])
    return


# ============================================================
# 7. ベータ・アルファ分析 (BTC基準)
# ============================================================
@app.cell
def sec_beta(TOKENS, COLORS, daily, mo, np, pl, plt, sp_stats):
    _base = daily[TOKENS[0]].drop_nulls("return_1d").select("date", pl.col("return_1d").alias("BTC"))
    for _t in TOKENS[1:]:
        _other = daily[_t].drop_nulls("return_1d").select("date", pl.col("return_1d").alias(_t))
        _base = _base.join(_other, on="date", how="inner")

    _btc_ret = _base["BTC"].to_numpy()

    _fig, _axes = plt.subplots(1, len(TOKENS) - 1, figsize=(6 * (len(TOKENS) - 1), 5))
    _beta_rows = []

    for _idx, _t in enumerate(TOKENS[1:]):
        _alt_ret = _base[_t].to_numpy()
        _slope, _intercept, _r, _p, _se = sp_stats.linregress(_btc_ret, _alt_ret)

        _axes[_idx].scatter(_btc_ret, _alt_ret, alpha=0.3, s=10, color=COLORS[_t])
        _xl = np.linspace(_btc_ret.min(), _btc_ret.max(), 100)
        _axes[_idx].plot(_xl, _slope * _xl + _intercept, color="red", linewidth=1.5, label="回帰直線")
        _axes[_idx].set_xlabel("BTC 日次リターン")
        _axes[_idx].set_ylabel(f"{_t} 日次リターン")
        _axes[_idx].set_title(f"BTC vs {_t}")
        _axes[_idx].grid(True, alpha=0.3)
        _axes[_idx].legend(fontsize=9)
        _sig = "***" if _p < 0.001 else "**" if _p < 0.01 else "*" if _p < 0.05 else ""
        _axes[_idx].annotate(
            f"β = {_slope:.4f}\nα = {_intercept:.6f}\nR² = {_r**2:.4f}\np = {_p:.4f} {_sig}",
            xy=(0.05, 0.95), xycoords="axes fraction", fontsize=10,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
        )

        _beta_rows.append({
            "トークン": _t,
            "β (ベータ)": round(_slope, 4),
            "α (日次アルファ)": round(_intercept, 6),
            "α (年率)": round(_intercept * 365, 4),
            "R²": round(_r**2, 4),
            "p値": round(_p, 6),
        })

    _fig.tight_layout()
    _beta_df = pl.DataFrame(_beta_rows)

    mo.vstack([
        mo.md("## 7. ベータ・アルファ分析 (BTC基準)"),
        mo.md("""
- **β > 1**: BTCより高リスク・高リターン（レバレッジ的な動き）
- **β < 1**: BTCより低リスク
- **α > 0**: BTC対比で超過リターン
"""),
        _fig,
        mo.ui.table(_beta_df.to_pandas()),
    ])
    return


if __name__ == "__main__":
    app.run()
