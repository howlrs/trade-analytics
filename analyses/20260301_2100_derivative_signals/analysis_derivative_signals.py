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

    # matplotlib 日本語フォント設定
    fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mpl.rcParams["font.family"] = "Noto Sans CJK JP"
    mpl.rcParams["axes.unicode_minus"] = False

    DATA_DIR = Path(__file__).parent.parent.parent / "data"

    # --- OHLCV (1h, 1年分) ---
    binance_btc = pl.read_parquet(DATA_DIR / "binance_btcusdt_1h.parquet")
    binance_eth = pl.read_parquet(DATA_DIR / "binance_ethusdt_1h.parquet")

    # --- Funding Rate (8h, 1年分) ---
    binance_btc_fr = pl.read_parquet(DATA_DIR / "binance_btcusdt_funding_rate.parquet")
    binance_eth_fr = pl.read_parquet(DATA_DIR / "binance_ethusdt_funding_rate.parquet")
    bybit_btc_fr = pl.read_parquet(DATA_DIR / "bybit_btcusdt_funding_rate.parquet")
    bybit_eth_fr = pl.read_parquet(DATA_DIR / "bybit_ethusdt_funding_rate.parquet")

    # --- Open Interest (Bybit 4h, 1年分) ---
    bybit_btc_oi = pl.read_parquet(DATA_DIR / "bybit_btcusdt_open_interest.parquet")
    bybit_eth_oi = pl.read_parquet(DATA_DIR / "bybit_ethusdt_open_interest.parquet")

    # --- Long/Short Ratio (30日分) ---
    btc_ls_global = pl.read_parquet(DATA_DIR / "binance_btcusdt_ls_global.parquet")
    btc_ls_top = pl.read_parquet(DATA_DIR / "binance_btcusdt_ls_top_position.parquet")
    eth_ls_global = pl.read_parquet(DATA_DIR / "binance_ethusdt_ls_global.parquet")
    eth_ls_top = pl.read_parquet(DATA_DIR / "binance_ethusdt_ls_top_position.parquet")

    # --- DefiLlama (日次, 1年分) ---
    eth_tvl = pl.read_parquet(DATA_DIR / "defillama_ethereum_tvl.parquet")
    dex_volume = pl.read_parquet(DATA_DIR / "defillama_dex_volume.parquet")

    return (
        DATA_DIR,
        binance_btc,
        binance_btc_fr,
        binance_eth,
        binance_eth_fr,
        btc_ls_global,
        btc_ls_top,
        bybit_btc_fr,
        bybit_btc_oi,
        bybit_eth_fr,
        bybit_eth_oi,
        dex_volume,
        eth_ls_global,
        eth_ls_top,
        eth_tvl,
        mo,
        np,
        pl,
        plt,
        sp_stats,
    )


# ============================================================
# ヘルパー関数
# ============================================================
@app.cell
def helpers(np, pl, plt, sp_stats):
    def scatter_regression(ax, x, y, xlabel, title, color="tab:blue"):
        """散布図 + 回帰直線を描画し、回帰統計を返す"""
        mask = np.isfinite(x) & np.isfinite(y)
        x, y = x[mask], y[mask]
        if len(x) < 5:
            ax.set_title(f"{title} (データ不足)")
            return None
        slope, intercept, r, p, se = sp_stats.linregress(x, y)
        ax.scatter(x, y, alpha=0.35, s=12, color=color, edgecolors="none")
        x_line = np.linspace(x.min(), x.max(), 100)
        ax.plot(x_line, slope * x_line + intercept, color="red", linewidth=1.5, label="回帰直線")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("日次リターン")
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="lower right", fontsize=9)
        sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
        ax.annotate(
            f"傾き = {slope:.6f}\nr = {r:.4f}\np = {p:.4f} {sig}",
            xy=(0.05, 0.95),
            xycoords="axes fraction",
            fontsize=10,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
        )
        return {"指標": title, "傾き": slope, "r": r, "R²": r**2, "p値": p, "SE": se, "N": len(x)}

    def daily_ohlcv(df):
        """1h OHLCV → 日次リターン"""
        return (
            df.sort("timestamp")
            .group_by_dynamic("timestamp", every="1d")
            .agg(pl.col("close").last())
            .with_columns(pl.col("close").pct_change().alias("return_1d"))
            .drop_nulls("return_1d")
            .with_columns(pl.col("timestamp").cast(pl.Date).alias("date"))
        )

    def daily_fr_sum(df):
        """8h FR → 日次合算"""
        return (
            df.sort("timestamp")
            .group_by_dynamic("timestamp", every="1d")
            .agg(pl.col("funding_rate").sum().alias("daily_fr"))
            .with_columns(pl.col("timestamp").cast(pl.Date).alias("date"))
        )

    def daily_oi_change(df):
        """4h OI → 日次変化率"""
        return (
            df.sort("timestamp")
            .group_by_dynamic("timestamp", every="1d")
            .agg(pl.col("open_interest").last())
            .with_columns(pl.col("open_interest").pct_change().alias("oi_change"))
            .drop_nulls("oi_change")
            .with_columns(pl.col("timestamp").cast(pl.Date).alias("date"))
        )

    return daily_fr_sum, daily_oi_change, daily_ohlcv, scatter_regression


