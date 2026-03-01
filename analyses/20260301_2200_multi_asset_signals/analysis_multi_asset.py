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

    ASSETS = {
        "BTC": {"chain": None},
        "ETH": {"chain": "Ethereum"},
        "SOL": {"chain": "Solana"},
        "SUI": {"chain": "Sui"},
    }

    def load_asset(symbol):
        _s = symbol.lower()
        _data = {}
        _data["binance_ohlcv"] = pl.read_parquet(DATA_DIR / f"binance_{_s}usdt_1h.parquet")
        _data["binance_fr"] = pl.read_parquet(DATA_DIR / f"binance_{_s}usdt_funding_rate.parquet")
        _data["bybit_fr"] = pl.read_parquet(DATA_DIR / f"bybit_{_s}usdt_funding_rate.parquet")
        _data["bybit_oi"] = pl.read_parquet(DATA_DIR / f"bybit_{_s}usdt_open_interest.parquet")
        _ls_path = DATA_DIR / f"binance_{_s}usdt_ls_top_position.parquet"
        _data["ls_top"] = pl.read_parquet(_ls_path) if _ls_path.exists() else None
        _chain = ASSETS[symbol]["chain"]
        if _chain:
            _tvl_path = DATA_DIR / f"defillama_{_chain.lower()}_tvl.parquet"
            _data["tvl"] = pl.read_parquet(_tvl_path) if _tvl_path.exists() else None
        else:
            _data["tvl"] = None
        _data["dex_volume"] = pl.read_parquet(DATA_DIR / "defillama_dex_volume.parquet")
        return _data

    asset_data = {_sym: load_asset(_sym) for _sym in ASSETS}

    return (
        ASSETS,
        DATA_DIR,
        asset_data,
        mo,
        np,
        pl,
        plt,
        sp_stats,
    )


@app.cell
def helpers(np, pl, plt, sp_stats):
    def scatter_regression(ax, x, y, xlabel, title, color="tab:blue"):
        _mask = np.isfinite(x) & np.isfinite(y)
        x, y = x[_mask], y[_mask]
        if len(x) < 5:
            ax.set_title(f"{title} (データ不足)")
            return None
        _slope, _intercept, _r, _p, _se = sp_stats.linregress(x, y)
        ax.scatter(x, y, alpha=0.35, s=12, color=color, edgecolors="none")
        _xl = np.linspace(x.min(), x.max(), 100)
        ax.plot(_xl, _slope * _xl + _intercept, color="red", linewidth=1.5, label="回帰直線")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("日次リターン")
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="lower right", fontsize=9)
        _sig = "***" if _p < 0.001 else "**" if _p < 0.01 else "*" if _p < 0.05 else ""
        ax.annotate(
            f"傾き = {_slope:.6f}\nr = {_r:.4f}\np = {_p:.4f} {_sig}",
            xy=(0.05, 0.95), xycoords="axes fraction", fontsize=10,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
        )
        return {"指標": title, "傾き": _slope, "r": _r, "R²": _r**2, "p値": _p, "SE": _se, "N": len(x)}

    def daily_ohlcv(df):
        return (
            df.sort("timestamp")
            .group_by_dynamic("timestamp", every="1d")
            .agg(pl.col("close").last())
            .with_columns(pl.col("close").pct_change().alias("return_1d"))
            .drop_nulls("return_1d")
            .with_columns(pl.col("timestamp").cast(pl.Date).alias("date"))
        )

    def daily_fr_sum(df):
        return (
            df.sort("timestamp")
            .group_by_dynamic("timestamp", every="1d")
            .agg(pl.col("funding_rate").sum().alias("daily_fr"))
            .with_columns(pl.col("timestamp").cast(pl.Date).alias("date"))
        )

    def daily_oi_change(df):
        return (
            df.sort("timestamp")
            .group_by_dynamic("timestamp", every="1d")
            .agg(pl.col("open_interest").last())
            .with_columns(pl.col("open_interest").pct_change().alias("oi_change"))
            .drop_nulls("oi_change")
            .with_columns(pl.col("timestamp").cast(pl.Date).alias("date"))
        )

    return daily_fr_sum, daily_oi_change, daily_ohlcv, scatter_regression


