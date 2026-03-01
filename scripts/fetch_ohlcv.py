#!/usr/bin/env python3
"""OHLCV data fetcher for Binance and Bybit futures (linear perpetual).

Usage:
    python scripts/fetch_ohlcv.py --exchange binance --symbol BTC/USDT:USDT --start 2025-03-01 --end 2026-03-01
    python scripts/fetch_ohlcv.py --exchange bybit --symbol ETH/USDT:USDT --timeframe 1h --start 2025-03-01
"""

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

BYBIT_INTERVAL_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W", "1M": "M",
}

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000,
}


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
    """Fetch OHLCV using ccxt (works well for Binance)."""
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

        ohlcv.sort(key=lambda x: x[0])
        ohlcv = [row for row in ohlcv if row[0] < end_ms]
        if not ohlcv:
            break

        all_data.extend(ohlcv)
        last_ts = ohlcv[-1][0]
        logger.info(
            f"Fetched {len(ohlcv)} candles, last: {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).isoformat()}"
        )

        current_since = last_ts + 1

        if len(ohlcv) < limit:
            break

    return all_data


def fetch_bybit_ohlcv_rest(
    symbol_raw: str,
    timeframe: str,
    since_ms: int,
    end_ms: int,
) -> list:
    """Fetch OHLCV directly from Bybit v5 REST API (handles descending order)."""
    pair = symbol_raw.split(":")[0].replace("/", "")
    interval = BYBIT_INTERVAL_MAP.get(timeframe, "60")
    interval_ms = INTERVAL_MS.get(timeframe, 3_600_000)
    url = "https://api.bybit.com/v5/market/kline"
    all_data = []
    limit = 1000

    # Bybit returns data newest-first, so we iterate with end sliding backward
    current_end = end_ms

    while current_end > since_ms:
        params = {
            "category": "linear",
            "symbol": pair,
            "interval": interval,
            "start": since_ms,
            "end": current_end,
            "limit": limit,
        }

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            logger.warning(f"Request error, retrying in 5s: {e}")
            time.sleep(5)
            continue

        if result.get("retCode") != 0:
            logger.error(f"Bybit API error: {result.get('retMsg')}")
            break

        items = result.get("result", {}).get("list", [])
        if not items:
            break

        # Convert to standard format [timestamp, open, high, low, close, volume]
        batch = []
        for item in items:
            ts = int(item[0])
            if since_ms <= ts < end_ms:
                batch.append([ts, float(item[1]), float(item[2]), float(item[3]), float(item[4]), float(item[5])])

        batch.sort(key=lambda x: x[0])
        if not batch:
            break

        all_data.extend(batch)
        oldest_ts = batch[0][0]
        logger.info(
            f"Fetched {len(batch)} candles, oldest: {datetime.fromtimestamp(oldest_ts/1000, tz=timezone.utc).isoformat()}"
        )

        # Move end back to just before the oldest item
        current_end = oldest_ts - 1

        if len(items) < limit:
            break

        time.sleep(0.2)

    all_data.sort(key=lambda x: x[0])
    return all_data


def to_dataframe(data: list) -> pd.DataFrame:
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def output_path(exchange_id: str, symbol: str, timeframe: str, custom_path: str = None) -> Path:
    if custom_path:
        return Path(custom_path)
    clean_symbol = symbol.split(":")[0].replace("/", "").lower()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{exchange_id}_{clean_symbol}_{timeframe}.parquet"


def main():
    args = parse_args()

    since_ms = to_ms(args.start)
    end_ms = to_ms(args.end) if args.end else int(datetime.now(timezone.utc).timestamp() * 1000)

    logger.info(f"Fetching {args.symbol} {args.timeframe} from {args.exchange} ({args.start} to {args.end or 'now'})")

    if args.exchange == "bybit":
        data = fetch_bybit_ohlcv_rest(args.symbol, args.timeframe, since_ms, end_ms)
    else:
        exchange = create_exchange(args.exchange)
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