# ============================================================
# 1. FR 乖離シグナル (Binance - Bybit)
# ============================================================
@app.cell
def fr_divergence(
    binance_btc_fr,
    binance_eth_fr,
    bybit_btc_fr,
    bybit_eth_fr,
    daily_fr_sum,
    daily_ohlcv,
    binance_btc,
    binance_eth,
    mo,
    np,
    pl,
    plt,
    scatter_regression,
):
    def _analyze_fr_div(binance_fr, bybit_fr, ohlcv, symbol):
        b_fr = daily_fr_sum(binance_fr).select("date", pl.col("daily_fr").alias("binance_fr"))
        y_fr = daily_fr_sum(bybit_fr).select("date", pl.col("daily_fr").alias("bybit_fr"))
        price = daily_ohlcv(ohlcv).select("date", "return_1d")

        merged = (
            b_fr.join(y_fr, on="date", how="inner")
            .with_columns((pl.col("binance_fr") - pl.col("bybit_fr")).alias("fr_div"))
            .join(price, on="date", how="inner")
        )

        # 当日のFR乖離 vs 翌日リターン
        merged = merged.sort("date").with_columns(
            pl.col("return_1d").shift(-1).alias("next_return")
        ).drop_nulls("next_return")

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        x_div = merged["fr_div"].to_numpy()
        y_ret = merged["next_return"].to_numpy()
        y_same = merged["return_1d"].to_numpy()

        reg1 = scatter_regression(
            axes[0], x_div, y_same,
            "FR乖離 (Binance - Bybit)", f"{symbol}: FR乖離 vs 当日リターン",
        )
        reg2 = scatter_regression(
            axes[1], x_div, y_ret,
            "FR乖離 (Binance - Bybit)", f"{symbol}: FR乖離 vs 翌日リターン",
            color="tab:orange",
        )
        fig.tight_layout()
        return fig, [reg1, reg2]

    fig_btc_div, regs_btc_div = _analyze_fr_div(binance_btc_fr, bybit_btc_fr, binance_btc, "BTC")
    fig_eth_div, regs_eth_div = _analyze_fr_div(binance_eth_fr, bybit_eth_fr, binance_eth, "ETH")

    fr_div_results = [r for r in regs_btc_div + regs_eth_div if r]

    mo.vstack([
        mo.md("## 1. FR乖離シグナル (Binance - Bybit)"),
        mo.md("""
Binance と Bybit の Funding Rate 差分が価格方向の予測力を持つか検証。
FR乖離が大きい = 取引所間でセンチメント差 → アービトラージ機会 or 反転シグナル。
"""),
        mo.md("### BTC"),
        fig_btc_div,
        mo.md("### ETH"),
        fig_eth_div,
    ])
    return (fr_div_results,)


