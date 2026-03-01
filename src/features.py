"""共通フィーチャーエンジニアリングモジュール.

全データソースから1h粒度の統合フィーチャーDataFrameを構築する。
分析ノートブック間でのフィーチャー算出ロジックの重複を排除。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl


def _norm_ts(df: pl.DataFrame) -> pl.DataFrame:
    """timestampをdatetime[ns, UTC]に統一."""
    ts = df.get_column("timestamp")
    if ts.dtype != pl.Datetime("ns", "UTC"):
        df = df.with_columns(
            pl.col("timestamp").cast(pl.Datetime("ns")).dt.replace_time_zone("UTC")
        )
    return df


def _to_naive(df: pl.DataFrame) -> pl.DataFrame:
    """timezone除去 + datetime[ns]にキャスト（join_asof用）."""
    return df.with_columns(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns"))
    )


def _build_ohlcv_features(ohlcv: pl.DataFrame) -> pl.DataFrame:
    """OHLCVデータからテクニカル特徴量を算出."""
    range_expr = pl.col("high") - pl.col("low")
    body_expr = (pl.col("close") - pl.col("open")).abs()

    df = ohlcv.with_columns([
        # リターン
        (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        (pl.col("close") / pl.col("close").shift(8) - 1).alias("ret_8h"),
        (pl.col("close") / pl.col("close").shift(24) - 1).alias("ret_24h"),
        # ボリューム比率
        (pl.col("volume") / pl.col("volume").rolling_mean(168)).alias("vol_ratio_7d"),
        (pl.col("volume") / pl.col("volume").rolling_mean(24)).alias("vol_ratio_24h"),
        # キャンドルスティック
        (range_expr / pl.col("open")).alias("range_pct"),
        (body_expr / range_expr).alias("body_ratio"),
        ((pl.col("close") - pl.col("low")) / range_expr).alias("close_position"),
        # signed volume
        (pl.col("volume") * (pl.col("close") - pl.col("open")).sign()).alias("signed_volume"),
    ])

    # rolling features requiring signed_volume
    df = df.with_columns([
        (pl.col("signed_volume").rolling_sum(8)
         / pl.col("volume").rolling_sum(8)).alias("vol_imbalance_8h"),
        (pl.col("ret_1h").rolling_std(24)).alias("volatility_24h"),
    ])

    return df.drop("signed_volume")


def _join_funding_rate(df: pl.DataFrame, fr: pl.DataFrame) -> pl.DataFrame:
    """FR (8h間隔) を1h足にjoin_asof."""
    fr_naive = fr.select(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
        pl.col("funding_rate"),
    ).sort("ts_join")

    df = df.with_columns(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
    )
    df = df.sort("ts_join").join_asof(fr_naive, on="ts_join", strategy="backward")

    # FR cumulative 24h (3 funding periods)
    df = df.with_columns(
        pl.col("funding_rate").rolling_sum(3).alias("fr_cum24h"),
    )
    return df.drop("ts_join")


def _join_oi(df: pl.DataFrame, oi: pl.DataFrame) -> pl.DataFrame:
    """OI (Bybit) を1h足にjoin_asof."""
    oi_naive = oi.select(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
        pl.col("open_interest"),
    ).sort("ts_join")

    df = df.with_columns(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
    )
    df = df.sort("ts_join").join_asof(oi_naive, on="ts_join", strategy="backward")

    # OI 4h変化率
    df = df.with_columns(
        pl.col("open_interest").pct_change(4).alias("oi_chg_4h"),
    )
    return df.drop(["ts_join", "open_interest"])


def _join_basis(df: pl.DataFrame, basis: pl.DataFrame) -> pl.DataFrame:
    """Basis 1hデータを結合."""
    basis_features = basis.select(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
        pl.col("basis_rate"),
    ).sort("ts_join")

    # basis の rolling 統計は join 後に計算
    df = df.with_columns(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
    )
    df = df.sort("ts_join").join_asof(basis_features, on="ts_join", strategy="backward")

    # basis 派生特徴量
    df = df.with_columns([
        (pl.col("basis_rate").rolling_mean(24)).alias("_basis_ma24"),
        (pl.col("basis_rate").rolling_std(24)).alias("_basis_std24"),
    ])
    df = df.with_columns([
        ((pl.col("basis_rate") - pl.col("_basis_ma24")) / pl.col("_basis_std24")).alias("basis_zscore_24h"),
        (pl.col("basis_rate") - pl.col("_basis_ma24")).alias("basis_ma_diff"),
    ])
    return df.drop(["ts_join", "_basis_ma24", "_basis_std24"])


def _join_fear_greed(df: pl.DataFrame, fg: pl.DataFrame) -> pl.DataFrame:
    """Fear & Greed Index (日次) をjoin_asof."""
    fg_naive = fg.with_columns(
        pl.col("value").cast(pl.Float64),
    ).with_columns(
        (pl.col("value") - pl.col("value").shift(7)).alias("fg_change_7d"),
    ).select(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
        pl.col("value").alias("fg_value"),
        pl.col("fg_change_7d"),
    ).sort("ts_join")

    df = df.with_columns(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
    )
    df = df.sort("ts_join").join_asof(fg_naive, on="ts_join", strategy="backward")
    return df.drop("ts_join")


def _join_stablecoin(df: pl.DataFrame, data_dir: Path) -> pl.DataFrame:
    """ステーブルコイン供給変化データをjoin_asof."""
    sc = pl.read_parquet(data_dir / "defillama_stablecoin_mcap.parquet")
    sc = sc.with_columns(
        pl.col("total_mcap_usd").cast(pl.Float64),
    ).with_columns(
        (pl.col("total_mcap_usd") / pl.col("total_mcap_usd").shift(7) - 1).alias("sc_mcap_chg_7d"),
    )
    sc_naive = sc.select(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
        pl.col("sc_mcap_chg_7d"),
    ).sort("ts_join")

    usdt = pl.read_parquet(data_dir / "defillama_usdt_mcap.parquet")
    usdt = usdt.with_columns(
        pl.col("circulating_usd").cast(pl.Float64),
    ).with_columns(
        (pl.col("circulating_usd") / pl.col("circulating_usd").shift(7) - 1).alias("usdt_chg_7d"),
    )
    usdt_naive = usdt.select(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
        pl.col("usdt_chg_7d"),
    ).sort("ts_join")

    df = df.with_columns(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
    )
    df = df.sort("ts_join").join_asof(sc_naive, on="ts_join", strategy="backward")
    df = df.sort("ts_join").join_asof(usdt_naive, on="ts_join", strategy="backward")
    return df.drop("ts_join")


def _add_time_features(df: pl.DataFrame) -> pl.DataFrame:
    """時刻・曜日の周期エンコード."""
    df = df.with_columns([
        pl.col("timestamp").dt.hour().alias("_hour"),
        pl.col("timestamp").dt.weekday().alias("_dow"),
    ])
    df = df.with_columns([
        (pl.col("_hour").cast(pl.Float64) * 2 * np.pi / 24).sin().alias("hour_sin"),
        (pl.col("_hour").cast(pl.Float64) * 2 * np.pi / 24).cos().alias("hour_cos"),
        (pl.col("_dow").cast(pl.Float64) * 2 * np.pi / 7).sin().alias("dow_sin"),
        (pl.col("_dow").cast(pl.Float64) * 2 * np.pi / 7).cos().alias("dow_cos"),
    ])
    return df.drop(["_hour", "_dow"])


def _add_target(df: pl.DataFrame, horizon: int = 8) -> pl.DataFrame:
    """将来リターンとその符号（二値ターゲット）を追加."""
    df = df.with_columns(
        (pl.col("close").shift(-horizon) / pl.col("close") - 1).alias(f"fwd_{horizon}h"),
    )
    df = df.with_columns(
        pl.when(pl.col(f"fwd_{horizon}h") > 0).then(1).otherwise(-1).alias("target"),
    )
    return df


# 最終出力に含める特徴量カラム
FEATURE_COLS = [
    # OHLCV
    "ret_1h", "ret_8h", "ret_24h",
    "vol_ratio_7d", "vol_ratio_24h",
    "vol_imbalance_8h",
    "range_pct", "body_ratio", "close_position",
    "volatility_24h",
    # Derivative
    "funding_rate", "fr_cum24h",
    "oi_chg_4h",
    # Basis
    "basis_rate", "basis_zscore_24h", "basis_ma_diff",
    # Sentiment
    "fg_value", "fg_change_7d",
    # Macro
    "sc_mcap_chg_7d", "usdt_chg_7d",
    # Time
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
]

# クロスアセット特徴量（ALTのみ）
CROSS_FEATURE_COLS = ["btc_basis_rate"]


def build_features(
    sym: str,
    data_dir: Path,
    *,
    btc_basis: pl.DataFrame | None = None,
    horizon: int = 8,
) -> pl.DataFrame:
    """1h粒度の統合フィーチャーDataFrameを構築.

    Parameters
    ----------
    sym : str
        トークンシンボル (BTC, ETH, SOL, SUI)
    data_dir : Path
        dataディレクトリのパス
    btc_basis : pl.DataFrame | None
        BTCのbasis_rateデータ（ALTトークンのクロスアセット特徴量用）
    horizon : int
        ターゲットの将来リターン時間 (デフォルト: 8h)

    Returns
    -------
    pl.DataFrame
        timestamp, 特徴量カラム群, fwd_{horizon}h, target を含むDataFrame
    """
    sym_lower = sym.lower()

    # OHLCV
    ohlcv = _norm_ts(
        pl.read_parquet(data_dir / f"binance_{sym_lower}usdt_1h.parquet")
    ).sort("timestamp")
    df = _build_ohlcv_features(ohlcv)

    # Funding Rate (Bybit)
    fr = _norm_ts(
        pl.read_parquet(data_dir / f"bybit_{sym_lower}usdt_funding_rate.parquet")
    ).sort("timestamp")
    df = _join_funding_rate(df, fr)

    # Open Interest (Bybit)
    oi = _norm_ts(
        pl.read_parquet(data_dir / f"bybit_{sym_lower}usdt_open_interest.parquet")
    ).sort("timestamp")
    df = _join_oi(df, oi)

    # Basis
    basis = _norm_ts(
        pl.read_parquet(data_dir / f"binance_{sym_lower}usdt_basis_1h.parquet")
    ).sort("timestamp")
    df = _join_basis(df, basis)

    # Fear & Greed
    fg = pl.read_parquet(data_dir / "fear_greed_index.parquet")
    df = _join_fear_greed(df, fg)

    # Stablecoin
    df = _join_stablecoin(df, data_dir)

    # Cross-asset (ALTのみ)
    if sym.upper() != "BTC" and btc_basis is not None:
        btc_naive = btc_basis.select(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
            pl.col("basis_rate").alias("btc_basis_rate"),
        ).sort("ts_join")
        df = df.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("ns")).alias("ts_join"),
        )
        df = df.sort("ts_join").join_asof(btc_naive, on="ts_join", strategy="backward")
        df = df.drop("ts_join")

    # Time features
    df = _add_time_features(df)

    # Target
    df = _add_target(df, horizon=horizon)

    # fwd_NhのNaN行を除去
    df = df.drop_nulls(f"fwd_{horizon}h")

    return df


def build_all_features(
    data_dir: Path,
    tokens: list[str] | None = None,
    horizon: int = 8,
) -> dict[str, pl.DataFrame]:
    """全トークンのフィーチャーDataFrameを構築.

    Returns
    -------
    dict[str, pl.DataFrame]
        {sym: features_df} の辞書
    """
    if tokens is None:
        tokens = ["BTC", "ETH", "SOL", "SUI"]

    # BTCのbasisデータを先に読んでクロスアセット用に保持
    btc_basis = _norm_ts(
        pl.read_parquet(data_dir / "binance_btcusdt_basis_1h.parquet")
    ).sort("timestamp")

    results = {}
    for sym in tokens:
        results[sym] = build_features(
            sym,
            data_dir,
            btc_basis=btc_basis if sym != "BTC" else None,
            horizon=horizon,
        )
    return results


def get_feature_cols(sym: str) -> list[str]:
    """特徴量カラム名のリストを返す."""
    cols = list(FEATURE_COLS)
    if sym.upper() != "BTC":
        cols.extend(CROSS_FEATURE_COLS)
    return cols
