import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


# ============================================================
# Cell 1: setup — imports, 定数
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
    FR_CAP = 0.0001  # 0.01% cap
    FWD_PERIODS_FR = [1, 3, 6]  # 8h, 24h, 48h
    FWD_PERIODS_OI = [1, 2, 6]  # 4h, 8h, 24h

    return (
        COLORS,
        DATA_DIR,
        FR_CAP,
        FWD_PERIODS_FR,
        FWD_PERIODS_OI,
        TOKENS,
        TRAIN_END,
        mo,
        np,
        pl,
        plt,
        sp_stats,
    )


# ============================================================
# Cell 2: data_load — 全トークンデータ統合
# ============================================================
@app.cell
def data_load(DATA_DIR, FR_CAP, FWD_PERIODS_FR, FWD_PERIODS_OI, TOKENS, TRAIN_END, np, pl):
    def _norm_ts(df):
        """SOL/SUIのdatetime[ms]をdatetime[ns, UTC]に統一"""
        _ts = df.get_column("timestamp")
        if _ts.dtype != pl.Datetime("ns", "UTC"):
            df = df.with_columns(
                pl.col("timestamp")
                .cast(pl.Datetime("ns"))
                .dt.replace_time_zone("UTC")
            )
        return df

    # --- FR: Bybit FR + OHLCV close/volume を join_asof ---
    fr_data = {}
    for _sym in TOKENS:
        _fr = _norm_ts(
            pl.read_parquet(DATA_DIR / f"bybit_{_sym.lower()}usdt_funding_rate.parquet")
        ).sort("timestamp")

        _ohlcv = _norm_ts(
            pl.read_parquet(DATA_DIR / f"binance_{_sym.lower()}usdt_1h.parquet")
        ).sort("timestamp")

        # join_asof: naive datetime に統一
        _fr_naive = _fr.with_columns(pl.col("timestamp").dt.replace_time_zone(None))
        _ohlcv_naive = _ohlcv.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None)
        ).select(["timestamp", "close", "volume"])

        _merged = _fr_naive.join_asof(
            _ohlcv_naive, on="timestamp", strategy="backward"
        )

        # 24h累積FR (rolling_sum over 3 x 8h periods)
        _merged = _merged.with_columns(
            pl.col("funding_rate").rolling_sum(3).alias("fr_cum24h"),
        )

        # 将来リターン (FR間隔 = 8h)
        for _p in FWD_PERIODS_FR:
            _merged = _merged.with_columns(
                (pl.col("close").shift(-_p) / pl.col("close") - 1).alias(f"fwd_{_p}"),
            )

        # volume ratio
        _merged = _merged.with_columns(
            (pl.col("volume") / pl.col("volume").rolling_mean(21)).alias("vol_ratio_7d"),
        )

        # train/test split
        _merged = _merged.with_columns(
            pl.when(pl.col("timestamp") < pl.lit(TRAIN_END).str.to_datetime())
            .then(pl.lit("train"))
            .otherwise(pl.lit("test"))
            .alias("split"),
        )

        fr_data[_sym] = _merged

    # --- OI: Bybit OI + 4h resample OHLCV ---
    oi_data = {}
    for _sym in TOKENS:
        _oi = _norm_ts(
            pl.read_parquet(DATA_DIR / f"bybit_{_sym.lower()}usdt_open_interest.parquet")
        ).sort("timestamp")

        _ohlcv = _norm_ts(
            pl.read_parquet(DATA_DIR / f"binance_{_sym.lower()}usdt_1h.parquet")
        ).sort("timestamp")

        # 4h resample OHLCV
        _ohlcv_4h = (
            _ohlcv.with_columns(pl.col("timestamp").dt.replace_time_zone(None))
            .group_by_dynamic("timestamp", every="4h")
            .agg([
                pl.col("open").first().alias("open"),
                pl.col("high").max().alias("high"),
                pl.col("low").min().alias("low"),
                pl.col("close").last().alias("close"),
                pl.col("volume").sum().alias("volume"),
            ])
            .sort("timestamp")
        )

        _oi_naive = _oi.with_columns(pl.col("timestamp").dt.replace_time_zone(None))

        _merged = _oi_naive.join(
            _ohlcv_4h, on="timestamp", how="inner"
        ).sort("timestamp")

        # OI変化率
        _merged = _merged.with_columns(
            (pl.col("open_interest") / pl.col("open_interest").shift(1) - 1).alias("oi_chg"),
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("price_chg"),
        )

        # 将来リターン (OI間隔 = 4h)
        for _p in FWD_PERIODS_OI:
            _merged = _merged.with_columns(
                (pl.col("close").shift(-_p) / pl.col("close") - 1).alias(f"fwd_{_p}"),
            )

        # volume ratio
        _merged = _merged.with_columns(
            (pl.col("volume") / pl.col("volume").rolling_mean(42)).alias("vol_ratio_7d"),
        )

        # train/test split
        _merged = _merged.with_columns(
            pl.when(pl.col("timestamp") < pl.lit(TRAIN_END).str.to_datetime())
            .then(pl.lit("train"))
            .otherwise(pl.lit("test"))
            .alias("split"),
        )

        oi_data[_sym] = _merged

    # --- 閾値算出 (Train期間のみ, リーク防止) ---
    thresholds = {}
    for _sym in TOKENS:
        _fr_train = fr_data[_sym].filter(pl.col("split") == "train")
        _oi_train = oi_data[_sym].filter(pl.col("split") == "train")

        _fr_vals = _fr_train.get_column("funding_rate").drop_nulls().to_numpy()
        _fr_cum_vals = _fr_train.get_column("fr_cum24h").drop_nulls().to_numpy()
        _oi_vals = _oi_train.get_column("oi_chg").drop_nulls().to_numpy()

        thresholds[_sym] = {
            "fr_neg_2s": float(np.mean(_fr_vals) - 2 * np.std(_fr_vals)),
            "fr_pos_cap": FR_CAP,
            "fr_cum_neg_2s": float(np.mean(_fr_cum_vals) - 2 * np.std(_fr_cum_vals)),
            "fr_cum_pos_2s": float(np.mean(_fr_cum_vals) + 2 * np.std(_fr_cum_vals)),
            "oi_neg_2s": float(np.mean(_oi_vals) - 2 * np.std(_oi_vals)),
            "oi_pos_2s": float(np.mean(_oi_vals) + 2 * np.std(_oi_vals)),
        }

    return fr_data, oi_data, thresholds