# ============================================================
# 2. FR 極端値シグナル
# ============================================================
@app.cell
def fr_extreme(
    binance_btc_fr,
    binance_eth_fr,
    daily_fr_sum,
    daily_ohlcv,
    binance_btc,
    binance_eth,
    mo,
    np,
    pl,
    plt,
    scatter_regression,
):
    def _analyze_fr_extreme(fr, ohlcv, symbol):
        fr_d = daily_fr_sum(fr).select("date", "daily_fr")
        price = daily_ohlcv(ohlcv).select("date", "return_1d")

        merged = fr_d.join(price, on="date", how="inner").sort("date")
        merged = merged.with_columns(
            pl.col("return_1d").shift(-1).alias("next_return"),
            pl.col("return_1d").shift(-2).alias("return_2d_fwd"),
        ).drop_nulls("return_2d_fwd")

        # FR極端値ゾーン（上位/下位10%）
        fr_vals = merged["daily_fr"].to_numpy()
        q10, q90 = np.percentile(fr_vals, [10, 90])

        fig, axes = plt.subplots(1, 3, figsize=(18, 5))

        # 全体: FR vs 翌日リターン
        reg1 = scatter_regression(
            axes[0], fr_vals, merged["next_return"].to_numpy(),
            "日次FR合算", f"{symbol}: FR vs 翌日リターン",
        )

        # 高FR日の翌日リターン分布
        high_fr = merged.filter(pl.col("daily_fr") > q90)
        low_fr = merged.filter(pl.col("daily_fr") < q10)
        normal_fr = merged.filter((pl.col("daily_fr") >= q10) & (pl.col("daily_fr") <= q90))

        categories = ["低FR\n(下位10%)", "通常", "高FR\n(上位10%)"]
        means = [
            low_fr["next_return"].mean(),
            normal_fr["next_return"].mean(),
            high_fr["next_return"].mean(),
        ]
        stds = [
            low_fr["next_return"].std(),
            normal_fr["next_return"].std(),
            high_fr["next_return"].std(),
        ]
        counts = [low_fr.height, normal_fr.height, high_fr.height]

        bars = axes[1].bar(categories, means, yerr=stds, capsize=5, color=["tab:green", "gray", "tab:red"], alpha=0.7)
        axes[1].set_ylabel("翌日平均リターン")
        axes[1].set_title(f"{symbol}: FRゾーン別 翌日リターン")
        axes[1].axhline(0, color="black", linewidth=0.5)
        axes[1].grid(True, alpha=0.3)
        for bar, c in zip(bars, counts):
            axes[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                        f"n={c}", ha="center", va="bottom", fontsize=9)

        # 2日後リターン
        reg2 = scatter_regression(
            axes[2], fr_vals, merged["return_2d_fwd"].to_numpy(),
            "日次FR合算", f"{symbol}: FR vs 2日後リターン",
            color="tab:purple",
        )
        fig.tight_layout()
        return fig, [reg1, reg2], {"symbol": symbol, "q10": q10, "q90": q90,
                                    "high_mean": means[2], "low_mean": means[0]}

    fig_btc_ext, regs_btc_ext, stats_btc_ext = _analyze_fr_extreme(binance_btc_fr, binance_btc, "BTC")
    fig_eth_ext, regs_eth_ext, stats_eth_ext = _analyze_fr_extreme(binance_eth_fr, binance_eth, "ETH")

    fr_extreme_results = [r for r in regs_btc_ext + regs_eth_ext if r]

    mo.vstack([
        mo.md("## 2. FR極端値シグナル"),
        mo.md("""
FRが極端に高い/低い場合、ポジションの過熱 → 反転が起きやすいか検証。
- **高FR (上位10%)**: ロング過熱 → 下落圧力？
- **低FR (下位10%)**: ショート過熱 → 上昇圧力？
"""),
        mo.md("### BTC"),
        fig_btc_ext,
        mo.md("### ETH"),
        fig_eth_ext,
    ])
    return (fr_extreme_results,)


