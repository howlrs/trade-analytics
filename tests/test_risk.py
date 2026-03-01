"""Tests for the risk management module."""

import numpy as np
import pytest

from src.risk import (
    RiskParams,
    compute_position_size,
    compute_rvol,
    detect_extreme_event,
    detect_regime,
    generate_alerts,
)


class TestComputeRvol:
    def test_basic(self):
        rng = np.random.default_rng(42)
        rets = rng.normal(0, 0.01, 100)
        rvol = compute_rvol(rets, window=24)
        assert np.isnan(rvol[:24]).all()
        assert not np.isnan(rvol[24:]).any()
        assert (rvol[24:] > 0).all()

    def test_short_input(self):
        rvol = compute_rvol(np.array([0.01, -0.01, 0.02]), window=24)
        assert np.isnan(rvol).all()

    def test_constant_returns(self):
        rets = np.full(50, 0.01)
        rvol = compute_rvol(rets, window=24)
        # Constant returns → zero vol
        assert np.allclose(rvol[24:], 0.0)


class TestDetectRegime:
    def test_three_regimes(self):
        rvol = np.array([0.001, 0.002, 0.005, 0.008, 0.012, 0.015])
        regime = detect_regime(rvol, low_pct=33, high_pct=67)
        # Should have Low (0), Mid (1), High (2)
        assert 0 in regime
        assert 2 in regime

    def test_nan_handling(self):
        rvol = np.array([np.nan, 0.01, np.nan, 0.02])
        regime = detect_regime(rvol)
        assert np.isnan(regime[0])
        assert np.isnan(regime[2])

    def test_all_nan(self):
        rvol = np.full(10, np.nan)
        regime = detect_regime(rvol)
        assert np.isnan(regime).all()


class TestComputePositionSize:
    def test_basic(self):
        size = compute_position_size(
            rvol_24h=0.01, hour_utc=12, is_weekend=False, regime=1
        )
        assert 0.1 <= size <= 5.0

    def test_high_vol_reduces_size(self):
        low_vol = compute_position_size(0.005, 12, False, 1)
        high_vol = compute_position_size(0.02, 12, False, 1)
        assert low_vol > high_vol

    def test_us_session_reduces(self):
        us = compute_position_size(0.01, 14, False, 1)
        other = compute_position_size(0.01, 12, False, 1)
        assert us < other

    def test_weekend_increases(self):
        weekday = compute_position_size(0.01, 12, False, 1)
        weekend = compute_position_size(0.01, 12, True, 1)
        assert weekend > weekday

    def test_high_regime_reduces(self):
        low_regime = compute_position_size(0.01, 12, False, 0)
        high_regime = compute_position_size(0.01, 12, False, 2)
        assert low_regime > high_regime

    def test_nan_rvol(self):
        params = RiskParams()
        size = compute_position_size(np.nan, 12, False, 1, params)
        assert size == params.min_leverage

    def test_zero_rvol(self):
        params = RiskParams()
        size = compute_position_size(0.0, 12, False, 1, params)
        assert size == params.min_leverage

    def test_clipping(self):
        params = RiskParams(max_leverage=3.0, min_leverage=0.5)
        # Very low vol → should clip at max
        size = compute_position_size(0.0001, 12, False, 0, params)
        assert size == params.max_leverage


class TestDetectExtremeEvent:
    def test_basic(self):
        returns = np.array([0.001, -0.05, 0.002, 0.06, -0.001])
        rvol = np.array([0.01, 0.01, 0.01, 0.01, 0.01])
        extreme = detect_extreme_event(returns, rvol, threshold=2.0)
        assert not extreme[0]  # 0.1σ
        assert extreme[1]  # -5σ
        assert not extreme[2]  # 0.2σ
        assert extreme[3]  # 6σ

    def test_mismatched_lengths(self):
        with pytest.raises(ValueError):
            detect_extreme_event(np.array([0.01]), np.array([0.01, 0.02]))


class TestGenerateAlerts:
    def test_vol_spike(self):
        n = 50
        returns = np.full(n, 0.001)
        rvol_24h = np.full(n, 0.02)  # High
        rvol_7d = np.full(n, 0.01)  # Low → rvol_24h > 1.5x rvol_7d
        alerts = generate_alerts(returns, rvol_24h, rvol_7d)
        vol_spikes = [a for a in alerts if a["type"] == "vol_spike"]
        assert len(vol_spikes) > 0

    def test_no_alerts_normal(self):
        n = 50
        returns = np.full(n, 0.001)
        rvol_24h = np.full(n, 0.01)
        rvol_7d = np.full(n, 0.01)
        alerts = generate_alerts(returns, rvol_24h, rvol_7d)
        vol_spikes = [a for a in alerts if a["type"] == "vol_spike"]
        assert len(vol_spikes) == 0
