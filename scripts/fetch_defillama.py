#!/usr/bin/env python3
"""DefiLlama TVL and DEX volume fetcher.

Fetches historical TVL for specified chains and DEX trading volume.
No API key required.

Usage:
    python scripts/fetch_defillama.py
    python scripts/fetch_defillama.py --chains Ethereum BSC Solana
"""

import argparse
import logging
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
BASE_URL = "https://api.llama.fi"


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch DefiLlama data")
    parser.add_argument(
        "--chains", nargs="+", default=["Ethereum"], help="Chain names"
    )
    parser.add_argument(
        "--start", default="2025-03-01", help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument("--output-dir", default=None, help="Output directory")
    return parser.parse_args()


def fetch_chain_tvl(chain: str) -> pl.DataFrame:
    """Fetch historical TVL for a chain."""
    url = f"{BASE_URL}/v2/historicalChainTvl/{chain}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        logger.warning(f"No TVL data for {chain}")
        return pl.DataFrame()

    df = pl.DataFrame(data)
    df = df.with_columns(
        pl.from_epoch(pl.col("date"), time_unit="s").alias("timestamp"),
    ).select("timestamp", "tvl")

    logger.info(f"{chain} TVL: {df.height} records, {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    return df


def fetch_dex_volume_daily() -> pl.DataFrame:
    """Fetch historical daily DEX trading volume (all chains)."""
    url = "https://api.llama.fi/overview/dexs?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true&dataType=dailyVolume"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    chart_data = data.get("totalDataChart", [])
    if not chart_data:
        logger.warning("No DEX volume data")
        return pl.DataFrame()

    records = [{"date": r[0], "dex_volume": r[1]} for r in chart_data]
    df = pl.DataFrame(records)
    df = df.with_columns(
        pl.from_epoch(pl.col("date"), time_unit="s").alias("timestamp"),
    ).select("timestamp", "dex_volume")

    logger.info(f"DEX volume: {df.height} records, {df['timestamp'].min()} ~ {df['timestamp'].max()}")
    return df


def main():
    args = parse_args()
    out_dir = Path(args.output_dir) if args.output_dir else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    start_ts = datetime.strptime(args.start, "%Y-%m-%d")

    # Chain TVL
    for chain in args.chains:
        df = fetch_chain_tvl(chain)
        if df.is_empty():
            continue
        df = df.filter(pl.col("timestamp") >= start_ts)
        out_path = out_dir / f"defillama_{chain.lower()}_tvl.parquet"
        df.write_parquet(out_path)
        logger.info(f"Saved {df.height} records to {out_path}")

    # DEX Volume
    df_dex = fetch_dex_volume_daily()
    if not df_dex.is_empty():
        df_dex = df_dex.filter(pl.col("timestamp") >= start_ts)
        out_path = out_dir / "defillama_dex_volume.parquet"
        df_dex.write_parquet(out_path)
        logger.info(f"Saved {df_dex.height} records to {out_path}")


if __name__ == "__main__":
    main()
