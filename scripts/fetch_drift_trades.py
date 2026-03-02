#!/usr/bin/env python3
"""Drift Protocol trade history fetcher via DLOB REST API.

Fetches recent trades for Drift perpetual markets and saves to parquet.

Usage:
    python scripts/fetch_drift_trades.py --symbol SOL --limit 100
    python scripts/fetch_drift_trades.py --symbol SOL,ETH,BTC --limit 1000
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

DLOB_BASE_URL = "https://dlob.drift.trade"

DRIFT_MARKET_INDEX = {
    "SOL": 0,
    "BTC": 1,
    "ETH": 2,
}


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch Drift trade history")
    parser.add_argument(
        "--symbol", default="SOL", help="Comma-separated symbols (e.g. SOL,ETH,BTC)"
    )
    parser.add_argument(
        "--limit", type=int, default=1000, help="Number of trades to fetch (default: 1000)"
    )
    parser.add_argument(
        "--output-dir", default=None, help="Output directory (default: data/)"
    )
    return parser.parse_args()


def fetch_trades(market: str, limit: int = 1000, max_retries: int = 3) -> list[dict]:
    """Fetch recent trades from DLOB REST API."""
    market_index = DRIFT_MARKET_INDEX.get(market)
    if market_index is None:
        logger.error(f"Unknown market: {market}")
        return []

    url = f"{DLOB_BASE_URL}/trades"
    params = {
        "marketType": "perp",
        "marketIndex": market_index,
        "limit": limit,
    }

    retries = 0
    while retries < max_retries:
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            # Some endpoints wrap in {"trades": [...]}
            if isinstance(data, dict) and "trades" in data:
                return data["trades"]
            logger.warning(f"Unexpected response format for {market}: {type(data)}")
            return data if isinstance(data, list) else []
        except Exception as e:
            retries += 1
            if retries >= max_retries:
                logger.error(f"Failed after {max_retries} retries ({market}): {e}")
                return []
            logger.warning(f"Request error, retrying in 2s ({retries}/{max_retries}): {e}")
            time.sleep(2)

    return []


def trades_to_dataframe(trades: list[dict], market: str) -> pl.DataFrame:
    """Convert raw trade records to a polars DataFrame."""
    if not trades:
        return pl.DataFrame()

    rows = []
    for t in trades:
        try:
            # DLOB trades API response fields vary; handle common formats
            ts_raw = t.get("ts") or t.get("timestamp") or t.get("fillerRewardTs")
            if ts_raw is None:
                continue

            ts_val = int(ts_raw)
            # Detect seconds vs milliseconds
            if ts_val < 1e12:
                ts = datetime.fromtimestamp(ts_val, tz=timezone.utc)
            else:
                ts = datetime.fromtimestamp(ts_val / 1000, tz=timezone.utc)

            price = float(t.get("price", t.get("oraclePrice", 0)))
            size = float(t.get("baseAssetAmountFilled", t.get("size", t.get("baseAssetAmount", 0))))

            # Normalize size (Drift uses 1e9 precision for base asset)
            if size > 1e6:
                size = size / 1e9

            # Normalize price (Drift uses 1e6 precision)
            if price > 1e6 and market == "SOL":
                price = price / 1e6
            elif price > 1e8:
                price = price / 1e6

            side = t.get("takerOrderDirection", t.get("side", "unknown"))
            if side == "long":
                side = "buy"
            elif side == "short":
                side = "sell"

            rows.append({
                "timestamp": ts,
                "price": price,
                "size": abs(size),
                "side": side,
                "market": market,
                "tx_sig": t.get("txSig", t.get("txSignature", "")),
            })
        except (ValueError, TypeError) as e:
            logger.warning(f"Skipping malformed trade record: {e}")
            continue

    if not rows:
        return pl.DataFrame()

    schema = {
        "timestamp": pl.Datetime("ns", "UTC"),
        "price": pl.Float64,
        "size": pl.Float64,
        "side": pl.Utf8,
        "market": pl.Utf8,
        "tx_sig": pl.Utf8,
    }

    df = pl.DataFrame(rows, schema=schema)
    df = df.unique(subset=["timestamp", "tx_sig"]).sort("timestamp")
    return df


def output_path(market: str, out_dir: Path) -> Path:
    return out_dir / f"drift_{market.lower()}usdc_trades.parquet"


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbol.split(",")]
    out_dir = Path(args.output_dir) if args.output_dir else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    for symbol in symbols:
        if symbol not in DRIFT_MARKET_INDEX:
            logger.error(f"Unknown symbol: {symbol}. Valid: {list(DRIFT_MARKET_INDEX.keys())}")
            continue

        logger.info(f"Fetching trades for {symbol}-PERP (limit={args.limit})...")
        trades = fetch_trades(symbol, limit=args.limit)

        if not trades:
            logger.warning(f"No trades returned for {symbol}")
            continue

        logger.info(f"Received {len(trades)} raw trade records for {symbol}")
        df = trades_to_dataframe(trades, symbol)

        if df.is_empty():
            logger.warning(f"No valid trades parsed for {symbol}")
            continue

        out = output_path(symbol, out_dir)

        # Append if file exists
        if out.exists():
            df_existing = pl.read_parquet(out)
            df = pl.concat([df_existing, df])
            df = df.unique(subset=["timestamp", "tx_sig"]).sort("timestamp")

        df.write_parquet(out)
        logger.info(f"Saved {len(df)} trades to {out}")
        logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")


if __name__ == "__main__":
    main()