# ============================================================
# Cell 3: helpers — イベント分析関数
# ============================================================
@app.cell
def helpers(np, pl, plt, sp_stats):
    def event_returns(df, mask_col, fwd_col, split=None):
        """イベント時の将来リターンを抽出"""
        _df = df.filter(pl.col(mask_col))
        if split is not None:
            _df = _df.filter(pl.col("split") == split)
        _vals = _df.get_column(fwd_col).drop_nulls().to_numpy()
        return _vals

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

    def plot_event_dist(axes_row, rets_dict, horizon_label, colors):
        """トークン別のリターン分布ヒストグラム (1行4列)"""
        for _i, (_sym, _rets) in enumerate(rets_dict.items()):
            _ax = axes_row[_i]
            if len(_rets) < 3:
                _ax.set_title(f"{_sym} (N<3)")
                _ax.text(0.5, 0.5, "サンプル不足", transform=_ax.transAxes, ha="center")
                continue
            _ax.hist(_rets * 10000, bins=25, color=colors[_sym], alpha=0.7, edgecolor="white")
            _ax.axvline(0, color="black", linewidth=0.8)
            _mean = np.mean(_rets) * 10000
            _ax.axvline(_mean, color="red", linewidth=1.2, linestyle="--")
            _t, _p = sp_stats.ttest_1samp(_rets, 0)
            _sig = "***" if _p < 0.001 else "**" if _p < 0.01 else "*" if _p < 0.05 else ""
            _note = f"N={len(_rets)}"
            if len(_rets) < 10:
                _note += " ⚠サンプル不足"
            _ax.set_title(f"{_sym} {horizon_label}")
            _ax.annotate(
                f"平均={_mean:.1f}bps {_sig}\n{_note}",
                xy=(0.05, 0.95), xycoords="axes fraction", fontsize=8,
                va="top", bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.5),
            )
            _ax.set_xlabel("リターン (bps)")

    def plot_event_bar(ax, stats_list, labels, title):
        """イベント統計の棒グラフ"""
        _means = [s["mean_bps"] for s in stats_list]
        _colors_bar = ["tab:green" if m > 0 else "tab:red" for m in _means]
        _bars = ax.bar(labels, _means, color=_colors_bar, alpha=0.7)
        ax.axhline(0, color="black", linewidth=0.5)
        ax.set_ylabel("平均リターン (bps)")
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        for _bar, _s in zip(_bars, stats_list):
            _sig = "***" if _s["p"] < 0.001 else "**" if _s["p"] < 0.01 else "*" if _s["p"] < 0.05 else ""
            _label = f"N={_s['N']}{_sig}"
            ax.annotate(
                _label,
                xy=(_bar.get_x() + _bar.get_width() / 2, _bar.get_height()),
                ha="center", va="bottom" if _bar.get_height() >= 0 else "top",
                fontsize=7,
            )

    def sharpe_ratio(pnl, periods_per_year=365 * 3):
        """年率Sharpe。8h保有 = 1日3回"""
        if len(pnl) == 0 or np.std(pnl) == 0:
            return 0.0
        return float(np.mean(pnl) / np.std(pnl) * np.sqrt(periods_per_year))

    def max_drawdown(cum_pnl):
        if len(cum_pnl) == 0:
            return 0.0
        _peak = np.maximum.accumulate(cum_pnl)
        _dd = cum_pnl - _peak
        return float(np.min(_dd))

    def win_rate(pnl):
        if len(pnl) == 0:
            return 0.0
        return float(np.mean(pnl > 0))

    return event_returns, event_stats, max_drawdown, plot_event_bar, plot_event_dist, sharpe_ratio, win_rate


# ============================================================
# Cell 4: title_cell — タイトル + 閾値テーブル
# ============================================================
@app.cell
def title_cell(TOKENS, mo, pl, thresholds):
    _rows = []
    for _sym in TOKENS:
        _th = thresholds[_sym]
        _rows.append({
            "トークン": _sym,
            "FR -2σ": f"{_th['fr_neg_2s']*100:.4f}%",
            "FR cap": f"{_th['fr_pos_cap']*100:.4f}%",
            "FR累積24h -2σ": f"{_th['fr_cum_neg_2s']*100:.4f}%",
            "OI -2σ": f"{_th['oi_neg_2s']*100:.2f}%",
            "OI +2σ": f"{_th['oi_pos_2s']*100:.2f}%",
        })
    _th_df = pl.DataFrame(_rows)

    mo.vstack([
        mo.md("# デリバティブ主導シグナル分析"),
        mo.md("""
**方針転換**: OHLCV線形アルファはウォークフォワードで崩壊 → FR極端値・OI急変をプライマリシグナル（イベント駆動型）に切り替え。

- **FR Long**: FR < -2σ（ショート過熱 → 反転期待）
- **FR Short**: FR ≥ cap (0.01%)（ロング過熱 → 反転期待）
- **OI Surge+**: OI変化率 > +2σ（レバレッジ急増 → 不安定化）
- **OI Surge-**: OI変化率 < -2σ（清算連鎖 → 反転期待）
- Train: 〜2025-09-01 / Test: 2025-09-01〜
"""),
        mo.md("### 閾値テーブル（Train期間から算出）"),
        mo.ui.table(_th_df.to_pandas()),
    ])


