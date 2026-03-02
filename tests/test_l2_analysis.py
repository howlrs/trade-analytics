"""Tests for src/l2_analysis module using synthetic L2 data."""

import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from src.l2_analysis import (
    compute_spread_distribution,
    compute_oracle_divergence_dynamics,
    compute_book_shape,
    estimate_fill_probability,
    measure_adverse_selection,
    recommend_parameters,
)


# ---------------------------------------------------------------------------
# Synthetic data fixtures
# ---------------------------------------------------------------------------

def _make_l2(n: int = 1000, seed: int = 42) -> pl.DataFrame:
    """Create synthetic L2 snapshot data."""
    rng = np.random.default_rng(seed)
    base_price = 150.0
    timestamps = [
        datetime(2026, 3, 1, tzinfo=timezone.utc) + timedelta(seconds=5 * i)
        for i in range(n)
    ]

    oracle_price = base_price + np.cumsum(rng.normal(0, 0.01, n))
    drift_mid = oracle_price + rng.normal(0, 0.02, n)
    spread_bp = rng.uniform(5, 20, n)

    rows = {
        "timestamp": timestamps,
        "market": ["SOL-PERP"] * n,
        "oracle_price": oracle_price.tolist(),
        "oracle_twap": (oracle_price * 0.999).tolist(),
        "oracle_div_bp": (rng.normal(0, 3, n)).tolist(),
        "drift_mid": drift_mid.tolist(),
        "drift_spread_bp": spread_bp.tolist(),
    }

    # Add 5 levels of bid/ask
    for i in range(1, 6):
        offset = i * 0.05
        rows[f"drift_bid{i}_price"] = (drift_mid - offset).tolist()
        rows[f"drift_bid{i}_size"] = rng.uniform(1, 10, n).tolist()
        rows[f"drift_bid{i}_source"] = rng.choice(["vamm", "dlob"], n, p=[0.8, 0.2]).tolist()
        rows[f"drift_ask{i}_price"] = (drift_mid + offset).tolist()
        rows[f"drift_ask{i}_size"] = rng.uniform(1, 10, n).tolist()
        rows[f"drift_ask{i}_source"] = rng.choice(["vamm", "dlob"], n, p=[0.7, 0.3]).tolist()

    schema = {"timestamp": pl.Datetime("ns", "UTC")}
    df = pl.DataFrame(rows, schema_overrides=schema)

    # Add derived columns that load_l2_data would compute
    df = df.with_columns(
        pl.col("timestamp").dt.hour().alias("hour"),
    )
    df = df.with_columns(
        ((pl.col("drift_bid1_source") == "vamm").cast(pl.Float64) * 0.5
         + (pl.col("drift_ask1_source") == "vamm").cast(pl.Float64) * 0.5)
        .alias("vamm_share"),
    )
    # depth_imbalance
    bid_cols = [f"drift_bid{i}_size" for i in range(1, 6)]
    ask_cols = [f"drift_ask{i}_size" for i in range(1, 6)]
    df = df.with_columns(
        pl.sum_horizontal(*[pl.col(c) for c in bid_cols]).alias("_bd"),
        pl.sum_horizontal(*[pl.col(c) for c in ask_cols]).alias("_ad"),
    )
    df = df.with_columns(
        ((pl.col("_bd") - pl.col("_ad")) / (pl.col("_bd") + pl.col("_ad") + 1e-12))
        .alias("depth_imbalance"),
    ).drop("_bd", "_ad")

    # rvol_60 and regime
    df = df.with_columns(
        pl.col("oracle_price").log().diff().rolling_std(window_size=60, min_samples=10)
        .alias("rvol_60"),
    )
    q33 = df["rvol_60"].drop_nulls().quantile(0.33)
    q67 = df["rvol_60"].drop_nulls().quantile(0.67)
    df = df.with_columns(
        pl.when(pl.col("rvol_60") <= q33).then(pl.lit("low"))
        .when(pl.col("rvol_60") <= q67).then(pl.lit("mid"))
        .otherwise(pl.lit("high"))
        .alias("regime"),
    )

    return df


