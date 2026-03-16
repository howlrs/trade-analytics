"""Tests for L2 backtester using synthetic data."""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

# Add drift-mm to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "systems" / "drift-mm"))

from l2_backtester import L2PaperTrader, L2MMConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _make_l2_resampled(n: int = 500, seed: int = 42) -> pl.DataFrame:
    """Create synthetic resampled L2 data (1-min bars)."""
    rng = np.random.default_rng(seed)
    base_price = 150.0
    timestamps = [
        datetime(2026, 3, 1, tzinfo=timezone.utc) + timedelta(minutes=i)
        for i in range(n)
    ]

    oracle = base_price + np.cumsum(rng.normal(0, 0.02, n))
    drift_mid = oracle + rng.normal(0, 0.03, n)
    spread_bp = rng.uniform(5, 15, n)
    oracle_div = rng.normal(0, 2, n)

    return pl.DataFrame({
        "ts_bar": timestamps,
        "oracle_price": oracle.tolist(),
        "drift_mid": drift_mid.tolist(),
        "drift_spread_bp": spread_bp.tolist(),
        "oracle_div_bp": oracle_div.tolist(),
        "bid1_price": (drift_mid - 0.05).tolist(),
        "ask1_price": (drift_mid + 0.05).tolist(),
        "depth_imbalance": rng.uniform(-0.5, 0.5, n).tolist(),
        "rvol_60": rng.uniform(0.001, 0.01, n).tolist(),
        "vamm_share": rng.uniform(0.5, 1.0, n).tolist(),
        "hour": [t.hour for t in timestamps],
    }, schema_overrides={"ts_bar": pl.Datetime("ns", "UTC")})


def _make_trades(n: int = 200, seed: int = 42) -> pl.DataFrame:
    """Create synthetic trades aligned with L2 data."""
    rng = np.random.default_rng(seed)
    timestamps = [
        datetime(2026, 3, 1, tzinfo=timezone.utc) + timedelta(seconds=150 * i)
        for i in range(n)
    ]

    return pl.DataFrame({
        "timestamp": timestamps,
        "price": (150.0 + np.cumsum(rng.normal(0, 0.02, n))).tolist(),
        "size": rng.uniform(0.1, 5.0, n).tolist(),
        "side": rng.choice(["buy", "sell"], n).tolist(),
    }, schema_overrides={"timestamp": pl.Datetime("ns", "UTC")})


def _make_fr(n_hours: int = 10) -> pl.DataFrame:
    """Create synthetic hourly funding rates."""
    rng = np.random.default_rng(42)
    timestamps = [
        datetime(2026, 3, 1, tzinfo=timezone.utc) + timedelta(hours=i)
        for i in range(n_hours)
    ]
    return pl.DataFrame({
        "timestamp": timestamps,
        "fr_pct": rng.normal(0.00001, 0.00005, n_hours).tolist(),
    }, schema_overrides={"timestamp": pl.Datetime("ns", "UTC")})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFillCheck:
    def test_trade_cross_bid_fill(self):
        """Trade at or below bid price should fill bid."""
        bid, ask = 149.0, 151.0
        trades = pl.DataFrame({
            "price": [148.5],
            "side": ["sell"],
            "timestamp": [datetime(2026, 3, 1, tzinfo=timezone.utc)],
            "size": [1.0],
        }, schema_overrides={"timestamp": pl.Datetime("ns", "UTC")})
        bid_filled, ask_filled = L2PaperTrader.check_fill_trade_cross(bid, ask, trades)
        assert bid_filled is True
        assert ask_filled is False

    def test_trade_cross_ask_fill(self):
        """Trade at or above ask price should fill ask."""
        bid, ask = 149.0, 151.0
        trades = pl.DataFrame({
            "price": [151.5],
            "side": ["buy"],
            "timestamp": [datetime(2026, 3, 1, tzinfo=timezone.utc)],
            "size": [1.0],
        }, schema_overrides={"timestamp": pl.Datetime("ns", "UTC")})
        bid_filled, ask_filled = L2PaperTrader.check_fill_trade_cross(bid, ask, trades)
        assert bid_filled is False
        assert ask_filled is True

    def test_no_fill_on_empty_trades(self):
        bid, ask = 149.0, 151.0
        empty = pl.DataFrame({
            "price": [],
            "side": [],
            "timestamp": [],
            "size": [],
        }, schema_overrides={"timestamp": pl.Datetime("ns", "UTC")})
        bid_filled, ask_filled = L2PaperTrader.check_fill_trade_cross(bid, ask, empty)
        assert bid_filled is False
        assert ask_filled is False

    def test_level_touch(self):
        bid, ask = 149.0, 151.0
        bid_filled, ask_filled = L2PaperTrader.check_fill_level_touch(
            bid, ask, next_bid1=148.5, next_ask1=150.5
        )
        assert bid_filled is True
        assert ask_filled is False


class TestFeatureComputation:
    def test_features_returned(self):
        l2 = _make_l2_resampled()
        config = L2MMConfig()
        trader = L2PaperTrader(config)
        oracle = l2["oracle_price"].to_numpy()
        oracle_div = l2["oracle_div_bp"].to_numpy()
        spread_bp = l2["drift_spread_bp"].to_numpy()
        hour = l2["hour"].to_numpy()
        rvol_60 = l2["rvol_60"].to_numpy()
        fr_arr = np.zeros(len(oracle))

        features = trader.compute_features(
            100, oracle, oracle_div, spread_bp, hour, rvol_60, fr_arr
        )
        assert "rvol" in features
        assert "oracle_div_bp" in features
        assert features["rvol"] > 0


class TestMinimalRun:
    def test_run_completes(self):
        """Smoke test: run backtester on synthetic data."""
        l2 = _make_l2_resampled(n=500)
        trades = _make_trades(n=200)
        fr = _make_fr(n_hours=10)

        config = L2MMConfig(
            gamma=0.1,
            inv_limit=5,
            base_size=1.0,
            resample_seconds=60,
        )
        trader = L2PaperTrader(config)
        results = trader.run(l2, trades, fr)

        assert "sharpe" in results
        assert "total_pnl" in results
        assert "n_fills" in results
        assert len(results["pnl_history"]) > 0

    def test_level_touch_method(self):
        """Test with level_touch fill method."""
        l2 = _make_l2_resampled(n=300)
        trades = _make_trades(n=50)
        fr = _make_fr(n_hours=6)

        config = L2MMConfig(fill_method="level_touch")
        trader = L2PaperTrader(config)
        results = trader.run(l2, trades, fr)
        assert "sharpe" in results