# ============================================================
# Cell 5: sec_fr_extreme — FR(8h)極端値イベント分析
# ============================================================
@app.cell
def sec_fr_extreme(COLORS, FR_CAP, FWD_PERIODS_FR, TOKENS, event_returns, event_stats, fr_data, mo, np, pl, plot_event_dist, plt, thresholds):
    # イベントフラグ追加
    _fr_flagged = {}
    for _sym in TOKENS:
        _df = fr_data[_sym].with_columns([
            (pl.col("funding_rate") < thresholds[_sym]["fr_neg_2s"]).alias("fr_long"),
            (pl.col("funding_rate") >= FR_CAP).alias("fr_short"),
        ])
        _fr_flagged[_sym] = _df

    # Long: FR < -2σ
    _fig_long, _axes_long = plt.subplots(len(FWD_PERIODS_FR), 4, figsize=(18, 4 * len(FWD_PERIODS_FR)))
    for _ri, _p in enumerate(FWD_PERIODS_FR):
        _rets_dict = {}
        for _sym in TOKENS:
            _rets_dict[_sym] = event_returns(_fr_flagged[_sym], "fr_long", f"fwd_{_p}")
        _hrs = _p * 8
        plot_event_dist(_axes_long[_ri], _rets_dict, f"FR Long {_hrs}h", COLORS)
    _fig_long.suptitle("FR < -2σ → Long シグナル: リターン分布", fontsize=14, y=1.02)
    _fig_long.tight_layout()

    # Short: FR >= cap
    _fig_short, _axes_short = plt.subplots(len(FWD_PERIODS_FR), 4, figsize=(18, 4 * len(FWD_PERIODS_FR)))
    for _ri, _p in enumerate(FWD_PERIODS_FR):
        _rets_dict = {}
        for _sym in TOKENS:
            _rets_dict[_sym] = event_returns(_fr_flagged[_sym], "fr_short", f"fwd_{_p}")
        _hrs = _p * 8
        plot_event_dist(_axes_short[_ri], _rets_dict, f"FR Short {_hrs}h", COLORS)
    _fig_short.suptitle("FR ≥ cap (0.01%) → Short シグナル: リターン分布", fontsize=14, y=1.02)
    _fig_short.tight_layout()

    # 統計テーブル
    _stats_rows = []
    for _sym in TOKENS:
        for _p in FWD_PERIODS_FR:
            _hrs = _p * 8
            for _side, _col in [("Long", "fr_long"), ("Short", "fr_short")]:
                for _split in ["train", "test"]:
                    _rets = event_returns(_fr_flagged[_sym], _col, f"fwd_{_p}", _split)
                    _s = event_stats(_rets)
                    _stats_rows.append({
                        "トークン": _sym, "シグナル": _side, "ホライズン": f"{_hrs}h",
                        "Split": _split, **_s,
                    })
    fr_event_stats_df = pl.DataFrame(_stats_rows)

    mo.vstack([
        mo.md("## 1. FR(8h) 極端値イベント分析"),
        mo.md("**Long**: FR < -2σ（ショート過熱 → 反転期待） / **Short**: FR ≥ 0.01% cap（ロング過熱）"),
        _fig_long,
        _fig_short,
        mo.md("### FR イベント統計"),
        mo.ui.table(fr_event_stats_df.to_pandas()),
    ])
    return (fr_event_stats_df,)


# ============================================================
# Cell 6: sec_fr_cum24h — FR累積24h極端値
# ============================================================
@app.cell
def sec_fr_cum24h(COLORS, FWD_PERIODS_FR, TOKENS, event_returns, event_stats, fr_data, mo, np, pl, plot_event_dist, plt, thresholds):
    _fr_cum_flagged = {}
    for _sym in TOKENS:
        _df = fr_data[_sym].with_columns([
            (pl.col("fr_cum24h") < thresholds[_sym]["fr_cum_neg_2s"]).alias("fr_cum_long"),
            (pl.col("fr_cum24h") > thresholds[_sym]["fr_cum_pos_2s"]).alias("fr_cum_short"),
        ])
        _fr_cum_flagged[_sym] = _df

    _fig, _axes = plt.subplots(len(FWD_PERIODS_FR), 4, figsize=(18, 4 * len(FWD_PERIODS_FR)))
    for _ri, _p in enumerate(FWD_PERIODS_FR):
        _rets_dict = {}
        for _sym in TOKENS:
            _rets_dict[_sym] = event_returns(_fr_cum_flagged[_sym], "fr_cum_long", f"fwd_{_p}")
        _hrs = _p * 8
        plot_event_dist(_axes[_ri], _rets_dict, f"FR累積Long {_hrs}h", COLORS)
    _fig.suptitle("FR累積24h < -2σ → Long シグナル: リターン分布", fontsize=14, y=1.02)
    _fig.tight_layout()

    _stats_rows = []
    for _sym in TOKENS:
        for _p in FWD_PERIODS_FR:
            _hrs = _p * 8
            for _side, _col in [("CumLong", "fr_cum_long"), ("CumShort", "fr_cum_short")]:
                for _split in ["train", "test"]:
                    _rets = event_returns(_fr_cum_flagged[_sym], _col, f"fwd_{_p}", _split)
                    _s = event_stats(_rets)
                    _stats_rows.append({
                        "トークン": _sym, "シグナル": _side, "ホライズン": f"{_hrs}h",
                        "Split": _split, **_s,
                    })
    fr_cum_stats_df = pl.DataFrame(_stats_rows)

    mo.vstack([
        mo.md("## 2. FR累積24h 極端値イベント分析"),
        mo.md("3×8h合計が-2σを下回る = 持続的ショート過熱 → ロングスクイーズ前兆？"),
        _fig,
        mo.md("### FR累積24h イベント統計"),
        mo.ui.table(fr_cum_stats_df.to_pandas()),
    ])
    return (fr_cum_stats_df,)