def _make_trades(n: int = 200, seed: int = 42) -> pl.DataFrame:
    """Create synthetic trade data."""
    rng = np.random.default_rng(seed)
    timestamps = [
        datetime(2026, 3, 1, tzinfo=timezone.utc) + timedelta(seconds=25 * i)
        for i in range(n)
    ]

    return pl.DataFrame({
        "timestamp": timestamps,
        "price": (150.0 + np.cumsum(rng.normal(0, 0.01, n))).tolist(),
        "size": rng.uniform(0.1, 5.0, n).tolist(),
        "side": rng.choice(["buy", "sell"], n).tolist(),
        "tx_sig": [f"tx_{i}" for i in range(n)],
    }, schema_overrides={"timestamp": pl.Datetime("ns", "UTC")})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSpreadDistribution:
    def test_overall_stats(self):
        l2 = _make_l2()
        result = compute_spread_distribution(l2)
        assert "overall" in result
        assert result["overall"]["mean"] > 0
        assert result["overall"]["count"] == 1000
        assert result["overall"]["p5"] < result["overall"]["p95"]

    def test_by_hour(self):
        l2 = _make_l2()
        result = compute_spread_distribution(l2)
        by_hour = result["by_hour"]
        assert len(by_hour) > 0
        assert "mean_spread" in by_hour.columns

    def test_by_regime(self):
        l2 = _make_l2()
        result = compute_spread_distribution(l2)
        by_regime = result["by_regime"]
        assert len(by_regime) > 0


class TestOracleDivergence:
    def test_distribution(self):
        l2 = _make_l2()
        result = compute_oracle_divergence_dynamics(l2)
        assert "distribution" in result
        assert result["distribution"]["count"] > 0

    def test_acf_computed(self):
        l2 = _make_l2()
        result = compute_oracle_divergence_dynamics(l2)
        assert len(result["acf"]) > 0
        # First lag should have highest autocorrelation
        lags = result["acf"]
        assert lags[0][0] == 5  # first lag is 5 seconds

    def test_half_life(self):
        l2 = _make_l2()
        result = compute_oracle_divergence_dynamics(l2)
        assert result["half_life_seconds"] is not None
        assert result["half_life_seconds"] > 0


class TestBookShape:
    def test_source_shares(self):
        l2 = _make_l2()
        result = compute_book_shape(l2)
        shares = result["source_shares"]
        assert "bid_vamm_pct" in shares
        # vamm should dominate (we set p=0.8 for bids)
        assert shares["bid_vamm_pct"] > 50

    def test_depth_by_level(self):
        l2 = _make_l2()
        result = compute_book_shape(l2)
        dbl = result["depth_by_level"]
        assert len(dbl) == 5
        assert "mean_bid_size" in dbl.columns


class TestFillProbability:
    def test_overall_fill_rate(self):
        l2 = _make_l2()
        trades = _make_trades()
        result = estimate_fill_probability(l2, trades)
        assert 0.0 <= result["overall_fill_rate"] <= 1.0

    def test_empty_trades(self):
        l2 = _make_l2()
        empty_trades = pl.DataFrame({
            "timestamp": [],
            "price": [],
            "size": [],
            "side": [],
        }, schema_overrides={"timestamp": pl.Datetime("ns", "UTC")})
        result = estimate_fill_probability(l2, empty_trades)
        assert result["overall_fill_rate"] == 0.0


class TestAdverseSelection:
    def test_by_horizon(self):
        l2 = _make_l2(n=2000)
        trades = _make_trades(n=100)
        result = measure_adverse_selection(l2, trades)
        assert len(result["by_horizon"]) > 0
        # Should have entries for various horizons
        horizons = [h["horizon_s"] for h in result["by_horizon"]]
        assert 5 in horizons
        assert 60 in horizons

    def test_empty_trades(self):
        l2 = _make_l2()
        empty_trades = pl.DataFrame({
            "timestamp": [],
            "price": [],
            "size": [],
            "side": [],
        }, schema_overrides={"timestamp": pl.Datetime("ns", "UTC")})
        result = measure_adverse_selection(l2, empty_trades)
        assert result["by_horizon"] == []


class TestRecommendParameters:
    def test_produces_recommendations(self):
        l2 = _make_l2(n=2000)
        trades = _make_trades(n=100)

        spread_r = compute_spread_distribution(l2)
        div_r = compute_oracle_divergence_dynamics(l2)
        book_r = compute_book_shape(l2)
        fill_r = estimate_fill_probability(l2, trades)
        as_r = measure_adverse_selection(l2, trades)

        rec = recommend_parameters(spread_r, div_r, book_r, fill_r, as_r)
        assert "half_spread_bps" in rec
        assert "gamma" in rec
        assert "max_inventory" in rec
        assert rec["half_spread_bps"] > 0
        assert rec["gamma"] > 0
        assert len(rec["reasoning"]) >= 3
