#!/usr/bin/env python3
"""OHLCV data fetcher for Binance and Bybit futures (linear perpetual).

Usage:
    python scripts/fetch_ohlcv.py --exchange binance --symbol BTC/USDT:USDT --start 2025-03-01 --end 2026-03-01
    python scripts/fetch_ohlcv.py --exchange bybit --symbol ETH/USDT:USDT --timeframe 1h
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
    parser = argparse.ArgumentParser(description="Fetch OHLCV data")
    parser.add_argument("--exchange", required=True, choices=["binance", "bybit"])
    parser.add_argument("--symbol", required=True, help="e.g. BTC/USDT:USDT")
    parser.add_argument("--timeframe", default="1h", help="e.g. 1h, 4h, 1d")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD), defaults to now")
    parser.add_argument("--output", default=None, help="Output file path (auto-generated if omitted)")
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


def fetch_all_ohlcv(
    exchange,
    symbol: str,
    timeframe: str,
    since_ms: int,
    end_ms: int,
    limit: int = 1000,
) -> list:
    all_data = []
    current_since = since_ms

    while current_since < end_ms:
        try:
            ohlcv = exchange.fetch_ohlcv(
                symbol, timeframe, since=current_since, limit=limit
            )
        except ccxt.NetworkError as e:
            logger.warning(f"Network error, retrying in 5s: {e}")
            time.sleep(5)
            continue
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error: {e}")
            raise

        if not ohlcv:
            logger.info("No more data returned, stopping.")
            break

        # Filter out data beyond end_ms
        ohlcv = [row for row in ohlcv if row[0] < end_ms]
        if not ohlcv:
            break

        all_data.extend(ohlcv)
        last_ts = ohlcv[-1][0]
        logger.info(
            f"Fetched {len(ohlcv)} candles, last: {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).isoformat()}"
        )

        # Move past the last candle
        current_since = last_ts + 1

        if len(ohlcv) < limit:
            break

    return all_data


def to_dataframe(data: list) -> pd.DataFrame:
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def output_path(exchange_id: str, symbol: str, timeframe: str, custom_path: str = None) -> Path:
    if custom_path:
        return Path(custom_path)
    # BTC/USDT:USDT -> btcusdt
    clean_symbol = symbol.split(":")[0].replace("/", "").lower()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{exchange_id}_{clean_symbol}_{timeframe}.parquet"


def main():
    args = parse_args()

    exchange = create_exchange(args.exchange)
    since_ms = to_ms(args.start)
    end_ms = to_ms(args.end) if args.end else int(datetime.now(timezone.utc).timestamp() * 1000)

    logger.info(f"Fetching {args.symbol} {args.timeframe} from {args.exchange} ({args.start} to {args.end or 'now'})")

    data = fetch_all_ohlcv(exchange, args.symbol, args.timeframe, since_ms, end_ms)

    if not data:
        logger.warning("No data fetched.")
        return

    df = to_dataframe(data)
    out = output_path(args.exchange, args.symbol, args.timeframe, args.output)
    df.to_parquet(out, engine="pyarrow", index=False)

    logger.info(f"Saved {len(df)} records to {out}")
    logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")


if __name__ == "__main__":
    main()