# ============================================================
# Cell 7: sec_oi_surge — OI急変イベント分析
# ============================================================
@app.cell
def sec_oi_surge(COLORS, FWD_PERIODS_OI, TOKENS, event_returns, event_stats, mo, np, oi_data, pl, plot_event_dist, plt, thresholds):
    _oi_flagged = {}
    for _sym in TOKENS:
        _df = oi_data[_sym].with_columns([
            (pl.col("oi_chg") > thresholds[_sym]["oi_pos_2s"]).alias("oi_surge_up"),
            (pl.col("oi_chg") < thresholds[_sym]["oi_neg_2s"]).alias("oi_surge_dn"),
        ])
        _oi_flagged[_sym] = _df

    # OI surge up
    _fig_up, _axes_up = plt.subplots(len(FWD_PERIODS_OI), 4, figsize=(18, 4 * len(FWD_PERIODS_OI)))
    for _ri, _p in enumerate(FWD_PERIODS_OI):
        _rets_dict = {}
        for _sym in TOKENS:
            _rets_dict[_sym] = event_returns(_oi_flagged[_sym], "oi_surge_up", f"fwd_{_p}")
        _hrs = _p * 4
        plot_event_dist(_axes_up[_ri], _rets_dict, f"OI Surge+ {_hrs}h", COLORS)
    _fig_up.suptitle("OI > +2σ (急増): リターン分布", fontsize=14, y=1.02)
    _fig_up.tight_layout()

    # OI surge down
    _fig_dn, _axes_dn = plt.subplots(len(FWD_PERIODS_OI), 4, figsize=(18, 4 * len(FWD_PERIODS_OI)))
    for _ri, _p in enumerate(FWD_PERIODS_OI):
        _rets_dict = {}
        for _sym in TOKENS:
            _rets_dict[_sym] = event_returns(_oi_flagged[_sym], "oi_surge_dn", f"fwd_{_p}")
        _hrs = _p * 4
        plot_event_dist(_axes_dn[_ri], _rets_dict, f"OI Surge- {_hrs}h", COLORS)
    _fig_dn.suptitle("OI < -2σ (急減): リターン分布", fontsize=14, y=1.02)
    _fig_dn.tight_layout()

    _stats_rows = []
    for _sym in TOKENS:
        for _p in FWD_PERIODS_OI:
            _hrs = _p * 4
            for _side, _col in [("OI Surge+", "oi_surge_up"), ("OI Surge-", "oi_surge_dn")]:
                for _split in ["train", "test"]:
                    _rets = event_returns(_oi_flagged[_sym], _col, f"fwd_{_p}", _split)
                    _s = event_stats(_rets)
                    _stats_rows.append({
                        "トークン": _sym, "シグナル": _side, "ホライズン": f"{_hrs}h",
                        "Split": _split, **_s,
                    })
    oi_event_stats_df = pl.DataFrame(_stats_rows)

    mo.vstack([
        mo.md("## 3. OI急変イベント分析"),
        mo.md("**OI Surge+**: OI変化率 > +2σ（レバレッジ急増 → 不安定化） / **OI Surge-**: OI変化率 < -2σ（清算連鎖 → 反転期待）"),
        _fig_up,
        _fig_dn,
        mo.md("### OI イベント統計"),
        mo.ui.table(oi_event_stats_df.to_pandas()),
    ])
    return (oi_event_stats_df,)


# ============================================================
# Cell 8: sec_oi_price_divergence — OI-価格乖離
# ============================================================
@app.cell
def sec_oi_price_divergence(COLORS, FWD_PERIODS_OI, TOKENS, event_returns, event_stats, mo, np, oi_data, pl, plot_event_dist, plt, thresholds):
    _div_flagged = {}
    for _sym in TOKENS:
        _df = oi_data[_sym].with_columns([
            # 蓄積: OI↑ + Price↓
            ((pl.col("oi_chg") > 0.02) & (pl.col("price_chg") < -0.005)).alias("div_accumulate"),
            # 分散: OI↓ + Price↑
            ((pl.col("oi_chg") < -0.02) & (pl.col("price_chg") > 0.005)).alias("div_distribute"),
        ])
        _div_flagged[_sym] = _df

    # Accumulation divergence
    _fig_acc, _axes_acc = plt.subplots(len(FWD_PERIODS_OI), 4, figsize=(18, 4 * len(FWD_PERIODS_OI)))
    for _ri, _p in enumerate(FWD_PERIODS_OI):
        _rets_dict = {}
        for _sym in TOKENS:
            _rets_dict[_sym] = event_returns(_div_flagged[_sym], "div_accumulate", f"fwd_{_p}")
        _hrs = _p * 4
        plot_event_dist(_axes_acc[_ri], _rets_dict, f"蓄積乖離 {_hrs}h", COLORS)
    _fig_acc.suptitle("OI↑ + Price↓ (蓄積乖離): リターン分布", fontsize=14, y=1.02)
    _fig_acc.tight_layout()

    # Distribution divergence
    _fig_dist, _axes_dist = plt.subplots(len(FWD_PERIODS_OI), 4, figsize=(18, 4 * len(FWD_PERIODS_OI)))
    for _ri, _p in enumerate(FWD_PERIODS_OI):
        _rets_dict = {}
        for _sym in TOKENS:
            _rets_dict[_sym] = event_returns(_div_flagged[_sym], "div_distribute", f"fwd_{_p}")
        _hrs = _p * 4
        plot_event_dist(_axes_dist[_ri], _rets_dict, f"分散乖離 {_hrs}h", COLORS)
    _fig_dist.suptitle("OI↓ + Price↑ (分散乖離): リターン分布", fontsize=14, y=1.02)
    _fig_dist.tight_layout()

    _stats_rows = []
    for _sym in TOKENS:
        for _p in FWD_PERIODS_OI:
            _hrs = _p * 4
            for _side, _col in [("蓄積乖離", "div_accumulate"), ("分散乖離", "div_distribute")]:
                for _split in ["train", "test"]:
                    _rets = event_returns(_div_flagged[_sym], _col, f"fwd_{_p}", _split)
                    _s = event_stats(_rets)
                    _stats_rows.append({
                        "トークン": _sym, "シグナル": _side, "ホライズン": f"{_hrs}h",
                        "Split": _split, **_s,
                    })
    _div_stats_df = pl.DataFrame(_stats_rows)

    mo.vstack([
        mo.md("## 4. OI-価格乖離分析"),
        mo.md("""
- **蓄積乖離**: OI > +2% かつ Price < -0.5%（価格下落中にOI増加 → ショート蓄積 or 逆張りロング）
- **分散乖離**: OI < -2% かつ Price > +0.5%（価格上昇中にOI減少 → ポジション解消）
"""),
        _fig_acc,
        _fig_dist,
        mo.md("### OI-価格乖離 イベント統計"),
        mo.ui.table(_div_stats_df.to_pandas()),
    ])


