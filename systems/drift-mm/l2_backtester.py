"""L2-based Avellaneda-Stoikov backtester for Drift SOL-PERP.

Uses 5-second L2 snapshots (resampled to 1-minute) and trade stream
for realistic fill simulation. Extends the paper_trader.py AS model with:
  - Trade-cross fill detection (not candle high/low)
  - Oracle divergence as a quote skew feature
  - Adverse selection guard (spread widening)
  - 1-minute time resolution (vs 1-hour)

Usage (from repo root):
    import sys; sys.path.insert(0, "systems/drift-mm")
    from l2_backtester import L2PaperTrader, L2MMConfig
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

# drift-mm dir has hyphens; add to path for sibling imports
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from paper_trader import MMConfig, MMState  # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class L2MMConfig(MMConfig):
    resample_seconds: int = 60
    fill_method: str = "trade_cross"  # "trade_cross" or "level_touch"
    oracle_div_alpha: float = 0.5  # oracle divergence skew strength
    adverse_selection_guard_bps: float = 2.0  # extra spread on top of AS model


# ---------------------------------------------------------------------------
# L2 Paper Trader
# ---------------------------------------------------------------------------

class L2PaperTrader:
    def __init__(self, config: L2MMConfig):
        self.config = config
        self.state = MMState()

    # ---- resample L2 to target frequency ----

    @staticmethod
    def resample_l2(l2: pl.DataFrame, seconds: int = 60) -> pl.DataFrame:
        """Resample 5s L2 snapshots to lower frequency (default 1 min).

        Takes the last snapshot in each window (close-like behavior).
        """
        return (
            l2.with_columns(
                pl.col("timestamp").dt.truncate(f"{seconds}s").alias("ts_bar"),
            )
            .group_by("ts_bar")
            .agg(
                pl.col("oracle_price").last().alias("oracle_price"),
                pl.col("drift_mid").last().alias("drift_mid"),
                pl.col("drift_spread_bp").last().alias("drift_spread_bp"),
                pl.col("oracle_div_bp").last().alias("oracle_div_bp"),
                pl.col("drift_bid1_price").last().alias("bid1_price"),
                pl.col("drift_ask1_price").last().alias("ask1_price"),
                pl.col("depth_imbalance").last().alias("depth_imbalance"),
                pl.col("rvol_60").last().alias("rvol_60"),
                pl.col("vamm_share").last().alias("vamm_share"),
                pl.col("hour").last().alias("hour"),
            )
            .sort("ts_bar")
        )

    # ---- feature computation ----

    def compute_features(self, i: int, oracle: np.ndarray,
                         oracle_div: np.ndarray, spread_bp: np.ndarray,
                         hour: np.ndarray, rvol_60: np.ndarray,
                         fr_arr: np.ndarray) -> dict:
        """Compute features for bar i (1-min resolution)."""
        cfg = self.config

        # Realized vol: annualized from 1-min log returns
        # Use ~60 bars = 1 hour lookback
        start = max(0, i - 60)
        if i - start >= 2:
            log_ret = np.diff(np.log(oracle[start:i + 1]))
            # 525,600 minutes per year
            rvol = np.std(log_ret) * math.sqrt(525_600)
        else:
            rvol = 0.5

        # Oracle divergence (current snapshot)
        oracle_div_val = oracle_div[i] if i < len(oracle_div) else 0.0

        # Spread from market
        market_spread = spread_bp[i] if i < len(spread_bp) else 10.0

        # FR EMA (hourly FR, use last known)
        alpha_ema = 2.0 / (cfg.fr_ema_span + 1)
        fr_start = max(0, i - cfg.fr_ema_span * 60)  # 60 bars per hour
        fr_ema = 0.0
        for j in range(fr_start, min(i + 1, len(fr_arr))):
            fr_ema = alpha_ema * fr_arr[j] + (1 - alpha_ema) * fr_ema

        # Momentum (4h = 240 bars at 1-min)
        mom_start = max(0, i - cfg.momentum_window * 60)
        momentum = (oracle[i] / oracle[mom_start] - 1.0) if oracle[mom_start] > 0 else 0.0

        h = int(hour[i]) if i < len(hour) else 0

        return {
            "rvol": rvol,
            "oracle_div_bp": float(oracle_div_val),
            "market_spread_bp": float(market_spread),
            "fr_ema": fr_ema,
            "momentum": momentum,
            "hour": h,
        }

    # ---- quote computation ----

    def compute_quotes(self, features: dict, mid: float) -> tuple[float, float]:
        """Compute bid/ask using AS model + oracle divergence + AS guard.

        Largely mirrors paper_trader.py but adds:
          1. Oracle divergence skew
          2. Adverse selection guard (spread widening)
        """
        cfg = self.config
        q = self.state.inventory
        sigma = features["rvol"]
        sigma_m = sigma / math.sqrt(525_600)  # per-minute sigma

        tau = 1.0

        # Effective gamma
        gamma_eff = cfg.gamma

        # AS reservation price
        reservation = mid - q * gamma_eff * (sigma_m ** 2) * tau * mid

        # FR bias
        fr_bias = -cfg.fr_alpha * features["fr_ema"] * mid
        reservation += fr_bias

        # Oracle divergence skew: if oracle > vAMM mid, shift reservation up
        # (oracle_div_bp > 0 means oracle above mid)
        oracle_div_shift = cfg.oracle_div_alpha * features["oracle_div_bp"] * mid * 1e-4
        reservation += oracle_div_shift

        # AS optimal spread
        spread = gamma_eff * (sigma_m ** 2) * tau * mid
        spread += (2.0 / gamma_eff) * math.log(1.0 + gamma_eff / cfg.kappa) * mid * sigma_m

        # Minimum spread: 5 bps
        min_spread = mid * 5e-4
        spread = max(spread, min_spread)

        # Adverse selection guard: widen spread
        as_guard = mid * cfg.adverse_selection_guard_bps * 1e-4
        spread += as_guard

        half_spread = spread / 2.0

        # Momentum skew
        mom = features["momentum"]
        skew = cfg.momentum_skew * np.sign(mom) * min(abs(mom) * 100, 1.0) * half_spread

        bid = reservation - half_spread + skew
        ask = reservation + half_spread + skew

        # Time-of-day adjustment
        h = features["hour"]
        if not (cfg.active_start <= h < cfg.active_end):
            extra = half_spread * 0.5
            bid -= extra
            ask += extra

        # Inventory limits
        if q >= cfg.inv_limit:
            bid = 0.0
        if q <= -cfg.inv_limit:
            ask = float("inf")

        return bid, ask

    # ---- fill detection ----

    @staticmethod
    def check_fill_trade_cross(
        bid: float, ask: float,
        trades_in_window: pl.DataFrame,
    ) -> tuple[bool, bool]:
        """Check if trades crossed our bid or ask.

        A buy trade at price >= our ask means our ask was filled.
        A sell trade at price <= our bid means our bid was filled.
        """
        if trades_in_window.is_empty():
            return False, False

        bid_filled = False
        ask_filled = False

        prices = trades_in_window["price"].to_numpy()
        sides = trades_in_window["side"].to_list()

        for price, side in zip(prices, sides):
            if bid > 0 and price <= bid:
                bid_filled = True
            if ask < float("inf") and price >= ask:
                ask_filled = True

        return bid_filled, ask_filled

    @staticmethod
    def check_fill_level_touch(
        bid: float, ask: float,
        next_bid1: float, next_ask1: float,
    ) -> tuple[bool, bool]:
        """Check if L2 best levels touched our quotes."""
        bid_filled = bid > 0 and next_bid1 <= bid
        ask_filled = ask < float("inf") and next_ask1 >= ask
        return bid_filled, ask_filled

    # ---- funding application ----

    def apply_funding(self, fr_value: float, price: float):
        """Apply funding rate. Same logic as paper_trader."""
        inv = self.state.inventory
        if abs(inv) < 1e-9:
            return
        payment = inv * fr_value * price
        self.state.cash -= payment
        self.state.fr_earnings -= payment

    # ---- main simulation ----

    def run(
        self,
        l2_resampled: pl.DataFrame,
        trades: pl.DataFrame,
        fr: pl.DataFrame,
    ) -> dict:
        """Run L2-based backtest.

        Args:
            l2_resampled: Resampled L2 data (from resample_l2)
            trades: Trade DataFrame with timestamp, price, size, side
            fr: Funding rate DataFrame with timestamp, fr_pct
        """
        cfg = self.config
        st = self.state

        # Extract arrays from resampled L2
        timestamps = l2_resampled["ts_bar"].to_numpy()
        oracle = l2_resampled["oracle_price"].to_numpy().astype(float)
        oracle_div = l2_resampled["oracle_div_bp"].to_numpy().astype(float)
        spread_bp = l2_resampled["drift_spread_bp"].to_numpy().astype(float)
        bid1 = l2_resampled["bid1_price"].to_numpy().astype(float)
        ask1 = l2_resampled["ask1_price"].to_numpy().astype(float)
        hour = l2_resampled["hour"].to_numpy().astype(int)
        rvol_60_arr = l2_resampled["rvol_60"].to_numpy().astype(float)

        n = len(oracle)

        # Build per-minute FR array (aligned to l2 timestamps)
        # FR is hourly; replicate to each minute within that hour
        fr_per_min = np.zeros(n)
        if not fr.is_empty() and "fr_pct" in fr.columns:
            fr_ts = fr["timestamp"].to_numpy()
            fr_vals = fr["fr_pct"].to_numpy().astype(float)
            fr_idx = 0
            for i in range(n):
                while fr_idx < len(fr_ts) - 1 and fr_ts[fr_idx + 1] <= timestamps[i]:
                    fr_idx += 1
                if fr_idx < len(fr_vals):
                    fr_per_min[i] = fr_vals[fr_idx]

        # Warmup
        warmup = max(60, cfg.rvol_window, cfg.momentum_window * 60) + 1
        warmup = min(warmup, n - 2)

        # Track last FR application hour
        last_fr_hour = -1

        for i in range(warmup, n - 1):
            mid = oracle[i]
            if mid <= 0 or np.isnan(mid):
                continue

            # Compute features
            features = self.compute_features(
                i, oracle, oracle_div, spread_bp, hour, rvol_60_arr, fr_per_min
            )

            # Compute quotes
            bid, ask = self.compute_quotes(features, mid)

            # Record spread
            if bid > 0 and ask < float("inf"):
                st.spread_history.append((ask - bid) / mid * 1e4)

            # Fill check
            if cfg.fill_method == "trade_cross":
                # Get trades between current and next bar
                ts_start = timestamps[i]
                ts_end = timestamps[i + 1]
                mask = (trades["timestamp"].to_numpy() >= ts_start) & \
                       (trades["timestamp"].to_numpy() < ts_end)
                trades_window = trades.filter(pl.Series(mask))
                bid_filled, ask_filled = self.check_fill_trade_cross(
                    bid, ask, trades_window
                )
            else:
                bid_filled, ask_filled = self.check_fill_level_touch(
                    bid, ask, bid1[i + 1], ask1[i + 1]
                )

            # Process fills
            if bid_filled and st.inventory < cfg.inv_limit:
                st.cash -= bid * cfg.base_size
                st.inventory += cfg.base_size
                rebate = bid * cfg.base_size * cfg.maker_rebate_bps * 1e-4
                st.cash += rebate
                st.total_fees_earned += rebate
                st.n_bid_fills += 1

            if ask_filled and st.inventory > -cfg.inv_limit:
                st.cash += ask * cfg.base_size
                st.inventory -= cfg.base_size
                rebate = ask * cfg.base_size * cfg.maker_rebate_bps * 1e-4
                st.cash += rebate
                st.total_fees_earned += rebate
                st.n_ask_fills += 1

            # Apply funding once per hour
            current_hour_ts = int(timestamps[i].astype("datetime64[h]").astype(np.int64))
            if current_hour_ts != last_fr_hour:
                self.apply_funding(fr_per_min[i], oracle[i])
                last_fr_hour = current_hour_ts

            # Mark-to-market
            next_price = oracle[i + 1]
            mtm = st.cash + st.inventory * next_price
            st.mark_to_market.append(mtm)
            st.pnl_history.append(mtm)
            st.inventory_history.append(st.inventory)
            st.timestamps.append(timestamps[i + 1])

        return self._compute_metrics(oracle)

    def _compute_metrics(self, oracle: np.ndarray) -> dict:
        """Compute performance metrics (similar to paper_trader)."""
        st = self.state
        pnl = np.array(st.pnl_history)
        if len(pnl) < 2:
            return {
                "sharpe": 0, "total_pnl": 0, "total_pnl_bps": 0,
                "max_dd": 0, "fill_rate_bid": 0, "fill_rate_ask": 0,
                "avg_inventory": 0, "fr_earnings": 0, "avg_spread_bps": 0,
                "n_fills": 0,
            }

        returns = np.diff(pnl)
        avg_price = np.mean(oracle[oracle > 0])

        # Annualize from 1-min bars (525,600 minutes/year)
        sharpe = (np.mean(returns) / (np.std(returns) + 1e-12)) * math.sqrt(525_600)

        cummax = np.maximum.accumulate(pnl)
        dd = cummax - pnl
        max_dd = np.max(dd) if len(dd) > 0 else 0

        total_fills = st.n_bid_fills + st.n_ask_fills
        n_bars = len(pnl)
        inv_arr = np.array(st.inventory_history)

        total_pnl = pnl[-1] if len(pnl) > 0 else 0
        total_pnl_bps = total_pnl / (avg_price * self.config.base_size) * 1e4

        spread_arr = np.array(st.spread_history)

        return {
            "sharpe": sharpe,
            "total_pnl": total_pnl,
            "total_pnl_bps": total_pnl_bps,
            "max_dd": max_dd,
            "max_dd_bps": max_dd / (avg_price * self.config.base_size) * 1e4,
            "fill_rate_bid": st.n_bid_fills / n_bars if n_bars > 0 else 0,
            "fill_rate_ask": st.n_ask_fills / n_bars if n_bars > 0 else 0,
            "n_bid_fills": st.n_bid_fills,
            "n_ask_fills": st.n_ask_fills,
            "n_fills": total_fills,
            "avg_inventory": np.mean(np.abs(inv_arr)) if len(inv_arr) > 0 else 0,
            "max_inventory": np.max(np.abs(inv_arr)) if len(inv_arr) > 0 else 0,
            "fr_earnings": st.fr_earnings,
            "fr_frac": st.fr_earnings / (total_pnl + 1e-12) if total_pnl != 0 else 0,
            "avg_spread_bps": np.mean(spread_arr) if len(spread_arr) > 0 else 0,
            "median_spread_bps": np.median(spread_arr) if len(spread_arr) > 0 else 0,
            "total_fees_earned": st.total_fees_earned,
            "timestamps": st.timestamps,
            "pnl_history": st.pnl_history,
            "inventory_history": st.inventory_history,
        }