@app.cell
def asset_selector(ASSETS, mo):
    asset_dropdown = mo.ui.dropdown(
        options=list(ASSETS.keys()),
        value="SUI",
        label="分析対象通貨",
    )
    mo.vstack([
        mo.md("# デリバティブシグナル分析 (マルチアセット)"),
        mo.md("分析対象の通貨を選択してください:"),
        asset_dropdown,
    ])
    return (asset_dropdown,)


@app.cell
def price_overview(asset_dropdown, asset_data, daily_ohlcv, mo, plt):
    sym = asset_dropdown.value
    _d = asset_data[sym]
    _ohlcv = _d["binance_ohlcv"]

    _fig, _axes = plt.subplots(2, 1, figsize=(14, 7), gridspec_kw={"height_ratios": [3, 1]})
    _axes[0].plot(_ohlcv["timestamp"].to_list(), _ohlcv["close"].to_list(), linewidth=0.6, color="tab:orange")
    _axes[0].set_title(f"{sym}/USDT 終値 (Binance 1h)")
    _axes[0].set_ylabel("Price (USDT)")
    _axes[0].grid(True, alpha=0.3)
    _axes[1].bar(_ohlcv["timestamp"].to_list(), _ohlcv["volume"].to_list(), width=0.04, color="tab:blue", alpha=0.5)
    _axes[1].set_ylabel("Volume")
    _axes[1].grid(True, alpha=0.3)
    _fig.tight_layout()

    mo.vstack([mo.md(f"## {sym}/USDT 価格概要"), _fig])
    return (sym,)


@app.cell
def sec_fr_divergence(
    sym, asset_data, daily_fr_sum, daily_ohlcv, mo, np, pl, plt, scatter_regression,
):
    _d = asset_data[sym]
    _b_fr = daily_fr_sum(_d["binance_fr"]).select("date", pl.col("daily_fr").alias("binance_fr"))
    _y_fr = daily_fr_sum(_d["bybit_fr"]).select("date", pl.col("daily_fr").alias("bybit_fr"))
    _price = daily_ohlcv(_d["binance_ohlcv"]).select("date", "return_1d")
    _merged = (
        _b_fr.join(_y_fr, on="date", how="inner")
        .with_columns((pl.col("binance_fr") - pl.col("bybit_fr")).alias("fr_div"))
        .join(_price, on="date", how="inner")
        .sort("date")
        .with_columns(pl.col("return_1d").shift(-1).alias("next_return"))
        .drop_nulls("next_return")
    )
    _fig, _axes = plt.subplots(1, 2, figsize=(14, 5))
    _x = _merged["fr_div"].to_numpy()
    _r1 = scatter_regression(_axes[0], _x, _merged["return_1d"].to_numpy(),
        "FR乖離 (Binance - Bybit)", f"{sym}: FR乖離 vs 当日リターン")
    _r2 = scatter_regression(_axes[1], _x, _merged["next_return"].to_numpy(),
        "FR乖離 (Binance - Bybit)", f"{sym}: FR乖離 vs 翌日リターン", color="tab:orange")
    _fig.tight_layout()

    results_div = [_r for _r in [_r1, _r2] if _r]
    mo.vstack([
        mo.md("## 1. FR乖離シグナル (Binance - Bybit)"),
        mo.md("取引所間のFunding Rate差分が価格方向の予測力を持つか検証。"),
        _fig,
    ])
    return (results_div,)