# ============================================================
# Cell 9: sec_ohlcv_filter — OHLCVフィルタ効果
# ============================================================
@app.cell
def sec_ohlcv_filter(COLORS, TOKENS, event_stats, fr_data, mo, np, pl, plt, thresholds):
    _filter_rows = []
    for _sym in TOKENS:
        _df = fr_data[_sym].with_columns([
            (pl.col("funding_rate") < thresholds[_sym]["fr_neg_2s"]).alias("fr_long"),
        ])
        for _split in ["train", "test"]:
            _split_df = _df.filter(pl.col("split") == _split)

            # フィルタなし
            _rets_nofilt = _split_df.filter(pl.col("fr_long")).get_column("fwd_3").drop_nulls().to_numpy()
            _s_nofilt = event_stats(_rets_nofilt)

            # vol_ratio_7d > 1.5 フィルタ
            _rets_filt = (
                _split_df.filter(pl.col("fr_long") & (pl.col("vol_ratio_7d") > 1.5))
                .get_column("fwd_3").drop_nulls().to_numpy()
            )
            _s_filt = event_stats(_rets_filt)

            _filter_rows.append({
                "トークン": _sym, "Split": _split, "フィルタ": "なし",
                **_s_nofilt,
            })
            _filter_rows.append({
                "トークン": _sym, "Split": _split, "フィルタ": "vol>1.5x",
                **_s_filt,
            })
    _filter_df = pl.DataFrame(_filter_rows)

    # 可視化: フィルタ有無の比較棒グラフ
    _fig, _axes = plt.subplots(1, 4, figsize=(18, 5))
    for _i, _sym in enumerate(TOKENS):
        _test_data = _filter_df.filter(
            (pl.col("トークン") == _sym) & (pl.col("Split") == "test")
        )
        _labels = _test_data.get_column("フィルタ").to_list()
        _means = _test_data.get_column("mean_bps").to_list()
        _colors_bar = ["tab:green" if m > 0 else "tab:red" for m in _means]
        _axes[_i].bar(_labels, _means, color=_colors_bar, alpha=0.7)
        _axes[_i].axhline(0, color="black", linewidth=0.5)
        _axes[_i].set_title(f"{_sym} FR Long 24h (Test)")
        _axes[_i].set_ylabel("平均リターン (bps)")
        _axes[_i].grid(True, alpha=0.3)
    _fig.suptitle("OHLCVフィルタ効果: vol_ratio_7d > 1.5", fontsize=14)
    _fig.tight_layout()

    mo.vstack([
        mo.md("## 5. OHLCVフィルタ効果検証"),
        mo.md("FR Long (24h hold) にボリュームフィルタ `vol_ratio_7d > 1.5` を適用した場合の比較。"),
        _fig,
        mo.ui.table(_filter_df.to_pandas()),
    ])


