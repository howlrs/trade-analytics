import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


@app.cell
def setup():
    from pathlib import Path

    import marimo as mo
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    import numpy as np
    import polars as pl

    # matplotlib 日本語フォント設定（.ttc は自動検出されないため明示登録）
    import matplotlib.font_manager as fm

    fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mpl.rcParams["font.family"] = "Noto Sans CJK JP"
    mpl.rcParams["axes.unicode_minus"] = False

    DATA_DIR = Path(__file__).parent.parent.parent / "data"

    # --- OHLCV ---
    binance_btc_ohlcv = pl.read_parquet(DATA_DIR / "binance_btcusdt_1h.parquet")
    binance_eth_ohlcv = pl.read_parquet(DATA_DIR / "binance_ethusdt_1h.parquet")
    bybit_btc_ohlcv = pl.read_parquet(DATA_DIR / "bybit_btcusdt_1h.parquet")
    bybit_eth_ohlcv = pl.read_parquet(DATA_DIR / "bybit_ethusdt_1h.parquet")

    # --- Funding Rate ---
    binance_btc_fr = pl.read_parquet(DATA_DIR / "binance_btcusdt_funding_rate.parquet")
    binance_eth_fr = pl.read_parquet(DATA_DIR / "binance_ethusdt_funding_rate.parquet")
    bybit_btc_fr = pl.read_parquet(DATA_DIR / "bybit_btcusdt_funding_rate.parquet")
    bybit_eth_fr = pl.read_parquet(DATA_DIR / "bybit_ethusdt_funding_rate.parquet")

    # --- Open Interest ---
    binance_btc_oi = pl.read_parquet(DATA_DIR / "binance_btcusdt_open_interest.parquet")
    binance_eth_oi = pl.read_parquet(DATA_DIR / "binance_ethusdt_open_interest.parquet")
    bybit_btc_oi = pl.read_parquet(DATA_DIR / "bybit_btcusdt_open_interest.parquet")
    bybit_eth_oi = pl.read_parquet(DATA_DIR / "bybit_ethusdt_open_interest.parquet")

    return (
        DATA_DIR,
        Path,
        binance_btc_fr,
        binance_btc_ohlcv,
        binance_btc_oi,
        binance_eth_fr,
        binance_eth_ohlcv,
        binance_eth_oi,
        bybit_btc_fr,
        bybit_btc_ohlcv,
        bybit_btc_oi,
        bybit_eth_fr,
        bybit_eth_ohlcv,
        bybit_eth_oi,
        mo,
        np,
        pl,
        plt,
    )


@app.cell
def data_overview(
    binance_btc_fr,
    binance_btc_ohlcv,
    binance_btc_oi,
    binance_eth_fr,
    binance_eth_ohlcv,
    binance_eth_oi,
    bybit_btc_fr,
    bybit_btc_ohlcv,
    bybit_btc_oi,
    bybit_eth_fr,
    bybit_eth_ohlcv,
    bybit_eth_oi,
    mo,
    pl,
):
    datasets = {
        "Binance BTC OHLCV": binance_btc_ohlcv,
        "Binance ETH OHLCV": binance_eth_ohlcv,
        "Bybit BTC OHLCV": bybit_btc_ohlcv,
        "Bybit ETH OHLCV": bybit_eth_ohlcv,
        "Binance BTC FR": binance_btc_fr,
        "Binance ETH FR": binance_eth_fr,
        "Bybit BTC FR": bybit_btc_fr,
        "Bybit ETH FR": bybit_eth_fr,
        "Binance BTC OI": binance_btc_oi,
        "Binance ETH OI": binance_eth_oi,
        "Bybit BTC OI": bybit_btc_oi,
        "Bybit ETH OI": bybit_eth_oi,
    }

    summary_rows = []
    for name, df in datasets.items():
        ts = df["timestamp"]
        summary_rows.append(
            {
                "データセット": name,
                "レコード数": df.height,
                "開始日": str(ts.min()),
                "終了日": str(ts.max()),
                "カラム": ", ".join(df.columns),
            }
        )

    summary_df = pl.DataFrame(summary_rows)

    mo.vstack(
        [
            mo.md("## 1. データ概要"),
            mo.md("全12ファイル（BTC/ETH x Binance/Bybit x OHLCV/FR/OI）の概要:"),
            mo.ui.table(summary_df.to_pandas()),
        ]
    )
    return


