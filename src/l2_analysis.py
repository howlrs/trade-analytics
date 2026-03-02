"""L2 order book analysis module for Drift SOL-PERP.

Provides reusable analysis functions for L2 snapshots, trades, and candles.
Used by both the pipeline script and the marimo notebook.

Functions:
    load_l2_data        - Load L2 parquet + derive columns
    load_trades         - Load & dedup trade parquets (recent + historical)
    load_candles_and_fr - Load 1h candles + funding rates
    compute_spread_distribution       - Spread stats by hour, regime
    compute_oracle_divergence_dynamics - ACF, half-life, distribution
    compute_book_shape                - vAMM/dlob shares, depth by level
    estimate_fill_probability         - Fill rate by level, spread, hour
    measure_adverse_selection         - Post-trade oracle moves
    recommend_parameters              - MM parameter recommendations
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import polars as pl


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_l2_data(data_dir: Path) -> pl.DataFrame:
    """Load L2 snapshot parquet and add derived columns.

    Expected file: data_dir / "drift_solusdc_l2_snapshots.parquet"

    Derived columns:
        hour          - UTC hour (0-23)
        vamm_share    - fraction of best bid/ask from vAMM source
        depth_imbalance - (bid_depth - ask_depth) / (bid_depth + ask_depth)
        rvol_60       - rolling realized vol over 60 snapshots (~5min)
        regime        - low/mid/high based on rvol_60 terciles
    """
    path = data_dir / "drift_solusdc_l2_snapshots.parquet"
    df = pl.read_parquet(path)

    # Ensure sorted by timestamp
    df = df.sort("timestamp")

    # Hour of day
    df = df.with_columns(
        pl.col("timestamp").dt.hour().alias("hour"),
    )

    # vAMM share: 1 if best level is vamm, 0 if dlob, 0.5 if mixed
    df = df.with_columns(
        ((pl.col("drift_bid1_source") == "vamm").cast(pl.Float64) * 0.5
         + (pl.col("drift_ask1_source") == "vamm").cast(pl.Float64) * 0.5)
        .alias("vamm_share"),
    )

    # Depth imbalance: sum of bid sizes vs ask sizes (levels 1-5)
    bid_cols = [f"drift_bid{i}_size" for i in range(1, 6)]
    ask_cols = [f"drift_ask{i}_size" for i in range(1, 6)]
    df = df.with_columns(
        pl.sum_horizontal(*[pl.col(c).fill_null(0.0) for c in bid_cols]).alias("_bid_depth"),
        pl.sum_horizontal(*[pl.col(c).fill_null(0.0) for c in ask_cols]).alias("_ask_depth"),
    )
    df = df.with_columns(
        ((pl.col("_bid_depth") - pl.col("_ask_depth"))
         / (pl.col("_bid_depth") + pl.col("_ask_depth") + 1e-12))
        .alias("depth_imbalance"),
    )

    # Rolling realized vol (60 snapshots = ~5 min at 5s interval)
    df = df.with_columns(
        pl.col("oracle_price").log().diff().alias("_log_ret"),
    )
    df = df.with_columns(
        pl.col("_log_ret")
        .rolling_std(window_size=60, min_samples=10)
        .alias("rvol_60"),
    )

    # Regime based on rvol_60 terciles
    q33 = df["rvol_60"].drop_nulls().quantile(0.33)
    q67 = df["rvol_60"].drop_nulls().quantile(0.67)
    df = df.with_columns(
        pl.when(pl.col("rvol_60") <= q33).then(pl.lit("low"))
        .when(pl.col("rvol_60") <= q67).then(pl.lit("mid"))
        .otherwise(pl.lit("high"))
        .alias("regime"),
    )

    # Drop temp columns
    df = df.drop("_bid_depth", "_ask_depth", "_log_ret")

    return df


def load_trades(data_dir: Path) -> pl.DataFrame:
    """Load trades from parquet files (recent + historical), dedup.

    Looks for:
        data_dir / "drift_solusdc_trades.parquet"       (recent, from fetch_drift_trades.py)
        data_dir / "drift_sol_perp_trades.parquet"       (historical, from fetch_drift_historical.py)
    """
    dfs = []
    for name in ["drift_solusdc_trades.parquet", "drift_sol_perp_trades.parquet"]:
        path = data_dir / name
        if path.exists():
            dfs.append(pl.read_parquet(path))

    if not dfs:
        raise FileNotFoundError(f"No trade parquet files found in {data_dir}")

    df = pl.concat(dfs, how="diagonal_relaxed")

    # Dedup and sort
    if "tx_sig" in df.columns:
        df = df.unique(subset=["timestamp", "tx_sig"]).sort("timestamp")
    else:
        df = df.unique(subset=["timestamp"]).sort("timestamp")

    return df


def load_candles_and_fr(data_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load 1h candles and funding rates for Drift SOL-PERP.

    Returns (candles, funding_rates) DataFrames.
    """
    candles = pl.read_parquet(data_dir / "drift_sol_perp_candles_1h.parquet")
    fr = pl.read_parquet(data_dir / "drift_sol_perp_funding_rates.parquet")

    # Convert FR from price units to fraction
    if "oracle_price_twap" in fr.columns:
        fr = fr.with_columns(
            (pl.col("funding_rate") / pl.col("oracle_price_twap")).alias("fr_pct"),
        )

    return candles, fr


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def compute_spread_distribution(l2: pl.DataFrame) -> dict:
    """Compute spread distribution statistics.

    Returns:
        overall  - dict with mean, median, std, p5, p25, p75, p95
        by_hour  - DataFrame with hour, mean_spread, median_spread, count
        by_regime - DataFrame with regime, mean_spread, median_spread, count
    """
    spread = l2["drift_spread_bp"].drop_nulls()

    overall = {
        "mean": spread.mean(),
        "median": spread.median(),
        "std": spread.std(),
        "p5": spread.quantile(0.05),
        "p25": spread.quantile(0.25),
        "p75": spread.quantile(0.75),
        "p95": spread.quantile(0.95),
        "count": len(spread),
    }

    by_hour = (
        l2.group_by("hour")
        .agg(
            pl.col("drift_spread_bp").mean().alias("mean_spread"),
            pl.col("drift_spread_bp").median().alias("median_spread"),
            pl.col("drift_spread_bp").std().alias("std_spread"),
            pl.len().alias("count"),
        )
        .sort("hour")
    )

    by_regime = (
        l2.filter(pl.col("regime").is_not_null())
        .group_by("regime")
        .agg(
            pl.col("drift_spread_bp").mean().alias("mean_spread"),
            pl.col("drift_spread_bp").median().alias("median_spread"),
            pl.col("drift_spread_bp").std().alias("std_spread"),
            pl.len().alias("count"),
        )
        .sort("regime")
    )

    return {"overall": overall, "by_hour": by_hour, "by_regime": by_regime}