@app.cell
def sec_fr_extreme(
    sym, asset_data, daily_fr_sum, daily_ohlcv, mo, np, pl, plt, scatter_regression,
):
    _d = asset_data[sym]
    _fr_d = daily_fr_sum(_d["binance_fr"]).select("date", "daily_fr")
    _price = daily_ohlcv(_d["binance_ohlcv"]).select("date", "return_1d")
    _merged = _fr_d.join(_price, on="date", how="inner").sort("date")
    _merged = _merged.with_columns(
        pl.col("return_1d").shift(-1).alias("next_return"),
        pl.col("return_1d").shift(-2).alias("return_2d_fwd"),
    ).drop_nulls("return_2d_fwd")

    _fr_vals = _merged["daily_fr"].to_numpy()
    _q10, _q90 = np.percentile(_fr_vals, [10, 90])

    _fig, _axes = plt.subplots(1, 3, figsize=(18, 5))
    _r1 = scatter_regression(_axes[0], _fr_vals, _merged["next_return"].to_numpy(),
        "日次FR合算", f"{sym}: FR vs 翌日リターン")

    _high = _merged.filter(pl.col("daily_fr") > _q90)
    _low = _merged.filter(pl.col("daily_fr") < _q10)
    _normal = _merged.filter((pl.col("daily_fr") >= _q10) & (pl.col("daily_fr") <= _q90))

    _cats = ["低FR\n(下位10%)", "通常", "高FR\n(上位10%)"]
    _means = [_low["next_return"].mean(), _normal["next_return"].mean(), _high["next_return"].mean()]
    _stds = [_low["next_return"].std(), _normal["next_return"].std(), _high["next_return"].std()]
    _counts = [_low.height, _normal.height, _high.height]

    _bars = _axes[1].bar(_cats, _means, yerr=_stds, capsize=5,
                         color=["tab:green", "gray", "tab:red"], alpha=0.7)
    _axes[1].set_ylabel("翌日平均リターン")
    _axes[1].set_title(f"{sym}: FRゾーン別 翌日リターン")
    _axes[1].axhline(0, color="black", linewidth=0.5)
    _axes[1].grid(True, alpha=0.3)
    for _bar, _c in zip(_bars, _counts):
        _axes[1].text(_bar.get_x() + _bar.get_width() / 2, _bar.get_height(),
                     f"n={_c}", ha="center", va="bottom", fontsize=9)

    _r2 = scatter_regression(_axes[2], _fr_vals, _merged["return_2d_fwd"].to_numpy(),
        "日次FR合算", f"{sym}: FR vs 2日後リターン", color="tab:purple")
    _fig.tight_layout()

    results_ext = [_r for _r in [_r1, _r2] if _r]
    mo.vstack([
        mo.md("## 2. FR極端値シグナル"),
        mo.md("高FR(上位10%) → ロング過熱 → 下落？ / 低FR(下位10%) → ショート過熱 → 上昇？"),
        _fig,
    ])
    return (results_ext,)


