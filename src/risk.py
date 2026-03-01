"""Risk management module based on robust empirical findings.

Provides dynamic position sizing, regime detection, and alert generation
using only statistically validated patterns (Train/Test consistent).

Key findings leveraged:
- Vol clustering: AC lag=1h r=0.98, lag=24h r=0.30-0.49
- Regime persistence: L→L=95.6%, H→H=95.8%
- Vol change mean-reversion: r=-0.27 to -0.37
- Extreme event clustering: P(|z|>2 | prev>2) = 3.1x
- Time-of-day range: US session (13-16 UTC) = 2x Asia quiet (04-06 UTC)
- Weekend range: 63-78% of weekday
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class RiskParams:
    """Configuration for the risk module."""

    target_annual_vol: float = 0.20  # 20% annualized
    max_leverage: float = 5.0
    min_leverage: float = 0.1
    regime_window: int = 168  # 7 days for regime detection
    vol_window_short: int = 24  # 24h rolling vol
    vol_window_long: int = 168  # 7d rolling vol
    extreme_threshold: float = 2.0  # z-score for extreme event
    extreme_cooldown_hours: int = 4


def compute_rvol(returns: np.ndarray, window: int = 24) -> np.ndarray:
    """Compute rolling realized volatility (std of returns).

    Uses the robust finding that rvol is the best predictor of future vol
    (naive prediction r=0.30-0.49 at 24h lag, outperforms ML).
    """
    if len(returns) < window:
        return np.full(len(returns), np.nan)

    rvol = np.full(len(returns), np.nan)
    for i in range(window, len(returns)):
        rvol[i] = np.std(returns[i - window : i])
    return rvol


def detect_regime(
    rvol: np.ndarray,
    low_pct: float = 33,
    high_pct: float = 67,
) -> np.ndarray:
    """Classify volatility regime: 0=Low, 1=Mid, 2=High.

    Leverages regime persistence finding (L→L=95.6%, H→H=95.8%).
    Once in a regime, expect it to persist for 18-24h on average.
    """
    valid = rvol[~np.isnan(rvol)]
    if len(valid) == 0:
        return np.full(len(rvol), np.nan)

    p_low = np.percentile(valid, low_pct)
    p_high = np.percentile(valid, high_pct)

    regime = np.full(len(rvol), np.nan)
    for i in range(len(rvol)):
        if np.isnan(rvol[i]):
            continue
        if rvol[i] < p_low:
            regime[i] = 0
        elif rvol[i] < p_high:
            regime[i] = 1
        else:
            regime[i] = 2
    return regime


def compute_position_size(
    rvol_24h: float,
    hour_utc: int,
    is_weekend: bool,
    regime: int,
    params: RiskParams | None = None,
) -> float:
    """Compute optimal position size based on robust structural patterns.

    Uses:
    - rvol_24h: Current 24h realized vol (best vol predictor)
    - hour_utc: Time-of-day adjustment (13-16 UTC = 2x range)
    - is_weekend: Weekend adjustment (63-78% range)
    - regime: Vol regime (Low=0, Mid=1, High=2)

    Returns: position multiplier (1.0 = base size)
    """
    if params is None:
        params = RiskParams()

    if np.isnan(rvol_24h) or rvol_24h <= 0:
        return params.min_leverage

    # Base: inverse vol sizing
    hourly_target = params.target_annual_vol / np.sqrt(8760)
    base_size = hourly_target / rvol_24h

    # Time-of-day adjustment (empirical range ratios, Train/Test consistent)
    # US session (13-16): range ~1.4x average → reduce size
    # Asia quiet (04-06): range ~0.7x average → increase size
    if 13 <= hour_utc <= 16:
        time_adj = 0.75  # Wider range → smaller size
    elif 4 <= hour_utc <= 6:
        time_adj = 1.3  # Narrower range → larger size
    else:
        time_adj = 1.0

    # Weekend adjustment (range = 63-78% of weekday)
    weekend_adj = 1.2 if is_weekend else 1.0

    # Regime adjustment
    # Low vol: stable, can size up slightly
    # High vol: extreme clustering risk, size down
    regime_adj = {0: 1.1, 1: 1.0, 2: 0.7}.get(regime, 1.0)

    final_size = base_size * time_adj * weekend_adj * regime_adj
    return float(np.clip(final_size, params.min_leverage, params.max_leverage))


def detect_extreme_event(
    returns: np.ndarray,
    rvol: np.ndarray,
    threshold: float = 2.0,
) -> np.ndarray:
    """Detect extreme events (|z-score| > threshold).

    Key finding: P(|z|>2 | prev >2) = 3.1x unconditional.
    After an extreme event, the next hour has 3x normal probability
    of another extreme event.

    Returns: boolean array of extreme events
    """
    if len(returns) != len(rvol):
        raise ValueError("returns and rvol must have same length")

    z = np.full(len(returns), np.nan)
    for i in range(len(returns)):
        if not np.isnan(rvol[i]) and rvol[i] > 0:
            z[i] = returns[i] / rvol[i]

    return np.abs(z) > threshold


def generate_alerts(
    returns: np.ndarray,
    rvol_24h: np.ndarray,
    rvol_7d: np.ndarray,
) -> list[dict]:
    """Generate risk alerts based on validated patterns.

    Alert types:
    - vol_spike: rvol_24h > rvol_7d * 1.5 (vol regime change)
    - extreme_cluster: Consecutive extreme events
    - vol_mean_reversion: After vol spike, expect reversion (r=-0.27 to -0.37)
    """
    alerts = []

    for i in range(1, len(returns)):
        if np.isnan(rvol_24h[i]) or np.isnan(rvol_7d[i]):
            continue

        # Vol spike alert
        if rvol_24h[i] > rvol_7d[i] * 1.5:
            alerts.append(
                {
                    "index": i,
                    "type": "vol_spike",
                    "message": f"rvol_24h ({rvol_24h[i]:.4f}) > 1.5x rvol_7d ({rvol_7d[i]:.4f})",
                    "action": "reduce_position",
                }
            )

        # Extreme event clustering
        if rvol_24h[i] > 0:
            z = abs(returns[i]) / rvol_24h[i]
            if z > 2.0:
                if i > 0 and rvol_24h[i - 1] > 0:
                    z_prev = abs(returns[i - 1]) / rvol_24h[i - 1]
                    if z_prev > 2.0:
                        alerts.append(
                            {
                                "index": i,
                                "type": "extreme_cluster",
                                "message": f"Consecutive extreme events (z={z:.1f}, prev_z={z_prev:.1f})",
                                "action": "widen_spread_or_reduce",
                            }
                        )

        # Vol mean reversion signal (after sustained high vol)
        if i >= 48 and rvol_24h[i] > rvol_7d[i] * 1.3:
            # Check if vol has been elevated for 24h+
            recent_high = all(
                rvol_24h[j] > rvol_7d[j] * 1.1
                for j in range(max(0, i - 24), i)
                if not np.isnan(rvol_24h[j]) and not np.isnan(rvol_7d[j])
            )
            if recent_high:
                alerts.append(
                    {
                        "index": i,
                        "type": "vol_mean_reversion",
                        "message": "Vol elevated 24h+, expect mean reversion (r=-0.3)",
                        "action": "prepare_to_increase_position",
                    }
                )

    return alerts
