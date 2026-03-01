#!/usr/bin/env python3
"""Fear & Greed Index fetcher.

Fetches full history from alternative.me API (daily, since 2018).
No API key required.

Usage:
    python scripts/fetch_fear_greed.py
    python scripts/fetch_fear_greed.py --start 2025-03-01
"""

import argparse
import logging
from pathlib import Path

import polars as pl
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
API_URL = "https://api.alternative.me/fng/"


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch Fear & Greed Index")
    parser.add_argument("--start", default="2025-03-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--output", default=None, help="Output file path")
    return parser.parse_args()


def fetch_fear_greed() -> pl.DataFrame:
    """Fetch full Fear & Greed Index history."""
    params = {"limit": 0, "format": "json"}
    resp = requests.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    records = data.get("data", [])
    if not records:
        logger.warning("No Fear & Greed data returned")
        return pl.DataFrame()

    logger.info(f"Fetched {len(records)} records from API")

    df = pl.DataFrame(records)
    df = df.with_columns(
        pl.from_epoch(pl.col("timestamp").cast(pl.Int64), time_unit="s")
        .dt.replace_time_zone("UTC")
        .alias("timestamp"),
        pl.col("value").cast(pl.Int32).alias("value"),
        pl.col("value_classification").alias("classification"),
    ).select("timestamp", "value", "classification")

    df = df.unique(subset=["timestamp"]).sort("timestamp")

    logger.info(f"Parsed {df.height} records, {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    return df


def main():
    args = parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    df = fetch_fear_greed()
    if df.is_empty():
        logger.warning("No data fetched.")
        return

    df = df.filter(pl.col("timestamp") >= pl.lit(args.start).str.to_datetime().dt.replace_time_zone("UTC"))

    # Report nulls
    null_counts = df.null_count()
    for col in df.columns:
        n = null_counts[col][0]
        if n > 0:
            logger.warning(f"Column '{col}' has {n} null values")

    out_path = Path(args.output) if args.output else DATA_DIR / "fear_greed_index.parquet"
    df.write_parquet(out_path)
    logger.info(f"Saved {df.height} records to {out_path}")
    logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")


if __name__ == "__main__":
    main()
