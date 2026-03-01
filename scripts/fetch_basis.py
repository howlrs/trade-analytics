#!/usr/bin/env python3
"""Futures basis (Mark Price - Index Price) fetcher from Binance.

Fetches mark price klines and index price klines, then computes basis and basis_rate.
No API key required.

Usage:
    python scripts/fetch_basis.py
    python scripts/fetch_basis.py --start 2025-03-01 --symbols BTC ETH SOL SUI
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
SYMBOLS = ["BTC", "ETH", "SOL", "SUI"]
INTERVAL_MS = 3_600_000  # 1h


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch futures basis data")
    parser.add_argument("--start", default="2025-03-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD), defaults to now")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS, help="Symbols to fetch")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    return parser.parse_args()


def to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines(endpoint: str, symbol: str, since_ms: int, end_ms: int) -> list:
    """Fetch klines with forward pagination.

    Note: markPriceKlines uses 'symbol' param, indexPriceKlines uses 'pair' param.
    """
    url = f"{BASE_URL}{endpoint}"
    all_data = []
    current_since = since_ms
    limit = 1500
    max_retries = 3

    # indexPriceKlines uses 'pair' instead of 'symbol'
    sym_key = "pair" if "index" in endpoint else "symbol"

    while current_since < end_ms:
        params = {
            sym_key: f"{symbol}USDT",
            "interval": "1h",
            "startTime": current_since,
            "endTime": end_ms,
            "limit": limit,
        }

        retries = 0
        while retries < max_retries:
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                retries += 1
                if retries >= max_retries:
                    logger.error(f"Failed after {max_retries} retries: {e}")
                    return all_data
                logger.warning(f"Request error, retrying in 2s ({retries}/{max_retries}): {e}")
                time.sleep(2)
        else:
            break

        if not data:
            break

        # Kline format: [open_time, open, high, low, close, ...]
        for row in data:
            ts = int(row[0])
            if ts < end_ms:
                all_data.append((ts, float(row[4])))  # close price only

        last_ts = int(data[-1][0])
        logger.info(
            f"  Fetched {len(data)} klines, last: "
            f"{datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).isoformat()}"
        )

        current_since = last_ts + INTERVAL_MS

        if len(data) < limit:
            break

        time.sleep(0.2)

    return all_data


def fetch_basis_for_symbol(symbol: str, since_ms: int, end_ms: int) -> pl.DataFrame:
    """Fetch mark and index price klines, compute basis."""
    logger.info(f"Fetching mark price klines for {symbol}USDT...")
    mark_data = fetch_klines("/fapi/v1/markPriceKlines", symbol, since_ms, end_ms)

    logger.info(f"Fetching index price klines for {symbol}USDT...")
    index_data = fetch_klines("/fapi/v1/indexPriceKlines", symbol, since_ms, end_ms)

    if not mark_data or not index_data:
        logger.warning(f"No data for {symbol}")
        return pl.DataFrame()

    df_mark = pl.DataFrame(mark_data, schema=["timestamp", "mark_close"], orient="row")
    df_index = pl.DataFrame(index_data, schema=["timestamp", "index_close"], orient="row")

    # Convert timestamps
    df_mark = df_mark.with_columns(
        pl.from_epoch(pl.col("timestamp"), time_unit="ms").alias("timestamp")
    ).unique(subset=["timestamp"]).sort("timestamp")

    df_index = df_index.with_columns(
        pl.from_epoch(pl.col("timestamp"), time_unit="ms").alias("timestamp")
    ).unique(subset=["timestamp"]).sort("timestamp")

    # Join mark and index
    df = df_mark.join(df_index, on="timestamp", how="inner").sort("timestamp")

    # Compute basis
    df = df.with_columns(
        (pl.col("mark_close") - pl.col("index_close")).alias("basis"),
        ((pl.col("mark_close") - pl.col("index_close")) / pl.col("index_close")).alias("basis_rate"),
    )

    # Cast timestamp to datetime[ns, UTC]
    if df["timestamp"].dtype != pl.Datetime("ns", "UTC"):
        df = df.with_columns(
            pl.col("timestamp").cast(pl.Datetime("ns")).dt.replace_time_zone("UTC")
        )

    logger.info(f"{symbol}: {df.height} records, {df['timestamp'].min()} ~ {df['timestamp'].max()}")

    # Report nulls
    null_counts = df.null_count()
    for col in df.columns:
        n = null_counts[col][0]
        if n > 0:
            logger.warning(f"{symbol}: Column '{col}' has {n} null values")

    return df


def main():
    args = parse_args()
    out_dir = Path(args.output_dir) if args.output_dir else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    since_ms = to_ms(args.start)
    end_ms = to_ms(args.end) if args.end else int(datetime.now(timezone.utc).timestamp() * 1000)

    for symbol in args.symbols:
        symbol = symbol.upper()
        logger.info(f"=== {symbol}USDT ===")

        df = fetch_basis_for_symbol(symbol, since_ms, end_ms)
        if df.is_empty():
            continue

        out_path = out_dir / f"binance_{symbol.lower()}usdt_basis_1h.parquet"
        df.write_parquet(out_path)
        logger.info(f"Saved {df.height} records to {out_path}")


if __name__ == "__main__":
    main()