# ============================================================
# 3. OI急変 + FR方向の複合シグナル
# ============================================================
@app.cell
def oi_fr_combined(
    binance_btc_fr,
    binance_eth_fr,
    bybit_btc_oi,
    bybit_eth_oi,
    daily_fr_sum,
    daily_oi_change,
    daily_ohlcv,
    binance_btc,
    binance_eth,
    mo,
    np,
    pl,
    plt,
    scatter_regression,
):
    def _analyze_oi_fr(fr, oi, ohlcv, symbol):
        fr_d = daily_fr_sum(fr).select("date", "daily_fr")
        oi_d = daily_oi_change(oi).select("date", "oi_change")
        price = daily_ohlcv(ohlcv).select("date", "return_1d")

        merged = (
            price.join(fr_d, on="date", how="inner")
            .join(oi_d, on="date", how="inner")
            .sort("date")
        )
        merged = merged.with_columns(
            pl.col("return_1d").shift(-1).alias("next_return"),
            # 複合スコア: OI変化率 × FR符号
            (pl.col("oi_change") * pl.col("daily_fr").sign()).alias("oi_fr_score"),
        ).drop_nulls("next_return")

        fig, axes = plt.subplots(1, 3, figsize=(18, 5))

        vals = merged.to_dict()

        # OI変化率 vs 翌日リターン
        reg1 = scatter_regression(
            axes[0],
            np.array(vals["oi_change"]),
            np.array(vals["next_return"]),
            "日次OI変化率", f"{symbol}: OI変化率 vs 翌日リターン",
        )

        # 複合スコア vs 翌日リターン
        reg2 = scatter_regression(
            axes[1],
            np.array(vals["oi_fr_score"]),
            np.array(vals["next_return"]),
            "OI変化率 × FR符号", f"{symbol}: 複合スコア vs 翌日リターン",
            color="tab:green",
        )

        # 4象限分析: OI増減 × FR正負
        oi_up_fr_pos = merged.filter((pl.col("oi_change") > 0) & (pl.col("daily_fr") > 0))
        oi_up_fr_neg = merged.filter((pl.col("oi_change") > 0) & (pl.col("daily_fr") < 0))
        oi_dn_fr_pos = merged.filter((pl.col("oi_change") < 0) & (pl.col("daily_fr") > 0))
        oi_dn_fr_neg = merged.filter((pl.col("oi_change") < 0) & (pl.col("daily_fr") < 0))

        labels = ["OI↑FR+\n(強気過熱)", "OI↑FR-\n(逆張り蓄積)", "OI↓FR+\n(利確)", "OI↓FR-\n(投げ売り)"]
        means = [
            oi_up_fr_pos["next_return"].mean() if oi_up_fr_pos.height > 0 else 0,
            oi_up_fr_neg["next_return"].mean() if oi_up_fr_neg.height > 0 else 0,
            oi_dn_fr_pos["next_return"].mean() if oi_dn_fr_pos.height > 0 else 0,
            oi_dn_fr_neg["next_return"].mean() if oi_dn_fr_neg.height > 0 else 0,
        ]
        counts = [oi_up_fr_pos.height, oi_up_fr_neg.height, oi_dn_fr_pos.height, oi_dn_fr_neg.height]
        colors = ["tab:red", "tab:blue", "tab:orange", "tab:green"]

        bars = axes[2].bar(labels, means, color=colors, alpha=0.7)
        axes[2].set_ylabel("翌日平均リターン")
        axes[2].set_title(f"{symbol}: OI×FR 4象限 翌日リターン")
        axes[2].axhline(0, color="black", linewidth=0.5)
        axes[2].grid(True, alpha=0.3)
        for bar, c in zip(bars, counts):
            axes[2].text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                        f"n={c}", ha="center", va="bottom", fontsize=9)

        fig.tight_layout()
        return fig, [reg1, reg2]

    fig_btc_oifr, regs_btc_oifr = _analyze_oi_fr(binance_btc_fr, bybit_btc_oi, binance_btc, "BTC")
    fig_eth_oifr, regs_eth_oifr = _analyze_oi_fr(binance_eth_fr, bybit_eth_oi, binance_eth, "ETH")

    oi_fr_results = [r for r in regs_btc_oifr + regs_eth_oifr if r]

    mo.vstack([
        mo.md("## 3. OI急変 + FR方向の複合シグナル"),
        mo.md("""
OI（建玉）の変化とFRの方向を組み合わせた4象限分析:
- **OI↑ + FR+**: ロング過熱（強気ポジション蓄積）
- **OI↑ + FR-**: ショート蓄積（逆張り）
- **OI↓ + FR+**: ロング利確・解消
- **OI↓ + FR-**: ショート投げ売り

各象限の翌日リターンを比較し、シグナルとしての有効性を検証。
"""),
        mo.md("### BTC (Bybit OI, 1年分)"),
        fig_btc_oifr,
        mo.md("### ETH (Bybit OI, 1年分)"),
        fig_eth_oifr,
    ])
    return (oi_fr_results,)