# ============================================================
# Cell 10: sec_signal_combination — FR×OIレジーム分析
# ============================================================
@app.cell
def sec_signal_combination(FR_CAP, TOKENS, event_stats, fr_data, mo, np, oi_data, pl, plt, thresholds):
    # FR zone を OIデータのタイムスタンプに結合
    # OI=4h、FR=8h なので FR を join_asof で紐付け

    _regime_results = []
    for _sym in TOKENS:
        _oi = oi_data[_sym]

        # fr_data からFRだけ取得して join_asof
        _fr_for_join = fr_data[_sym].select(["timestamp", "funding_rate"]).sort("timestamp")
        _oi_sorted = _oi.sort("timestamp")

        _merged = _oi_sorted.join_asof(
            _fr_for_join, on="timestamp", strategy="backward"
        )

        # FR zone
        _th = thresholds[_sym]
        _merged = _merged.with_columns(
            pl.when(pl.col("funding_rate") < _th["fr_neg_2s"])
            .then(pl.lit("neg"))
            .when(pl.col("funding_rate") >= FR_CAP)
            .then(pl.lit("cap"))
            .otherwise(pl.lit("neutral"))
            .alias("fr_zone"),
        )

        # OI zone
        _merged = _merged.with_columns(
            pl.when(pl.col("oi_chg") > _th["oi_pos_2s"])
            .then(pl.lit("up"))
            .when(pl.col("oi_chg") < _th["oi_neg_2s"])
            .then(pl.lit("dn"))
            .otherwise(pl.lit("flat"))
            .alias("oi_zone"),
        )

        _merged = _merged.with_columns(
            (pl.col("fr_zone") + "_" + pl.col("oi_zone")).alias("regime"),
        )

        for _regime in ["neg_up", "neg_flat", "neg_dn", "neutral_up", "neutral_flat", "neutral_dn", "cap_up", "cap_flat", "cap_dn"]:
            for _split in ["train", "test"]:
                _sub = _merged.filter(
                    (pl.col("regime") == _regime) & (pl.col("split") == _split)
                )
                _rets = _sub.get_column("fwd_2").drop_nulls().to_numpy()  # 8h fwd
                _s = event_stats(_rets)
                _regime_results.append({
                    "トークン": _sym, "レジーム": _regime, "Split": _split, **_s,
                })

    _regime_df = pl.DataFrame(_regime_results)

    # ヒートマップ: Test期間の平均リターン
    _fr_zones = ["neg", "neutral", "cap"]
    _oi_zones = ["up", "flat", "dn"]
    _fig, _axes = plt.subplots(1, 4, figsize=(20, 5))
    for _i, _sym in enumerate(TOKENS):
        _test_data = _regime_df.filter(
            (pl.col("トークン") == _sym) & (pl.col("Split") == "test")
        )
        _matrix = np.zeros((3, 3))
        for _fi, _fz in enumerate(_fr_zones):
            for _oi_i, _oz in enumerate(_oi_zones):
                _regime_name = f"{_fz}_{_oz}"
                _row = _test_data.filter(pl.col("レジーム") == _regime_name)
                if len(_row) > 0:
                    _matrix[_fi, _oi_i] = _row.get_column("mean_bps").to_list()[0]

        _im = _axes[_i].imshow(_matrix, cmap="RdYlGn", aspect="auto", vmin=-100, vmax=100)
        _axes[_i].set_xticks(range(3))
        _axes[_i].set_xticklabels(["OI↑", "OI flat", "OI↓"])
        _axes[_i].set_yticks(range(3))
        _axes[_i].set_yticklabels(["FR neg", "FR neutral", "FR cap"])
        _axes[_i].set_title(f"{_sym} (Test)")
        for _fi in range(3):
            for _oi_i in range(3):
                _axes[_i].text(_oi_i, _fi, f"{_matrix[_fi, _oi_i]:.0f}",
                               ha="center", va="center", fontsize=9)
    _fig.colorbar(_im, ax=_axes.tolist(), label="平均リターン (bps)", shrink=0.8)
    _fig.suptitle("FR×OI 9レジーム 平均リターン (8h fwd, Test)", fontsize=14)
    _fig.tight_layout()

    mo.vstack([
        mo.md("## 6. FR×OI レジーム分析"),
        mo.md("FR zone (neg/neutral/cap) × OI zone (up/flat/dn) の9レジーム別リターン。"),
        _fig,
        mo.md("### レジーム統計"),
        mo.ui.table(_regime_df.to_pandas()),
    ])