@app.cell
def sec_oi_fr(
    sym, asset_data, daily_fr_sum, daily_oi_change, daily_ohlcv,
    mo, np, pl, plt, scatter_regression,
):
    _d = asset_data[sym]
    _fr_d = daily_fr_sum(_d["binance_fr"]).select("date", "daily_fr")
    _oi_d = daily_oi_change(_d["bybit_oi"]).select("date", "oi_change")
    _price = daily_ohlcv(_d["binance_ohlcv"]).select("date", "return_1d")
    _merged = (
        _price.join(_fr_d, on="date", how="inner")
        .join(_oi_d, on="date", how="inner")
        .sort("date")
        .with_columns(
            pl.col("return_1d").shift(-1).alias("next_return"),
            (pl.col("oi_change") * pl.col("daily_fr").sign()).alias("oi_fr_score"),
        )
        .drop_nulls("next_return")
    )
    _fig, _axes = plt.subplots(1, 3, figsize=(18, 5))
    _r1 = scatter_regression(_axes[0], _merged["oi_change"].to_numpy(),
        _merged["next_return"].to_numpy(), "日次OI変化率", f"{sym}: OI変化率 vs 翌日リターン")
    _r2 = scatter_regression(_axes[1], _merged["oi_fr_score"].to_numpy(),
        _merged["next_return"].to_numpy(), "OI変化率 × FR符号",
        f"{sym}: 複合スコア vs 翌日リターン", color="tab:green")

    _quads = [
        ((pl.col("oi_change") > 0) & (pl.col("daily_fr") > 0), "OI↑FR+\n(強気過熱)"),
        ((pl.col("oi_change") > 0) & (pl.col("daily_fr") < 0), "OI↑FR-\n(逆張り蓄積)"),
        ((pl.col("oi_change") < 0) & (pl.col("daily_fr") > 0), "OI↓FR+\n(利確)"),
        ((pl.col("oi_change") < 0) & (pl.col("daily_fr") < 0), "OI↓FR-\n(投げ売り)"),
    ]
    _qlabels, _qmeans, _qcounts = [], [], []
    _qcolors = ["tab:red", "tab:blue", "tab:orange", "tab:green"]
    for _cond, _label in _quads:
        _sub = _merged.filter(_cond)
        _qlabels.append(_label)
        _qmeans.append(_sub["next_return"].mean() if _sub.height > 0 else 0)
        _qcounts.append(_sub.height)

    _bars = _axes[2].bar(_qlabels, _qmeans, color=_qcolors, alpha=0.7)
    _axes[2].set_ylabel("翌日平均リターン")
    _axes[2].set_title(f"{sym}: OI×FR 4象限 翌日リターン")
    _axes[2].axhline(0, color="black", linewidth=0.5)
    _axes[2].grid(True, alpha=0.3)
    for _bar, _c in zip(_bars, _qcounts):
        _axes[2].text(_bar.get_x() + _bar.get_width() / 2, _bar.get_height(),
                     f"n={_c}", ha="center", va="bottom", fontsize=9)
    _fig.tight_layout()

    results_oifr = [_r for _r in [_r1, _r2] if _r]
    mo.vstack([
        mo.md("## 3. OI急変 + FR方向の複合シグナル"),
        mo.md("OI変化 × FR方向の4象限分析。Bybit OI (1年分) を使用。"),
        _fig,
    ])
    return (results_oifr,)


@app.cell
def sec_ls(
    sym, asset_data, daily_ohlcv, mo, np, pl, plt, scatter_regression,
):
    _d = asset_data[sym]
    _ls_df = _d["ls_top"]

    if _ls_df is None or _ls_df.height < 10:
        results_ls = []
        mo.vstack([
            mo.md("## 4. L/S比率分析"),
            mo.md(f"**{sym}**: L/S比率データが不足しています。"),
        ])
    else:
        _ls_daily = (
            _ls_df.sort("timestamp")
            .with_columns(pl.col("timestamp").cast(pl.Date).alias("date"))
            .group_by("date").agg(pl.col("long_short_ratio").mean())
        )
        _price = daily_ohlcv(_d["binance_ohlcv"]).select("date", "return_1d")
        _merged = _ls_daily.join(_price, on="date", how="inner").sort("date")
        _merged = _merged.with_columns(
            pl.col("return_1d").shift(-1).alias("next_return"),
        ).drop_nulls("next_return")

        _fig, _axes = plt.subplots(1, 2, figsize=(14, 5))
        _lsv = _merged["long_short_ratio"].to_numpy()
        _r1 = scatter_regression(_axes[0], _lsv, _merged["return_1d"].to_numpy(),
            "L/S比率 (トップ)", f"{sym}: L/S比率 vs 当日リターン")
        _r2 = scatter_regression(_axes[1], _lsv, _merged["next_return"].to_numpy(),
            "L/S比率 (トップ)", f"{sym}: L/S比率 vs 翌日リターン", color="tab:orange")
        _fig.tight_layout()

        results_ls = [_r for _r in [_r1, _r2] if _r]
        mo.vstack([
            mo.md("## 4. L/S比率分析 (トップトレーダー, 30日分)"),
            _fig,
        ])
    return (results_ls,)