def compute_oracle_divergence_dynamics(l2: pl.DataFrame) -> dict:
    """Compute oracle divergence (oracle vs vAMM mid) dynamics.

    Returns:
        distribution - dict with mean, std, p5, p95, abs_mean
        acf          - list of (lag_seconds, autocorrelation) tuples
        half_life_seconds - estimated half-life from ACF decay
    """
    div = l2["oracle_div_bp"].drop_nulls()

    distribution = {
        "mean": div.mean(),
        "std": div.std(),
        "p5": div.quantile(0.05),
        "p95": div.quantile(0.95),
        "abs_mean": div.abs().mean(),
        "count": len(div),
    }

    # ACF at various lags (5s, 15s, 30s, 1m, 2m, 5m, 10m, 30m, 1h)
    div_arr = div.to_numpy()
    n = len(div_arr)
    mean_d = np.mean(div_arr)
    var_d = np.var(div_arr)

    lags_snapshots = [1, 3, 6, 12, 24, 60, 120, 360, 720]  # in 5s snapshots
    lag_seconds = [5, 15, 30, 60, 120, 300, 600, 1800, 3600]
    acf = []
    for lag_s, lag_n in zip(lag_seconds, lags_snapshots):
        if lag_n >= n:
            break
        if var_d < 1e-12:
            acf.append((lag_s, 0.0))
            continue
        cov = np.mean((div_arr[lag_n:] - mean_d) * (div_arr[:-lag_n] - mean_d))
        acf.append((lag_s, float(cov / var_d)))

    # Estimate half-life from first ACF crossing below 0.5
    half_life_seconds = None
    for lag_s, r in acf:
        if r < 0.5:
            half_life_seconds = lag_s
            break
    if half_life_seconds is None and acf:
        half_life_seconds = acf[-1][0]  # longer than measured

    return {
        "distribution": distribution,
        "acf": acf,
        "half_life_seconds": half_life_seconds,
    }


