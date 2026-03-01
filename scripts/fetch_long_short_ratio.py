#!/usr/bin/env python3
"""Binance Futures Long/Short ratio fetcher.

Fetches top trader and global long/short account ratios.
Note: Binance retains only the latest 30 days of data.

Usage:
    python scripts/fetch_long_short_ratio.py --symbol BTC/USDT:USDT
    python scripts/fetch_long_short_ratio.py --symbol ETH/USDT:USDT --period 1h
"""

import argparse
import logging
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
BASE_URL = "https://fapi.binance.com"

ENDPOINTS = {
    "global": "/futures/data/globalLongShortAccountRatio",
    "top_account": "/futures/data/topLongShortAccountRatio",
    "top_position": "/futures/data/topLongShortPositionRatio",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch Long/Short ratio from Binance")
    parser.add_argument("--symbol", required=True, help="e.g. BTC/USDT:USDT")
    parser.add_argument("--period", default="4h", help="5m,15m,30m,1h,2h,4h,6h,12h,1d")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    return parser.parse_args()


def fetch_ratio(endpoint: str, pair: str, period: str) -> list:
    """Fetch all available ratio data (up to 30 days) by paginating."""
    url = BASE_URL + endpoint
    all_data = []
    limit = 500
    end_time = None

    while True:
        params = {"symbol": pair, "period": period, "limit": limit}
        if end_time:
            params["endTime"] = end_time

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"Request error, retrying in 5s: {e}")
            time.sleep(5)
            continue

        if not data:
            break

        all_data.extend(data)
        first_ts = data[0]["timestamp"]
        logger.info(
            f"Fetched {len(data)} records, earliest: "
            f"{datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).isoformat()}"
        )

        if len(data) < limit:
            break

        end_time = first_ts - 1
        time.sleep(0.5)

    return all_data


def to_df(data: list) -> pl.DataFrame:
    records = []
    for r in data:
        rec = {
            "timestamp": r["timestamp"],
            "long_short_ratio": float(r["longShortRatio"]),
        }
        if "longAccount" in r:
            rec["long_account"] = float(r["longAccount"])
            rec["short_account"] = float(r["shortAccount"])
        if "longPositions" in r:
            rec["long_position"] = float(r.get("longPositions", 0))
            rec["short_position"] = float(r.get("shortPositions", 0))
        records.append(rec)

    df = pl.DataFrame(records)
    df = df.with_columns(
        pl.from_epoch(pl.col("timestamp"), time_unit="ms").alias("timestamp")
    )
    return df.unique(subset=["timestamp"]).sort("timestamp")


def main():
    args = parse_args()
    pair = args.symbol.split(":")[0].replace("/", "")
    clean_symbol = pair.lower()
    out_dir = Path(args.output_dir) if args.output_dir else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    for kind, endpoint in ENDPOINTS.items():
        logger.info(f"Fetching {kind} L/S ratio for {pair} (period={args.period})")
        data = fetch_ratio(endpoint, pair, args.period)
        if not data:
            logger.warning(f"No data for {kind}")
            continue

        df = to_df(data)
        out_path = out_dir / f"binance_{clean_symbol}_ls_{kind}.parquet"
        df.write_parquet(out_path)
        logger.info(f"Saved {df.height} records to {out_path}")
        logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")


if __name__ == "__main__":
    main()