@app.cell
def sec_onchain(
    sym, asset_data, daily_ohlcv, mo, np, pl, plt, scatter_regression,
):
    _d = asset_data[sym]
    _tvl_df = _d["tvl"]
    _dex_df = _d["dex_volume"]
    _price = daily_ohlcv(_d["binance_ohlcv"]).select("date", "return_1d")

    _elements = [mo.md("## 5. オンチェーン指標 vs 価格")]
    _all_regs = []

    if _tvl_df is not None and _tvl_df.height > 10:
        _tvl_daily = (
            _tvl_df.sort("timestamp")
            .with_columns(
                pl.col("timestamp").cast(pl.Date).alias("date"),
                pl.col("tvl").pct_change().alias("tvl_change"),
            )
            .drop_nulls("tvl_change")
        )
        _mt = _tvl_daily.join(_price, on="date", how="inner").sort("date")
        _mt = _mt.with_columns(pl.col("return_1d").shift(-1).alias("next_return")).drop_nulls("next_return")

        _fig_t, _axes_t = plt.subplots(1, 2, figsize=(14, 5))
        _rt1 = scatter_regression(_axes_t[0], _mt["tvl_change"].to_numpy(),
            _mt["return_1d"].to_numpy(), "TVL変化率", f"{sym}: TVL変化率 vs 当日リターン")
        _rt2 = scatter_regression(_axes_t[1], _mt["tvl_change"].to_numpy(),
            _mt["next_return"].to_numpy(), "TVL変化率",
            f"{sym}: TVL変化率 vs 翌日リターン", color="tab:orange")
        _fig_t.tight_layout()
        _all_regs.extend([_r for _r in [_rt1, _rt2] if _r])
        _elements.append(mo.md(f"### {sym}チェーン TVL"))
        _elements.append(_fig_t)
    else:
        _elements.append(mo.md(f"**{sym}**: チェーンTVLデータなし（BTCはL1チェーンTVL非該当）"))

    _dex_daily = (
        _dex_df.sort("timestamp")
        .with_columns(
            pl.col("timestamp").cast(pl.Date).alias("date"),
            pl.col("dex_volume").pct_change().alias("dex_change"),
        )
        .drop_nulls("dex_change")
    )
    _md = _dex_daily.join(_price, on="date", how="inner").sort("date")
    _md = _md.with_columns(pl.col("return_1d").shift(-1).alias("next_return")).drop_nulls("next_return")

    _fig_d, _axes_d = plt.subplots(1, 2, figsize=(14, 5))
    _rd1 = scatter_regression(_axes_d[0], _md["dex_change"].to_numpy(),
        _md["return_1d"].to_numpy(), "DEX出来高変化率",
        f"{sym}: DEX出来高変化 vs 当日リターン", color="tab:green")
    _rd2 = scatter_regression(_axes_d[1], _md["dex_change"].to_numpy(),
        _md["next_return"].to_numpy(), "DEX出来高変化率",
        f"{sym}: DEX出来高変化 vs 翌日リターン", color="tab:purple")
    _fig_d.tight_layout()
    _all_regs.extend([_r for _r in [_rd1, _rd2] if _r])

    _elements.append(mo.md("### DEX出来高 (全チェーン合算)"))
    _elements.append(_fig_d)

    results_onchain = _all_regs
    mo.vstack(_elements)
    return (results_onchain,)


@app.cell
def sec_summary(
    sym, results_div, results_ext, results_oifr, results_ls, results_onchain,
    mo, pl,
):
    _all = results_div + results_ext + results_oifr + results_ls + results_onchain
    if _all:
        _df = pl.DataFrame(_all).sort("p値")
        _df = _df.with_columns([
            pl.col("傾き").round(6),
            pl.col("r").round(4),
            pl.col("R²").round(4),
            pl.col("p値").round(4),
            pl.col("SE").round(6),
        ])
        _sig = _df.filter(pl.col("p値") < 0.05).height

        mo.vstack([
            mo.md(f"## 総合サマリー ({sym})"),
            mo.md(f"全{_df.height}指標中、統計的に有意（p < 0.05）: **{_sig}個**"),
            mo.ui.table(_df.to_pandas()),
            mo.md("""
**判定基準**: p < 0.05 = 有意 / |r| > 0.3 = 中程度の相関 / R² > 0.1 = 変動の10%以上を説明

**注意**: L/S比率は30日分のみ。相関≠因果。市場レジーム変化で無効化されうる。
"""),
        ])
    return


if __name__ == "__main__":
    app.run()
