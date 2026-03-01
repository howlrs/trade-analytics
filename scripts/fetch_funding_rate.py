#!/usr/bin/env python3
"""Funding Rate history fetcher for Binance and Bybit futures.

Usage:
    python scripts/fetch_funding_rate.py --exchange binance --symbol BTC/USDT:USDT --start 2025-03-01
    python scripts/fetch_funding_rate.py --exchange bybit --symbol ETH/USDT:USDT --start 2025-03-01
"""

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch Funding Rate history")
    parser.add_argument("--exchange", required=True, choices=["binance", "bybit"])
    parser.add_argument("--symbol", required=True, help="e.g. BTC/USDT:USDT")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD), defaults to now")
    parser.add_argument("--output", default=None, help="Output file path")
    return parser.parse_args()


def create_exchange(exchange_id: str):
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })
    return exchange


def to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_all_funding_rates(
    exchange,
    symbol: str,
    since_ms: int,
    end_ms: int,
) -> list:
    all_data = []
    current_since = since_ms

    while current_since < end_ms:
        try:
            rates = exchange.fetch_funding_rate_history(
                symbol, since=current_since, limit=1000
            )
        except ccxt.NetworkError as e:
            logger.warning(f"Network error, retrying in 5s: {e}")
            time.sleep(5)
            continue
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error: {e}")
            raise

        if not rates:
            logger.info("No more data returned, stopping.")
            break

        # Filter out data beyond end_ms
        rates = [r for r in rates if r["timestamp"] < end_ms]
        if not rates:
            break

        all_data.extend(rates)
        last_ts = rates[-1]["timestamp"]
        logger.info(
            f"Fetched {len(rates)} records, last: {rates[-1].get('datetime', 'N/A')}"
        )

        current_since = last_ts + 1

        if len(rates) < 100:
            break

    return all_data


def to_dataframe(data: list) -> pd.DataFrame:
    records = []
    for r in data:
        records.append({
            "timestamp": r["timestamp"],
            "datetime": r.get("datetime"),
            "symbol": r.get("symbol"),
            "funding_rate": r.get("fundingRate"),
            "mark_price": r.get("markPrice"),
        })

    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    df = df.drop(columns=["datetime"], errors="ignore")
    return df


def output_path(exchange_id: str, symbol: str, custom_path: str = None) -> Path:
    if custom_path:
        return Path(custom_path)
    clean_symbol = symbol.split(":")[0].replace("/", "").lower()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{exchange_id}_{clean_symbol}_funding_rate.parquet"


def main():
    args = parse_args()

    exchange = create_exchange(args.exchange)
    since_ms = to_ms(args.start)
    end_ms = to_ms(args.end) if args.end else int(datetime.now(timezone.utc).timestamp() * 1000)

    logger.info(f"Fetching funding rates for {args.symbol} from {args.exchange} ({args.start} to {args.end or 'now'})")

    data = fetch_all_funding_rates(exchange, args.symbol, since_ms, end_ms)

    if not data:
        logger.warning("No data fetched.")
        return

    df = to_dataframe(data)
    out = output_path(args.exchange, args.symbol, args.output)
    df.to_parquet(out, engine="pyarrow", index=False)

    logger.info(f"Saved {len(df)} records to {out}")
    logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")


if __name__ == "__main__":
    main()