def compute_book_shape(l2: pl.DataFrame) -> dict:
    """Analyze order book shape: vAMM vs dlob sources, depth by level.

    Returns:
        source_shares - dict with vamm_pct, dlob_pct for bid/ask
        depth_by_level - DataFrame with level, mean_bid_size, mean_ask_size
        depth_by_hour  - DataFrame with hour, mean_total_depth
    """
    # Source shares for best bid/ask
    n = len(l2)
    source_shares = {}
    for side in ("bid", "ask"):
        col = f"drift_{side}1_source"
        if col in l2.columns:
            counts = l2[col].value_counts()
            vamm_n = counts.filter(pl.col(col) == "vamm")["count"].sum()
            source_shares[f"{side}_vamm_pct"] = float(vamm_n) / n * 100 if n > 0 else 0
            source_shares[f"{side}_dlob_pct"] = 100 - source_shares[f"{side}_vamm_pct"]

    # Depth by level
    levels = []
    for i in range(1, 6):
        levels.append({
            "level": i,
            "mean_bid_size": l2[f"drift_bid{i}_size"].drop_nulls().mean(),
            "mean_ask_size": l2[f"drift_ask{i}_size"].drop_nulls().mean(),
            "mean_bid_price_dist_bp": (
                (l2["drift_mid"] - l2[f"drift_bid{i}_price"])
                / (l2["drift_mid"] + 1e-12)
                * 1e4
            ).mean(),
            "mean_ask_price_dist_bp": (
                (l2[f"drift_ask{i}_price"] - l2["drift_mid"])
                / (l2["drift_mid"] + 1e-12)
                * 1e4
            ).mean(),
        })
    depth_by_level = pl.DataFrame(levels)

    # Total depth by hour
    bid_cols = [f"drift_bid{i}_size" for i in range(1, 6)]
    ask_cols = [f"drift_ask{i}_size" for i in range(1, 6)]
    depth_by_hour = (
        l2.with_columns(
            pl.sum_horizontal(*[pl.col(c).fill_null(0.0) for c in bid_cols + ask_cols])
            .alias("total_depth")
        )
        .group_by("hour")
        .agg(
            pl.col("total_depth").mean().alias("mean_total_depth"),
            pl.len().alias("count"),
        )
        .sort("hour")
    )

    return {
        "source_shares": source_shares,
        "depth_by_level": depth_by_level,
        "depth_by_hour": depth_by_hour,
    }


def estimate_fill_probability(l2: pl.DataFrame, trades: pl.DataFrame) -> dict:
    """Estimate fill probability by joining L2 snapshots with trades.

    For each L2 snapshot, check if a trade occurred within the next 60 seconds.
    Break down by spread bucket and hour.

    Returns:
        overall_fill_rate - fraction of snapshots with a fill in next 60s
        by_spread_bucket  - DataFrame with spread_bucket, fill_rate, count
        by_hour           - DataFrame with hour, fill_rate, count
    """
    # Ensure trades have timestamp
    if trades.is_empty():
        return {
            "overall_fill_rate": 0.0,
            "by_spread_bucket": pl.DataFrame(),
            "by_hour": pl.DataFrame(),
        }

    # Resample L2 to 1-minute to avoid excessive join
    l2_1m = (
        l2.with_columns(
            pl.col("timestamp").dt.truncate("1m").alias("ts_1m"),
        )
        .group_by("ts_1m")
        .agg(
            pl.col("drift_spread_bp").mean().alias("spread_bp"),
            pl.col("hour").first().alias("hour"),
        )
        .sort("ts_1m")
    )

    # Count trades per minute
    trades_1m = (
        trades.with_columns(
            pl.col("timestamp").dt.truncate("1m").alias("ts_1m"),
        )
        .group_by("ts_1m")
        .agg(
            pl.len().alias("n_trades"),
            pl.col("size").sum().alias("total_size"),
        )
    )

    # Join
    merged = l2_1m.join(trades_1m, on="ts_1m", how="left")
    merged = merged.with_columns(
        (pl.col("n_trades").fill_null(0) > 0).alias("has_fill"),
    )

    overall_fill_rate = merged["has_fill"].mean()

    # By spread bucket
    merged = merged.with_columns(
        pl.when(pl.col("spread_bp") < 5).then(pl.lit("<5bp"))
        .when(pl.col("spread_bp") < 10).then(pl.lit("5-10bp"))
        .when(pl.col("spread_bp") < 20).then(pl.lit("10-20bp"))
        .otherwise(pl.lit("20+bp"))
        .alias("spread_bucket"),
    )
    by_spread = (
        merged.group_by("spread_bucket")
        .agg(
            pl.col("has_fill").mean().alias("fill_rate"),
            pl.len().alias("count"),
        )
        .sort("spread_bucket")
    )

    # By hour
    by_hour = (
        merged.group_by("hour")
        .agg(
            pl.col("has_fill").mean().alias("fill_rate"),
            pl.len().alias("count"),
        )
        .sort("hour")
    )

    return {
        "overall_fill_rate": overall_fill_rate,
        "by_spread_bucket": by_spread,
        "by_hour": by_hour,
    }