@app.cell
def price_chart(binance_btc_ohlcv, binance_eth_ohlcv, mo, plt):
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    # BTC
    axes[0].plot(
        binance_btc_ohlcv["timestamp"].to_list(),
        binance_btc_ohlcv["close"].to_list(),
        linewidth=0.7,
        color="tab:orange",
    )
    axes[0].set_title("BTC/USDT 終値 (Binance 1h)")
    axes[0].set_ylabel("Price (USDT)")
    axes[0].grid(True, alpha=0.3)

    # ETH
    axes[1].plot(
        binance_eth_ohlcv["timestamp"].to_list(),
        binance_eth_ohlcv["close"].to_list(),
        linewidth=0.7,
        color="tab:blue",
    )
    axes[1].set_title("ETH/USDT 終値 (Binance 1h)")
    axes[1].set_ylabel("Price (USDT)")
    axes[1].grid(True, alpha=0.3)

    fig.tight_layout()

    mo.vstack(
        [
            mo.md("## 2. 価格チャート"),
            mo.md("Binance 1h OHLCVデータの終値推移"),
            fig,
        ]
    )
    return


@app.cell
def funding_rate_analysis(
    binance_btc_fr,
    binance_eth_fr,
    bybit_btc_fr,
    bybit_eth_fr,
    mo,
    pl,
    plt,
):
    def _plot_fr(binance_fr, bybit_fr, symbol):
        """Funding Rate の推移と累積FRをプロット"""
        fig, axes = plt.subplots(2, 1, figsize=(14, 5), sharex=True)

        # FR推移
        axes[0].plot(
            binance_fr["timestamp"].to_list(),
            binance_fr["funding_rate"].to_list(),
            linewidth=0.6,
            label="Binance",
            alpha=0.8,
        )
        axes[0].plot(
            bybit_fr["timestamp"].to_list(),
            bybit_fr["funding_rate"].to_list(),
            linewidth=0.6,
            label="Bybit",
            alpha=0.8,
        )
        axes[0].axhline(y=0, color="gray", linestyle="--", linewidth=0.5)
        axes[0].set_title(f"{symbol} Funding Rate")
        axes[0].set_ylabel("FR")
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # 累積FR
        binance_cum = binance_fr.with_columns(
            pl.col("funding_rate").cum_sum().alias("cum_fr")
        )
        bybit_cum = bybit_fr.with_columns(
            pl.col("funding_rate").cum_sum().alias("cum_fr")
        )
        axes[1].plot(
            binance_cum["timestamp"].to_list(),
            binance_cum["cum_fr"].to_list(),
            linewidth=0.8,
            label="Binance",
        )
        axes[1].plot(
            bybit_cum["timestamp"].to_list(),
            bybit_cum["cum_fr"].to_list(),
            linewidth=0.8,
            label="Bybit",
        )
        axes[1].set_title(f"{symbol} 累積 Funding Rate")
        axes[1].set_ylabel("Cumulative FR")
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        fig.tight_layout()
        return fig

    fig_btc_fr = _plot_fr(binance_btc_fr, bybit_btc_fr, "BTC")
    fig_eth_fr = _plot_fr(binance_eth_fr, bybit_eth_fr, "ETH")

    # FR統計
    def _fr_stats(fr_df, name):
        fr = fr_df["funding_rate"]
        return {
            "データセット": name,
            "平均FR": round(fr.mean(), 8),
            "中央値FR": round(fr.median(), 8),
            "最大FR": round(fr.max(), 8),
            "最小FR": round(fr.min(), 8),
            "正FR比率": round((fr > 0).mean(), 4),
        }

    fr_stats = pl.DataFrame(
        [
            _fr_stats(binance_btc_fr, "Binance BTC"),
            _fr_stats(bybit_btc_fr, "Bybit BTC"),
            _fr_stats(binance_eth_fr, "Binance ETH"),
            _fr_stats(bybit_eth_fr, "Bybit ETH"),
        ]
    )

    mo.vstack(
        [
            mo.md("## 3. Funding Rate 分析"),
            mo.md("### BTC Funding Rate"),
            fig_btc_fr,
            mo.md("### ETH Funding Rate"),
            fig_eth_fr,
            mo.md("### FR 統計サマリー"),
            mo.ui.table(fr_stats.to_pandas()),
        ]
    )
    return


