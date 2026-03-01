#!/usr/bin/env python3
"""Stablecoin market cap fetcher via DefiLlama.

Fetches total stablecoin market cap and individual USDT/USDC circulating supply.
No API key required.

Usage:
    python scripts/fetch_stablecoin.py
    python scripts/fetch_stablecoin.py --start 2025-03-01
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
BASE_URL = "https://stablecoins.llama.fi"

STABLECOINS = {
    "usdt": 1,
    "usdc": 2,
}


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch stablecoin market cap data")
    parser.add_argument("--start", default="2025-03-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    return parser.parse_args()


def fetch_total_stablecoin_mcap() -> pl.DataFrame:
    """Fetch total stablecoin market cap (all chains)."""
    url = f"{BASE_URL}/stablecoincharts/all"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        logger.warning("No total stablecoin data returned")
        return pl.DataFrame()

    records = []
    for entry in data:
        total = entry.get("totalCirculatingUSD", {}).get("peggedUSD", 0)
        records.append({
            "date": entry["date"],
            "total_mcap_usd": total,
        })

    df = pl.DataFrame(records)
    df = df.with_columns(
        pl.from_epoch(pl.col("date").cast(pl.Int64), time_unit="s")
        .dt.replace_time_zone("UTC")
        .alias("timestamp"),
    ).select("timestamp", "total_mcap_usd")

    df = df.unique(subset=["timestamp"]).sort("timestamp")
    logger.info(f"Total stablecoin MCap: {df.height} records, {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    return df


def fetch_individual_stablecoin(name: str, coin_id: int) -> pl.DataFrame:
    """Fetch individual stablecoin circulating supply."""
    url = f"{BASE_URL}/stablecoin/{coin_id}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    chain_data = data.get("chainBalances", {})

    # Aggregate across all chains
    ts_totals: dict[int, float] = {}
    for chain_info in chain_data.values():
        for entry in chain_info.get("tokens", []):
            ts = entry["date"]
            amount = entry.get("circulating", {}).get("peggedUSD", 0)
            ts_totals[ts] = ts_totals.get(ts, 0) + amount

    if not ts_totals:
        logger.warning(f"No data for {name}")
        return pl.DataFrame()

    records = [{"date": ts, "circulating_usd": val} for ts, val in ts_totals.items()]
    df = pl.DataFrame(records)
    df = df.with_columns(
        pl.from_epoch(pl.col("date").cast(pl.Int64), time_unit="s")
        .dt.replace_time_zone("UTC")
        .alias("timestamp"),
    ).select("timestamp", "circulating_usd")

    df = df.unique(subset=["timestamp"]).sort("timestamp")
    logger.info(f"{name.upper()}: {df.height} records, {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    return df


def main():
    args = parse_args()
    out_dir = Path(args.output_dir) if args.output_dir else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    start_filter = pl.lit(args.start).str.to_datetime().dt.replace_time_zone("UTC")

    # Total stablecoin market cap
    df_total = fetch_total_stablecoin_mcap()
    if not df_total.is_empty():
        df_total = df_total.filter(pl.col("timestamp") >= start_filter)
        null_counts = df_total.null_count()
        for col in df_total.columns:
            n = null_counts[col][0]
            if n > 0:
                logger.warning(f"total_mcap: Column '{col}' has {n} null values")
        out_path = out_dir / "defillama_stablecoin_mcap.parquet"
        df_total.write_parquet(out_path)
        logger.info(f"Saved {df_total.height} records to {out_path}")

    # Individual stablecoins
    for name, coin_id in STABLECOINS.items():
        df = fetch_individual_stablecoin(name, coin_id)
        if df.is_empty():
            continue
        df = df.filter(pl.col("timestamp") >= start_filter)
        null_counts = df.null_count()
        for col in df.columns:
            n = null_counts[col][0]
            if n > 0:
                logger.warning(f"{name}: Column '{col}' has {n} null values")
        out_path = out_dir / f"defillama_{name}_mcap.parquet"
        df.write_parquet(out_path)
        logger.info(f"Saved {df.height} records to {out_path}")


if __name__ == "__main__":
    main()