def measure_adverse_selection(l2: pl.DataFrame, trades: pl.DataFrame) -> dict:
    """Measure adverse selection: oracle price move after trades.

    For each trade, measure oracle_price change at 5s, 30s, 60s, 300s horizons.
    Positive = price moved in direction of trade (adverse for MM).

    Returns:
        by_horizon   - list of dicts with horizon_s, mean_move_bp, median_move_bp
        by_hour      - DataFrame with hour, mean_adverse_bp (at 60s horizon)
        by_size_bucket - DataFrame with size_bucket, mean_adverse_bp
    """
    if trades.is_empty() or l2.is_empty():
        return {"by_horizon": [], "by_hour": pl.DataFrame(), "by_size_bucket": pl.DataFrame()}

    # Build oracle price series from L2 (5s resolution)
    oracle = l2.select("timestamp", "oracle_price").sort("timestamp")

    # For each trade, find oracle price at trade time and at horizons
    horizons_s = [5, 30, 60, 300]
    trade_df = trades.select("timestamp", "price", "size", "side").sort("timestamp")

    # join_asof: find nearest oracle price at trade time
    trade_with_oracle = trade_df.join_asof(
        oracle.rename({"oracle_price": "oracle_at_trade"}),
        on="timestamp",
        strategy="nearest",
    )

    # For each horizon, join_asof with offset
    horizon_results = []
    for h in horizons_s:
        oracle_shifted = oracle.with_columns(
            (pl.col("timestamp") - pl.duration(seconds=h))
            .cast(pl.Datetime("ns", "UTC"))
            .alias("timestamp"),
        ).rename({"oracle_price": f"oracle_{h}s"})

        trade_with_oracle = trade_with_oracle.join_asof(
            oracle_shifted,
            on="timestamp",
            strategy="nearest",
        )

    # Compute adverse selection for each horizon
    by_horizon = []
    for h in horizons_s:
        col = f"oracle_{h}s"
        if col not in trade_with_oracle.columns:
            continue
        # Price move in bps
        move = (
            (trade_with_oracle[col] - trade_with_oracle["oracle_at_trade"])
            / (trade_with_oracle["oracle_at_trade"] + 1e-12)
            * 1e4
        )
        # Flip sign for sells (adverse = price moves against MM)
        side_sign = trade_with_oracle["side"].map_elements(
            lambda s: 1.0 if s == "buy" else -1.0, return_dtype=pl.Float64,
        )
        adverse = move * side_sign
        adverse_valid = adverse.drop_nulls()

        by_horizon.append({
            "horizon_s": h,
            "mean_move_bp": float(adverse_valid.mean()) if len(adverse_valid) > 0 else 0.0,
            "median_move_bp": float(adverse_valid.median()) if len(adverse_valid) > 0 else 0.0,
            "std_move_bp": float(adverse_valid.std()) if len(adverse_valid) > 0 else 0.0,
            "count": len(adverse_valid),
        })

    # By hour (using 60s horizon)
    col_60 = "oracle_60s"
    by_hour = pl.DataFrame()
    by_size = pl.DataFrame()

    if col_60 in trade_with_oracle.columns:
        tw = trade_with_oracle.with_columns(
            pl.col("timestamp").dt.hour().alias("hour"),
        )
        move_60 = (
            (tw[col_60] - tw["oracle_at_trade"])
            / (tw["oracle_at_trade"] + 1e-12)
            * 1e4
        )
        side_sign = tw["side"].map_elements(
            lambda s: 1.0 if s == "buy" else -1.0, return_dtype=pl.Float64,
        )
        tw = tw.with_columns(
            (move_60 * side_sign).alias("adverse_60s_bp"),
        )

        by_hour = (
            tw.group_by("hour")
            .agg(
                pl.col("adverse_60s_bp").mean().alias("mean_adverse_bp"),
                pl.col("adverse_60s_bp").median().alias("median_adverse_bp"),
                pl.len().alias("count"),
            )
            .sort("hour")
        )

        # By size bucket
        size_p50 = tw["size"].median()
        size_p90 = tw["size"].quantile(0.90)
        tw = tw.with_columns(
            pl.when(pl.col("size") < size_p50).then(pl.lit("small"))
            .when(pl.col("size") < size_p90).then(pl.lit("medium"))
            .otherwise(pl.lit("large"))
            .alias("size_bucket"),
        )
        by_size = (
            tw.group_by("size_bucket")
            .agg(
                pl.col("adverse_60s_bp").mean().alias("mean_adverse_bp"),
                pl.col("adverse_60s_bp").median().alias("median_adverse_bp"),
                pl.len().alias("count"),
            )
            .sort("size_bucket")
        )

    return {
        "by_horizon": by_horizon,
        "by_hour": by_hour,
        "by_size_bucket": by_size,
    }