# ============================================================
# 4. L/S比率 vs リターン (30日分)
# ============================================================
@app.cell
def ls_ratio_analysis(
    btc_ls_top,
    eth_ls_top,
    daily_ohlcv,
    binance_btc,
    binance_eth,
    mo,
    np,
    pl,
    plt,
    scatter_regression,
):
    def _analyze_ls(ls_df, ohlcv, symbol):
        # L/S ratio → 日次集計
        ls_daily = (
            ls_df.sort("timestamp")
            .with_columns(pl.col("timestamp").cast(pl.Date).alias("date"))
            .group_by("date")
            .agg(pl.col("long_short_ratio").mean())
        )
        price = daily_ohlcv(ohlcv).select("date", "return_1d")

        merged = ls_daily.join(price, on="date", how="inner").sort("date")
        merged = merged.with_columns(
            pl.col("return_1d").shift(-1).alias("next_return"),
        ).drop_nulls("next_return")

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        ls_vals = merged["long_short_ratio"].to_numpy()
        ret_same = merged["return_1d"].to_numpy()
        ret_next = merged["next_return"].to_numpy()

        reg1 = scatter_regression(
            axes[0], ls_vals, ret_same,
            "L/S比率 (トップトレーダー)", f"{symbol}: L/S比率 vs 当日リターン",
        )
        reg2 = scatter_regression(
            axes[1], ls_vals, ret_next,
            "L/S比率 (トップトレーダー)", f"{symbol}: L/S比率 vs 翌日リターン",
            color="tab:orange",
        )
        fig.tight_layout()
        return fig, [reg1, reg2]

    fig_btc_ls, regs_btc_ls = _analyze_ls(btc_ls_top, binance_btc, "BTC")
    fig_eth_ls, regs_eth_ls = _analyze_ls(eth_ls_top, binance_eth, "ETH")

    ls_results = [r for r in regs_btc_ls + regs_eth_ls if r]

    mo.vstack([
        mo.md("## 4. L/S比率分析 (トップトレーダー)"),
        mo.md("""
Binance トップトレーダー（残高上位20%）のLong/Short比率 vs 価格リターン。
L/S > 1 = ロング優勢。極端な偏りは反転シグナルとなるか？

**注意**: データは直近30日分のみ（API制限）。サンプル数が限定的。
"""),
        mo.md("### BTC"),
        fig_btc_ls,
        mo.md("### ETH"),
        fig_eth_ls,
    ])
    return (ls_results,)