# ============================================================
# Cell 11: sec_walkforward — Train vs Test検証
# ============================================================
@app.cell
def sec_walkforward(TOKENS, fr_data, fr_event_stats_df, mo, np, oi_data, oi_event_stats_df, pl, plt, sp_stats, thresholds):
    # 5シグナル × 4トークン × Train/Test の比較
    _signals = ["FR Long", "FR Short", "FR CumLong", "OI Surge+", "OI Surge-"]

    # Train/Test比較テーブルを構築
    _wf_rows = []
    for _sym in TOKENS:
        # FR Long 24h
        for _split in ["train", "test"]:
            _fr_df = fr_data[_sym].with_columns(
                (pl.col("funding_rate") < thresholds[_sym]["fr_neg_2s"]).alias("sig"),
            )
            _rets = _fr_df.filter((pl.col("sig")) & (pl.col("split") == _split)).get_column("fwd_3").drop_nulls().to_numpy()
            _mean = float(np.mean(_rets) * 10000) if len(_rets) > 0 else 0
            _n = len(_rets)
            _t, _p = sp_stats.ttest_1samp(_rets, 0) if len(_rets) >= 3 else (0, 1)
            _wf_rows.append({"トークン": _sym, "シグナル": "FR Long 24h", "Split": _split,
                             "N": _n, "mean_bps": round(_mean, 2), "p": round(float(_p), 4)})

        # FR Short 24h
        for _split in ["train", "test"]:
            _fr_df = fr_data[_sym].with_columns(
                (pl.col("funding_rate") >= thresholds[_sym]["fr_pos_cap"]).alias("sig"),
            )
            _rets = _fr_df.filter((pl.col("sig")) & (pl.col("split") == _split)).get_column("fwd_3").drop_nulls().to_numpy()
            _mean = float(np.mean(_rets) * 10000) if len(_rets) > 0 else 0
            _n = len(_rets)
            _t, _p = sp_stats.ttest_1samp(_rets, 0) if len(_rets) >= 3 else (0, 1)
            _wf_rows.append({"トークン": _sym, "シグナル": "FR Short 24h", "Split": _split,
                             "N": _n, "mean_bps": round(_mean, 2), "p": round(float(_p), 4)})

        # FR CumLong 24h
        for _split in ["train", "test"]:
            _fr_df = fr_data[_sym].with_columns(
                (pl.col("fr_cum24h") < thresholds[_sym]["fr_cum_neg_2s"]).alias("sig"),
            )
            _rets = _fr_df.filter((pl.col("sig")) & (pl.col("split") == _split)).get_column("fwd_3").drop_nulls().to_numpy()
            _mean = float(np.mean(_rets) * 10000) if len(_rets) > 0 else 0
            _n = len(_rets)
            _t, _p = sp_stats.ttest_1samp(_rets, 0) if len(_rets) >= 3 else (0, 1)
            _wf_rows.append({"トークン": _sym, "シグナル": "FR CumLong 24h", "Split": _split,
                             "N": _n, "mean_bps": round(_mean, 2), "p": round(float(_p), 4)})

        # OI Surge+ 8h
        for _split in ["train", "test"]:
            _oi_df = oi_data[_sym].with_columns(
                (pl.col("oi_chg") > thresholds[_sym]["oi_pos_2s"]).alias("sig"),
            )
            _rets = _oi_df.filter((pl.col("sig")) & (pl.col("split") == _split)).get_column("fwd_2").drop_nulls().to_numpy()
            _mean = float(np.mean(_rets) * 10000) if len(_rets) > 0 else 0
            _n = len(_rets)
            _t, _p = sp_stats.ttest_1samp(_rets, 0) if len(_rets) >= 3 else (0, 1)
            _wf_rows.append({"トークン": _sym, "シグナル": "OI Surge+ 8h", "Split": _split,
                             "N": _n, "mean_bps": round(_mean, 2), "p": round(float(_p), 4)})

        # OI Surge- 8h
        for _split in ["train", "test"]:
            _oi_df = oi_data[_sym].with_columns(
                (pl.col("oi_chg") < thresholds[_sym]["oi_neg_2s"]).alias("sig"),
            )
            _rets = _oi_df.filter((pl.col("sig")) & (pl.col("split") == _split)).get_column("fwd_2").drop_nulls().to_numpy()
            _mean = float(np.mean(_rets) * 10000) if len(_rets) > 0 else 0
            _n = len(_rets)
            _t, _p = sp_stats.ttest_1samp(_rets, 0) if len(_rets) >= 3 else (0, 1)
            _wf_rows.append({"トークン": _sym, "シグナル": "OI Surge- 8h", "Split": _split,
                             "N": _n, "mean_bps": round(_mean, 2), "p": round(float(_p), 4)})

    wf_results_df = pl.DataFrame(_wf_rows)

    # Train vs Test 散布図
    _fig, _ax = plt.subplots(1, 1, figsize=(10, 8))
    for _sym in TOKENS:
        _train = wf_results_df.filter(
            (pl.col("トークン") == _sym) & (pl.col("Split") == "train")
        ).get_column("mean_bps").to_numpy()
        _test = wf_results_df.filter(
            (pl.col("トークン") == _sym) & (pl.col("Split") == "test")
        ).get_column("mean_bps").to_numpy()
        _ax.scatter(_train, _test, label=_sym, s=80, alpha=0.7)
    _ax.axhline(0, color="gray", linewidth=0.5)
    _ax.axvline(0, color="gray", linewidth=0.5)
    _lim = max(abs(_ax.get_xlim()[0]), abs(_ax.get_xlim()[1]), abs(_ax.get_ylim()[0]), abs(_ax.get_ylim()[1])) * 1.1
    _ax.plot([-_lim, _lim], [-_lim, _lim], "k--", alpha=0.3, label="y=x")
    _ax.set_xlabel("Train mean_bps")
    _ax.set_ylabel("Test mean_bps")
    _ax.set_title("Train vs Test: 5シグナル × 4トークン")
    _ax.legend()
    _ax.grid(True, alpha=0.3)
    _fig.tight_layout()

    mo.vstack([
        mo.md("## 7. ウォークフォワード検証 (Train vs Test)"),
        mo.md("5シグナル × 4トークンの Train/Test リターン比較。y=x 線上にあればOverfitなし。"),
        _fig,
        mo.md("### 全シグナル Train/Test 統計"),
        mo.ui.table(wf_results_df.to_pandas()),
    ])
    return (wf_results_df,)