def recommend_parameters(
    spread_result: dict,
    divergence_result: dict,
    book_result: dict,
    fill_result: dict,
    adverse_result: dict,
) -> dict:
    """Recommend MM parameters based on analysis results.

    Returns dict with recommended values and reasoning strings.
    """
    reasoning = []

    # Half-spread: based on median spread and adverse selection
    median_spread = spread_result["overall"].get("median", 10.0)
    # Target half-spread should be > adverse selection at 60s
    as_60 = 0.0
    for h in adverse_result.get("by_horizon", []):
        if h["horizon_s"] == 60:
            as_60 = h["mean_move_bp"]
            break

    half_spread_bps = max(median_spread / 4, as_60 + 1.0, 2.0)
    reasoning.append(
        f"half_spread={half_spread_bps:.1f}bp: median_market_spread={median_spread:.1f}bp, "
        f"AS_60s={as_60:.1f}bp, need margin above AS"
    )

    # Gamma: based on volatility regime
    spread_std = spread_result["overall"].get("std", 5.0)
    gamma = 0.1 if spread_std < 10 else 0.15 if spread_std < 20 else 0.25
    reasoning.append(
        f"gamma={gamma}: spread_std={spread_std:.1f}bp, "
        f"{'low' if gamma < 0.15 else 'moderate' if gamma < 0.25 else 'high'} risk aversion"
    )

    # Active hours: pick hours with highest fill rate
    fill_by_hour = fill_result.get("by_hour", pl.DataFrame())
    if not fill_by_hour.is_empty() and "fill_rate" in fill_by_hour.columns:
        top_hours = (
            fill_by_hour.sort("fill_rate", descending=True)
            .head(8)["hour"]
            .sort()
            .to_list()
        )
        if top_hours:
            active_start = int(top_hours[0])
            active_end = int(top_hours[-1]) + 1
        else:
            active_start, active_end = 14, 22
    else:
        active_start, active_end = 14, 22
    reasoning.append(
        f"active_hours={active_start}-{active_end} UTC: based on fill rate by hour"
    )

    # Max inventory: based on AS magnitude
    max_inventory = 3 if as_60 > 3.0 else 5 if as_60 > 1.0 else 10
    reasoning.append(
        f"max_inventory={max_inventory}: AS_60s={as_60:.1f}bp, "
        f"{'tight' if max_inventory <= 3 else 'moderate' if max_inventory <= 5 else 'loose'} limits"
    )

    # Oracle divergence alpha
    half_life = divergence_result.get("half_life_seconds", 60)
    oracle_div_alpha = 0.3 if half_life and half_life < 30 else 0.5 if half_life and half_life < 120 else 0.8
    reasoning.append(
        f"oracle_div_alpha={oracle_div_alpha}: divergence half-life={half_life}s"
    )

    return {
        "half_spread_bps": half_spread_bps,
        "gamma": gamma,
        "active_start": active_start,
        "active_end": active_end,
        "max_inventory": max_inventory,
        "oracle_div_alpha": oracle_div_alpha,
        "reasoning": reasoning,
    }