@app.cell
def open_interest_analysis(
    binance_btc_oi,
    binance_btc_ohlcv,
    binance_eth_oi,
    binance_eth_ohlcv,
    bybit_btc_oi,
    bybit_eth_oi,
    mo,
    plt,
):
    def _plot_oi(binance_oi, bybit_oi, ohlcv, symbol):
        """OI推移と価格の重ね合わせ"""
        fig, ax1 = plt.subplots(figsize=(14, 5))

        # OI（左軸）
        ax1.plot(
            binance_oi["timestamp"].to_list(),
            binance_oi["open_interest"].to_list(),
            linewidth=0.7,
            label="Binance OI",
            color="tab:blue",
            alpha=0.8,
        )
        ax1.plot(
            bybit_oi["timestamp"].to_list(),
            bybit_oi["open_interest"].to_list(),
            linewidth=0.7,
            label="Bybit OI",
            color="tab:cyan",
            alpha=0.8,
        )
        ax1.set_ylabel("Open Interest (contracts)")
        ax1.set_title(f"{symbol} Open Interest vs Price")
        ax1.legend(loc="upper left")
        ax1.grid(True, alpha=0.3)

        # 価格（右軸）
        ax2 = ax1.twinx()
        ax2.plot(
            ohlcv["timestamp"].to_list(),
            ohlcv["close"].to_list(),
            linewidth=0.6,
            label="Price",
            color="tab:orange",
            alpha=0.6,
        )
        ax2.set_ylabel("Price (USDT)")
        ax2.legend(loc="upper right")

        fig.tight_layout()
        return fig

    fig_btc_oi = _plot_oi(
        binance_btc_oi, bybit_btc_oi, binance_btc_ohlcv, "BTC"
    )
    fig_eth_oi = _plot_oi(
        binance_eth_oi, bybit_eth_oi, binance_eth_ohlcv, "ETH"
    )

    mo.vstack(
        [
            mo.md("## 4. Open Interest 分析"),
            mo.md("### BTC Open Interest"),
            fig_btc_oi,
            mo.md("### ETH Open Interest"),
            fig_eth_oi,
        ]
    )
    return