# ============================================================
# Cell 12: sec_equity_curve — イベント型エクイティカーブ
# ============================================================
@app.cell
def sec_equity_curve(COLORS, TOKENS, fr_data, max_drawdown, mo, np, oi_data, pl, plt, sharpe_ratio, thresholds, win_rate):
    _fig, _axes = plt.subplots(2, 1, figsize=(16, 10), sharex=False)

    _perf_rows = []

    # FR Long (24h hold = fwd_3)
    _ax_fr = _axes[0]
    for _sym in TOKENS:
        _df = fr_data[_sym].with_columns(
            (pl.col("funding_rate") < thresholds[_sym]["fr_neg_2s"]).alias("sig"),
        ).sort("timestamp")
        _signals = _df.get_column("sig").to_numpy().astype(float)
        _fwd = _df.get_column("fwd_3").to_numpy()
        _mask = np.isfinite(_fwd)
        _pnl = (_signals * _fwd)[_mask]
        _cum = np.cumsum(_pnl)
        _ax_fr.plot(range(len(_cum)), _cum * 10000, label=_sym, color=COLORS[_sym], linewidth=1.2)

        _split_idx = _df.with_row_index().filter(pl.col("split") == "test").get_column("index").to_list()
        _test_start = _split_idx[0] if _split_idx else len(_cum)
        _test_pnl = (_signals * _fwd)[_mask][_test_start:]
        _test_cum = np.cumsum(_test_pnl) if len(_test_pnl) > 0 else np.array([0])

        _perf_rows.append({
            "シグナル": "FR Long 24h", "トークン": _sym,
            "Sharpe (全体)": round(sharpe_ratio(_pnl), 2),
            "MaxDD (全体)": round(max_drawdown(_cum) * 10000, 1),
            "勝率 (全体)": round(win_rate(_pnl[_pnl != 0]), 3),
            "Sharpe (Test)": round(sharpe_ratio(_test_pnl), 2),
            "MaxDD (Test)": round(max_drawdown(_test_cum) * 10000, 1),
        })

    _ax_fr.set_title("FR Long (24h hold) エクイティカーブ")
    _ax_fr.set_ylabel("累積リターン (bps)")
    _ax_fr.legend()
    _ax_fr.grid(True, alpha=0.3)
    _ax_fr.axhline(0, color="black", linewidth=0.5)

    # OI Surge+ Short (8h hold = fwd_2, short because surge → instability)
    _ax_oi = _axes[1]
    for _sym in TOKENS:
        _df = oi_data[_sym].with_columns(
            (pl.col("oi_chg") > thresholds[_sym]["oi_pos_2s"]).alias("sig"),
        ).sort("timestamp")
        _signals_arr = _df.get_column("sig").to_numpy().astype(float) * -1  # short
        _fwd = _df.get_column("fwd_2").to_numpy()
        _mask = np.isfinite(_fwd)
        _pnl = (_signals_arr * _fwd)[_mask]
        _cum = np.cumsum(_pnl)
        _ax_oi.plot(range(len(_cum)), _cum * 10000, label=_sym, color=COLORS[_sym], linewidth=1.2)

        _split_idx = _df.with_row_index().filter(pl.col("split") == "test").get_column("index").to_list()
        _test_start = _split_idx[0] if _split_idx else len(_cum)
        _test_pnl = (_signals_arr * _fwd)[_mask][_test_start:]
        _test_cum = np.cumsum(_test_pnl) if len(_test_pnl) > 0 else np.array([0])

        _perf_rows.append({
            "シグナル": "OI Surge+ Short 8h", "トークン": _sym,
            "Sharpe (全体)": round(sharpe_ratio(_pnl, 365 * 6), 2),  # 4h = 6/day
            "MaxDD (全体)": round(max_drawdown(_cum) * 10000, 1),
            "勝率 (全体)": round(win_rate(_pnl[_pnl != 0]), 3),
            "Sharpe (Test)": round(sharpe_ratio(_test_pnl, 365 * 6), 2),
            "MaxDD (Test)": round(max_drawdown(_test_cum) * 10000, 1),
        })

    _ax_oi.set_title("OI Surge+ Short (8h hold) エクイティカーブ")
    _ax_oi.set_ylabel("累積リターン (bps)")
    _ax_oi.legend()
    _ax_oi.grid(True, alpha=0.3)
    _ax_oi.axhline(0, color="black", linewidth=0.5)

    _fig.tight_layout()

    _perf_df = pl.DataFrame(_perf_rows)

    mo.vstack([
        mo.md("## 8. イベント型エクイティカーブ"),
        mo.md("""
- **FR Long**: FR < -2σ 時に24h保有（ロング）
- **OI Surge+ Short**: OI > +2σ 時に8h保有（ショート）
"""),
        _fig,
        mo.md("### パフォーマンス指標"),
        mo.ui.table(_perf_df.to_pandas()),
    ])


# ============================================================
# Cell 13: sec_summary — 総合サマリー + 次ステップ
# ============================================================
@app.cell
def sec_summary(fr_event_stats_df, mo, oi_event_stats_df, pl, wf_results_df):
    # Test期間で有意なシグナルを抽出
    _sig_test = wf_results_df.filter(
        (pl.col("Split") == "test") & (pl.col("p") < 0.1)
    ).sort("p")

    _n_significant = len(_sig_test)
    _n_total = len(wf_results_df.filter(pl.col("Split") == "test"))

    # Train→Test持続性
    _train_means = wf_results_df.filter(pl.col("Split") == "train").get_column("mean_bps").to_numpy()
    _test_means = wf_results_df.filter(pl.col("Split") == "test").get_column("mean_bps").to_numpy()
    _same_sign = sum((_train_means * _test_means) > 0)
    _total_pairs = len(_train_means)

    mo.vstack([
        mo.md("## 9. 総合サマリー"),
        mo.md(f"""
### 結果概要

- **Test期間で p<0.1 のシグナル**: {_n_significant} / {_n_total}
- **Train→Test 符号一致率**: {_same_sign}/{_total_pairs} ({_same_sign/_total_pairs*100:.0f}%)

### シグナル評価

| シグナル | 評価基準 | 備考 |
|---------|---------|------|
| FR Long (< -2σ) | ショート過熱からの反転 | BTC/SUIはTestイベント少（N<10） |
| FR Short (≥ cap) | ロング過熱からの反転 | cap固定のため全トークン共通閾値 |
| FR累積24h | 持続的ショート過熱 | 単発FRより安定的な可能性 |
| OI Surge+ | レバレッジ急増 → 不安定化 | Short方向のシグナル |
| OI Surge- | 清算連鎖 → 反転 | Long方向のシグナル |

### 次ステップ

1. **有意シグナルの組合せ最適化**: 複数シグナルの同時発火ルール策定
2. **保有期間の最適化**: 8h/24h/48h 以外の中間値も検討
3. **リスク管理**: ストップロス・テイクプロフィットの導入
4. **リアルタイム監視**: FR/OI閾値超過のアラート機構
5. **OI-価格乖離の閾値チューニング**: 固定値 → パーセンタイルベース
"""),
        mo.md("### Test期間 有意シグナル (p<0.1)"),
        mo.ui.table(_sig_test.to_pandas()) if _n_significant > 0 else mo.md("*有意なシグナルなし*"),
    ])


if __name__ == "__main__":
    app.run()
