#!/usr/bin/env python3
"""Open Interest history fetcher for Binance and Bybit futures.

Binance: /futures/data/openInterestHist (last 30 days only)
Bybit: /v5/market/open-interest (cursor pagination, 1 year possible)

Usage:
    python scripts/fetch_open_interest.py --exchange binance --symbol BTC/USDT:USDT
    python scripts/fetch_open_interest.py --exchange bybit --symbol ETH/USDT:USDT --start 2025-03-01
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


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch Open Interest history")
    parser.add_argument("--exchange", required=True, choices=["binance", "bybit"])
    parser.add_argument("--symbol", required=True, help="e.g. BTC/USDT:USDT")
    parser.add_argument("--interval", default="4h", help="e.g. 4h, 1h, 1d")
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", default=None, help="Output file path")
    return parser.parse_args()


def to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# --- Binance: REST API direct (ccxt doesn't support OI history well) ---

BINANCE_OI_INTERVAL_MAP = {
    "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h", "1d": "1d",
}


def fetch_binance_oi(symbol_raw: str, interval: str, start_ms: int = None, end_ms: int = None) -> list:
    # BTC/USDT:USDT -> BTCUSDT
    pair = symbol_raw.split(":")[0].replace("/", "")
    period = BINANCE_OI_INTERVAL_MAP.get(interval, "4h")
    url = "https://fapi.binance.com/futures/data/openInterestHist"
    all_data = []
    limit = 500

    params = {"symbol": pair, "period": period, "limit": limit}
    if start_ms:
        params["startTime"] = start_ms
    if end_ms:
        params["endTime"] = end_ms

    while True:
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
        last_ts = data[-1]["timestamp"]
        logger.info(
            f"Fetched {len(data)} OI records, last: {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).isoformat()}"
        )

        if len(data) < limit:
            break

        params["startTime"] = last_ts + 1
        time.sleep(0.5)

    return all_data


def binance_oi_to_df(data: list) -> pd.DataFrame:
    records = []
    for r in data:
        records.append({
            "timestamp": r["timestamp"],
            "open_interest": float(r["sumOpenInterest"]),
            "open_interest_value": float(r["sumOpenInterestValue"]),
        })
    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


# --- Bybit: REST API direct (cursor pagination) ---

BYBIT_OI_INTERVAL_MAP = {
    "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "4h": "4h", "1d": "1d",
}


def fetch_bybit_oi(symbol_raw: str, interval: str, start_ms: int = None, end_ms: int = None) -> list:
    pair = symbol_raw.split(":")[0].replace("/", "")
    interval_time = BYBIT_OI_INTERVAL_MAP.get(interval, "4h")
    url = "https://api.bybit.com/v5/market/open-interest"
    all_data = []
    cursor = None
    limit = 200

    while True:
        params = {
            "category": "linear",
            "symbol": pair,
            "intervalTime": interval_time,
            "limit": limit,
        }
        if start_ms:
            params["startTime"] = start_ms
        if end_ms:
            params["endTime"] = end_ms
        if cursor:
            params["cursor"] = cursor

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

        all_data.extend(items)
        logger.info(
            f"Fetched {len(items)} OI records, last: {datetime.fromtimestamp(int(items[-1]['timestamp'])/1000, tz=timezone.utc).isoformat()}"
        )

        next_cursor = result.get("result", {}).get("nextPageCursor", "")
        if not next_cursor or len(items) < limit:
            break

        cursor = next_cursor
        time.sleep(0.3)

    return all_data


def bybit_oi_to_df(data: list) -> pd.DataFrame:
    records = []
    for r in data:
        records.append({
            "timestamp": int(r["timestamp"]),
            "open_interest": float(r["openInterest"]),
        })
    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def output_path(exchange_id: str, symbol: str, custom_path: str = None) -> Path:
    if custom_path:
        return Path(custom_path)
    clean_symbol = symbol.split(":")[0].replace("/", "").lower()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{exchange_id}_{clean_symbol}_open_interest.parquet"


def main():
    args = parse_args()

    start_ms = to_ms(args.start) if args.start else None
    end_ms = to_ms(args.end) if args.end else None

    logger.info(f"Fetching OI for {args.symbol} from {args.exchange} (interval={args.interval})")

    if args.exchange == "binance":
        data = fetch_binance_oi(args.symbol, args.interval, start_ms, end_ms)
        if not data:
            logger.warning("No data fetched.")
            return
        df = binance_oi_to_df(data)
    elif args.exchange == "bybit":
        data = fetch_bybit_oi(args.symbol, args.interval, start_ms, end_ms)
        if not data:
            logger.warning("No data fetched.")
            return
        df = bybit_oi_to_df(data)

    out = output_path(args.exchange, args.symbol, args.output)
    df.to_parquet(out, engine="pyarrow", index=False)

    logger.info(f"Saved {len(df)} records to {out}")
    logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")


if __name__ == "__main__":
    main()