# ============================================================
# 5. オンチェーン指標 (TVL, DEX Volume) vs 価格
# ============================================================
@app.cell
def onchain_analysis(
    eth_tvl,
    dex_volume,
    daily_ohlcv,
    binance_btc,
    binance_eth,
    mo,
    np,
    pl,
    plt,
    scatter_regression,
):
    # TVL → 日次変化率
    tvl_daily = (
        eth_tvl.sort("timestamp")
        .with_columns(
            pl.col("timestamp").cast(pl.Date).alias("date"),
            pl.col("tvl").pct_change().alias("tvl_change"),
        )
        .drop_nulls("tvl_change")
    )

    # DEX Volume → 日次変化率
    dex_daily = (
        dex_volume.sort("timestamp")
        .with_columns(
            pl.col("timestamp").cast(pl.Date).alias("date"),
            pl.col("dex_volume").pct_change().alias("dex_vol_change"),
            pl.col("dex_volume").cast(pl.Float64).alias("dex_vol_f"),
        )
        .drop_nulls("dex_vol_change")
    )

    eth_price = daily_ohlcv(binance_eth).select("date", pl.col("return_1d").alias("eth_return"))
    btc_price = daily_ohlcv(binance_btc).select("date", pl.col("return_1d").alias("btc_return"))

    # ETH: TVL変化 vs リターン
    merged_tvl = tvl_daily.join(eth_price, on="date", how="inner").sort("date")
    merged_tvl = merged_tvl.with_columns(
        pl.col("eth_return").shift(-1).alias("next_return"),
    ).drop_nulls("next_return")

    # DEX Volume vs BTC/ETH リターン
    merged_dex = (
        dex_daily.join(btc_price, on="date", how="inner")
        .join(eth_price, on="date", how="inner")
        .sort("date")
    )
    merged_dex = merged_dex.with_columns(
        pl.col("btc_return").shift(-1).alias("btc_next"),
        pl.col("eth_return").shift(-1).alias("eth_next"),
    ).drop_nulls("btc_next")

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # TVL変化率 vs ETH当日リターン
    reg1 = scatter_regression(
        axes[0, 0],
        merged_tvl["tvl_change"].to_numpy(),
        merged_tvl["eth_return"].to_numpy(),
        "Ethereum TVL変化率", "TVL変化率 vs ETH当日リターン",
    )

    # TVL変化率 vs ETH翌日リターン
    reg2 = scatter_regression(
        axes[0, 1],
        merged_tvl["tvl_change"].to_numpy(),
        merged_tvl["next_return"].to_numpy(),
        "Ethereum TVL変化率", "TVL変化率 vs ETH翌日リターン",
        color="tab:orange",
    )

    # DEX出来高変化率 vs BTC翌日リターン
    reg3 = scatter_regression(
        axes[1, 0],
        merged_dex["dex_vol_change"].to_numpy(),
        merged_dex["btc_next"].to_numpy(),
        "DEX出来高変化率", "DEX出来高変化 vs BTC翌日リターン",
        color="tab:green",
    )

    # DEX出来高変化率 vs ETH翌日リターン
    reg4 = scatter_regression(
        axes[1, 1],
        merged_dex["dex_vol_change"].to_numpy(),
        merged_dex["eth_next"].to_numpy(),
        "DEX出来高変化率", "DEX出来高変化 vs ETH翌日リターン",
        color="tab:purple",
    )
    fig.tight_layout()

    onchain_results = [r for r in [reg1, reg2, reg3, reg4] if r]

    mo.vstack([
        mo.md("## 5. オンチェーン指標 vs 価格"),
        mo.md("""
DefiLlama の Ethereum TVL と DEX出来高を価格リターンの先行指標として検証。
- **TVL増加** → リスクオン → 翌日上昇？
- **DEX出来高急増** → アクティビティ活発化 → 翌日の方向性は？
"""),
        fig,
    ])
    return (onchain_results,)


# ============================================================
# 6. 総合サマリー
# ============================================================
@app.cell
def summary(
    fr_div_results,
    fr_extreme_results,
    oi_fr_results,
    ls_results,
    onchain_results,
    mo,
    pl,
):
    all_results = fr_div_results + fr_extreme_results + oi_fr_results + ls_results + onchain_results
    if all_results:
        summary_df = pl.DataFrame(all_results).sort("p値")
        summary_df = summary_df.with_columns([
            pl.col("傾き").round(6),
            pl.col("r").round(4),
            pl.col("R²").round(4),
            pl.col("p値").round(4),
            pl.col("SE").round(6),
        ])

        sig_count = summary_df.filter(pl.col("p値") < 0.05).height
        total_count = summary_df.height

        mo.vstack([
            mo.md("## 6. 総合サマリー"),
            mo.md(f"""
### 回帰分析結果一覧（p値昇順）
全{total_count}指標中、統計的に有意（p < 0.05）な指標: **{sig_count}個**

判定基準:
- p < 0.05: 有意（傾きがゼロでない可能性が高い）
- |r| > 0.3: 中程度の相関
- R² > 0.1: 変動の10%以上を説明
"""),
            mo.ui.table(summary_df.to_pandas()),
            mo.md("""
### 注意事項
- L/S比率は30日分のみ（サンプル不足に注意）
- Binance OI は API制限により30日分のみ → Bybit OI（1年分）を使用
- 相関≠因果。有意な指標も市場レジーム変化で無効化されうる
- 次のステップ: 有意な指標をバックテストで戦略に組み込む
"""),
        ])
    return


if __name__ == "__main__":
    app.run()
