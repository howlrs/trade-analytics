#!/usr/bin/env python3
"""Drift Protocol L2 orderbook snapshot collector with Binance BBO reference.

Polls Drift DLOB REST API and Binance futures BBO at regular intervals,
buffering snapshots in memory and flushing to parquet periodically.

Usage:
    python scripts/collect_drift_data.py --markets SOL --interval 5 --flush-interval 3600
    python scripts/collect_drift_data.py --markets SOL,ETH,BTC --interval 5
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

DLOB_BASE_URL = "https://dlob.drift.trade"
BINANCE_BBO_URL = "https://fapi.binance.com/fapi/v1/ticker/bookTicker"

# Drift perp market indices
DRIFT_MARKET_INDEX = {
    "SOL": 0,
    "BTC": 1,
    "ETH": 2,
}

# Drift uses PRICE_PRECISION = 1e6 for perp prices
DRIFT_PRICE_PRECISION = 1e6

# Number of orderbook levels to store
NUM_LEVELS = 5

# Graceful shutdown flag
_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Shutdown signal received, flushing buffers...")
    _shutdown = True


def parse_args():
    parser = argparse.ArgumentParser(description="Collect Drift L2 orderbook snapshots")
    parser.add_argument(
        "--markets", default="SOL", help="Comma-separated markets (e.g. SOL,ETH,BTC)"
    )
    parser.add_argument(
        "--interval", type=int, default=5, help="Polling interval in seconds (default: 5)"
    )
    parser.add_argument(
        "--flush-interval",
        type=int,
        default=3600,
        help="Parquet flush interval in seconds (default: 3600)",
    )
    parser.add_argument(
        "--output-dir", default=None, help="Output directory (default: data/)"
    )
    parser.add_argument(
        "--depth", type=int, default=20, help="Orderbook depth to request (default: 20)"
    )
    return parser.parse_args()


def fetch_drift_l2(market: str, depth: int = 20) -> dict | None:
    """Fetch Drift L2 orderbook from DLOB REST API."""
    market_index = DRIFT_MARKET_INDEX.get(market)
    if market_index is None:
        logger.error(f"Unknown market: {market}")
        return None

    url = f"{DLOB_BASE_URL}/l2"
    params = {
        "marketType": "perp",
        "marketIndex": market_index,
        "depth": depth,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Drift L2 fetch error ({market}): {e}")
        return None


def fetch_binance_bbo(symbol: str) -> dict | None:
    """Fetch Binance futures best bid/offer."""
    params = {"symbol": f"{symbol}USDT"}

    try:
        resp = requests.get(BINANCE_BBO_URL, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Binance BBO fetch error ({symbol}): {e}")
        return None


def parse_l2_snapshot(
    l2_data: dict,
    binance_bbo: dict | None,
    market: str,
) -> dict:
    """Parse DLOB L2 response and Binance BBO into a flat snapshot dict."""
    now = datetime.now(timezone.utc)

    bids = l2_data.get("bids", [])
    asks = l2_data.get("asks", [])

    row = {"timestamp": now, "market": market}

    # Parse up to NUM_LEVELS levels for bids and asks
    # DLOB API returns prices in PRICE_PRECISION (1e6) and sizes in raw units
    for i in range(NUM_LEVELS):
        level = i + 1
        if i < len(bids):
            bid = bids[i]
            raw_price = float(bid.get("price", 0))
            raw_size = float(bid.get("size", 0))
            # Normalize: prices > 1e5 are in PRICE_PRECISION format
            price = raw_price / DRIFT_PRICE_PRECISION if raw_price > 1e5 else raw_price
            size = raw_size / 1e9 if raw_size > 1e6 else raw_size
            row[f"drift_bid{level}_price"] = price
            row[f"drift_bid{level}_size"] = size
            row[f"drift_bid{level}_source"] = bid.get("sources", {}).get("dlob", None)
            # Determine source from the sources dict
            sources = bid.get("sources", {})
            if sources.get("dlob"):
                row[f"drift_bid{level}_source"] = "dlob"
            elif sources.get("vamm"):
                row[f"drift_bid{level}_source"] = "vamm"
            else:
                row[f"drift_bid{level}_source"] = "unknown"
        else:
            row[f"drift_bid{level}_price"] = None
            row[f"drift_bid{level}_size"] = None
            row[f"drift_bid{level}_source"] = None

        if i < len(asks):
            ask = asks[i]
            raw_price = float(ask.get("price", 0))
            raw_size = float(ask.get("size", 0))
            price = raw_price / DRIFT_PRICE_PRECISION if raw_price > 1e5 else raw_price
            size = raw_size / 1e9 if raw_size > 1e6 else raw_size
            row[f"drift_ask{level}_price"] = price
            row[f"drift_ask{level}_size"] = size
            sources = ask.get("sources", {})
            if sources.get("dlob"):
                row[f"drift_ask{level}_source"] = "dlob"
            elif sources.get("vamm"):
                row[f"drift_ask{level}_source"] = "vamm"
            else:
                row[f"drift_ask{level}_source"] = "unknown"
        else:
            row[f"drift_ask{level}_price"] = None
            row[f"drift_ask{level}_size"] = None
            row[f"drift_ask{level}_source"] = None

    # Compute derived fields from best bid/ask
    bid1 = row.get("drift_bid1_price")
    ask1 = row.get("drift_ask1_price")

    if bid1 and ask1 and bid1 > 0 and ask1 > 0:
        mid = (bid1 + ask1) / 2
        row["drift_mid"] = mid
        row["drift_spread_bp"] = (ask1 - bid1) / mid * 10000
    else:
        row["drift_mid"] = None
        row["drift_spread_bp"] = None

    # Binance BBO
    if binance_bbo:
        b_bid = float(binance_bbo.get("bidPrice", 0))
        b_ask = float(binance_bbo.get("askPrice", 0))
        row["binance_bid"] = b_bid
        row["binance_ask"] = b_ask
        if b_bid > 0 and b_ask > 0:
            b_mid = (b_bid + b_ask) / 2
            row["binance_mid"] = b_mid
            if row["drift_mid"] is not None and b_mid > 0:
                row["cex_dex_divergence_bp"] = (
                    (row["drift_mid"] - b_mid) / b_mid * 10000
                )
            else:
                row["cex_dex_divergence_bp"] = None
        else:
            row["binance_mid"] = None
            row["cex_dex_divergence_bp"] = None
    else:
        row["binance_bid"] = None
        row["binance_ask"] = None
        row["binance_mid"] = None
        row["cex_dex_divergence_bp"] = None

    return row


def snapshot_schema() -> dict:
    """Return polars schema for L2 snapshot DataFrame."""
    schema = {
        "timestamp": pl.Datetime("ns", "UTC"),
        "market": pl.Utf8,
    }

    for i in range(1, NUM_LEVELS + 1):
        for side in ("bid", "ask"):
            schema[f"drift_{side}{i}_price"] = pl.Float64
            schema[f"drift_{side}{i}_size"] = pl.Float64
            schema[f"drift_{side}{i}_source"] = pl.Utf8

    schema["drift_mid"] = pl.Float64
    schema["drift_spread_bp"] = pl.Float64
    schema["binance_bid"] = pl.Float64
    schema["binance_ask"] = pl.Float64
    schema["binance_mid"] = pl.Float64
    schema["cex_dex_divergence_bp"] = pl.Float64

    return schema


def output_path(market: str, out_dir: Path) -> Path:
    """Generate output path for a market."""
    return out_dir / f"drift_{market.lower()}usdc_l2_snapshots.parquet"


def flush_buffer(buffer: list[dict], market: str, out_dir: Path) -> None:
    """Write buffered snapshots to parquet, appending to existing file."""
    if not buffer:
        return

    schema = snapshot_schema()
    df_new = pl.DataFrame(buffer, schema=schema)

    out = output_path(market, out_dir)

    if out.exists():
        df_existing = pl.read_parquet(out)
        df_merged = pl.concat([df_existing, df_new])
    else:
        df_merged = df_new

    df_merged = df_merged.unique(subset=["timestamp"]).sort("timestamp")
    df_merged.write_parquet(out)
    logger.info(f"Flushed {len(df_new)} snapshots for {market} (total: {len(df_merged)}) → {out}")


def collect_loop(markets: list[str], interval: int, flush_interval: int, out_dir: Path, depth: int):
    """Main collection loop."""
    # Per-market buffers
    buffers: dict[str, list[dict]] = {m: [] for m in markets}
    last_flush = time.monotonic()
    snapshot_count = 0

    logger.info(f"Starting collection: markets={markets}, interval={interval}s, flush_interval={flush_interval}s")

    while not _shutdown:
        cycle_start = time.monotonic()

        for market in markets:
            if _shutdown:
                break

            l2_data = fetch_drift_l2(market, depth=depth)
            binance_bbo = fetch_binance_bbo(market)

            if l2_data is None:
                continue

            row = parse_l2_snapshot(l2_data, binance_bbo, market)
            buffers[market].append(row)

        snapshot_count += 1
        if snapshot_count % 60 == 0:
            total_buffered = sum(len(b) for b in buffers.values())
            logger.info(f"Collected {snapshot_count} cycles, {total_buffered} rows buffered")

        # Periodic flush
        elapsed = time.monotonic() - last_flush
        if elapsed >= flush_interval:
            for market in markets:
                flush_buffer(buffers[market], market, out_dir)
                buffers[market] = []
            last_flush = time.monotonic()

        # Sleep for remainder of interval
        cycle_elapsed = time.monotonic() - cycle_start
        sleep_time = max(0, interval - cycle_elapsed)
        if sleep_time > 0 and not _shutdown:
            time.sleep(sleep_time)

    # Final flush on shutdown
    logger.info("Performing final flush...")
    for market in markets:
        flush_buffer(buffers[market], market, out_dir)
    logger.info("Shutdown complete.")


def main():
    args = parse_args()

    markets = [m.strip().upper() for m in args.markets.split(",")]
    for m in markets:
        if m not in DRIFT_MARKET_INDEX:
            logger.error(f"Unknown market: {m}. Valid: {list(DRIFT_MARKET_INDEX.keys())}")
            sys.exit(1)

    out_dir = Path(args.output_dir) if args.output_dir else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    collect_loop(markets, args.interval, args.flush_interval, out_dir, args.depth)


if __name__ == "__main__":
    main()