@app.cell
def correlation_analysis(
    binance_btc_fr,
    binance_btc_ohlcv,
    binance_btc_oi,
    binance_eth_fr,
    binance_eth_ohlcv,
    binance_eth_oi,
    mo,
    np,
    pl,
    plt,
):
    from scipy import stats as sp_stats

    def _scatter_with_regression(ax, x, y, xlabel, title, color="tab:blue"):
        """散布図 + 回帰直線を描画し、回帰統計を返す"""
        slope, intercept, r_value, p_value, std_err = sp_stats.linregress(x, y)
        ax.scatter(x, y, alpha=0.4, s=10, color=color)
        x_line = np.linspace(x.min(), x.max(), 100)
        ax.plot(x_line, slope * x_line + intercept, color="red", linewidth=1.5, label="回帰直線")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("日次リターン")
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="lower right", fontsize=9)
        ax.annotate(
            f"傾き = {slope:.6f}\nr = {r_value:.4f}\np = {p_value:.4f}",
            xy=(0.05, 0.95),
            xycoords="axes fraction",
            fontsize=10,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
        )
        return {"傾き": slope, "r": r_value, "p値": p_value, "標準誤差": std_err}

    def _compute_correlation(ohlcv, fr, oi, symbol):
        """価格リターン vs FR, OI変化率の相関を計算・可視化"""
        # 日次リサンプル: OHLCVから日次リターン
        daily_price = (
            ohlcv.sort("timestamp")
            .group_by_dynamic("timestamp", every="1d")
            .agg(pl.col("close").last())
            .with_columns(
                pl.col("close").pct_change().alias("return_1d")
            )
            .drop_nulls("return_1d")
        )

        # FR: 日次合算
        daily_fr = (
            fr.sort("timestamp")
            .group_by_dynamic("timestamp", every="1d")
            .agg(pl.col("funding_rate").sum().alias("daily_fr"))
        )

        # OI: 日次変化率
        daily_oi = (
            oi.sort("timestamp")
            .group_by_dynamic("timestamp", every="1d")
            .agg(pl.col("open_interest").last())
            .with_columns(
                pl.col("open_interest").pct_change().alias("oi_change")
            )
            .drop_nulls("oi_change")
        )

        # timestamp を日付に丸めて結合
        daily_price = daily_price.with_columns(
            pl.col("timestamp").cast(pl.Date).alias("date")
        )
        daily_fr = daily_fr.with_columns(
            pl.col("timestamp").cast(pl.Date).alias("date")
        )
        daily_oi = daily_oi.with_columns(
            pl.col("timestamp").cast(pl.Date).alias("date")
        )

        merged = (
            daily_price.select("date", "return_1d")
            .join(daily_fr.select("date", "daily_fr"), on="date", how="inner")
            .join(daily_oi.select("date", "oi_change"), on="date", how="inner")
            .drop_nulls()
        )

        if merged.height < 5:
            return None, None, None, fig

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        ret = merged["return_1d"].to_numpy()
        fr_vals = merged["daily_fr"].to_numpy()
        oi_vals = merged["oi_change"].to_numpy()

        reg_fr = _scatter_with_regression(
            axes[0], fr_vals, ret,
            "日次 Funding Rate", f"{symbol}: リターン vs Funding Rate",
        )
        reg_oi = _scatter_with_regression(
            axes[1], oi_vals, ret,
            "日次 OI 変化率", f"{symbol}: リターン vs OI変化率",
            color="tab:green",
        )

        fig.suptitle(f"{symbol} 簡易相関分析 (Binance, 日次)", fontsize=13)
        fig.tight_layout()

        return reg_fr, reg_oi, fig

    reg_fr_btc, reg_oi_btc, fig_btc_corr = _compute_correlation(
        binance_btc_ohlcv, binance_btc_fr, binance_btc_oi, "BTC"
    )
    reg_fr_eth, reg_oi_eth, fig_eth_corr = _compute_correlation(
        binance_eth_ohlcv, binance_eth_fr, binance_eth_oi, "ETH"
    )

    def _row(symbol, reg_fr, reg_oi):
        row = {"通貨": symbol}
        if reg_fr:
            row["FR傾き"] = round(reg_fr["傾き"], 6)
            row["FR r"] = round(reg_fr["r"], 4)
            row["FR p値"] = round(reg_fr["p値"], 4)
        if reg_oi:
            row["OI傾き"] = round(reg_oi["傾き"], 6)
            row["OI r"] = round(reg_oi["r"], 4)
            row["OI p値"] = round(reg_oi["p値"], 4)
        return row

    corr_summary = pl.DataFrame(
        [
            _row("BTC", reg_fr_btc, reg_oi_btc),
            _row("ETH", reg_fr_eth, reg_oi_eth),
        ]
    )

    mo.vstack(
        [
            mo.md("## 5. 簡易相関分析"),
            mo.md("Binanceデータを日次集計し、価格リターンとFR/OI変化率の回帰分析。傾きが大きく p値が小さいほど指標として有用。"),
            mo.md("### BTC"),
            fig_btc_corr,
            mo.md("### ETH"),
            fig_eth_corr,
            mo.md("### 回帰統計サマリー"),
            mo.ui.table(corr_summary.to_pandas()),
        ]
    )
    return


@app.cell
def summary(mo):
    mo.md(
        """
        ## 6. まとめ・メモ

        ### データ状況
        - BTC/ETH x Binance/Bybit の OHLCV(1h), Funding Rate(8h), Open Interest を取得済み
        - Bybit OI には `open_interest_value` カラムがない点に注意

        ### 次のステップ
        - FR乖離（Binance - Bybit）を使ったアービトラージシグナルの検討
        - OI急増 + FR偏りを組み合わせたポジション過熱シグナル
        - ボリュームプロファイル分析
        - 複数タイムフレーム（4h, 1d）での分析
        """
    )
    return


if __name__ == "__main__":
    app.run()
