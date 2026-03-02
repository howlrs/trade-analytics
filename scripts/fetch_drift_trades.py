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

DATA_API_BASE_URL = "https://data.api.drift.trade"

# Legacy DLOB mapping (kept for reference)
DRIFT_MARKET_INDEX = {
    "SOL": 0,
    "BTC": 1,
    "ETH": 2,
}

DRIFT_MARKET_SYMBOL = {
    "SOL": "SOL-PERP",
    "BTC": "BTC-PERP",
    "ETH": "ETH-PERP",
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
    """Fetch recent trades from Drift Data API.

    Paginates automatically since API returns max 50 records per request.
    """
    symbol = DRIFT_MARKET_SYMBOL.get(market)
    if symbol is None:
        logger.error(f"Unknown market: {market}")
        return []

    all_records: list[dict] = []
    page: str | None = None
    page_size = min(limit, 50)  # API max is 50

    while len(all_records) < limit:
        url = f"{DATA_API_BASE_URL}/market/{symbol}/trades"
        params: dict = {"limit": page_size}
        if page is not None:
            params["page"] = page

        retries = 0
        data = None
        while retries < max_retries:
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                retries += 1
                if retries >= max_retries:
                    logger.error(f"Failed after {max_retries} retries ({market}): {e}")
                    return all_records
                logger.warning(f"Request error, retrying in 2s ({retries}/{max_retries}): {e}")
                time.sleep(2)

        if data is None or not data.get("success"):
            break

        records = data.get("records", [])
        if not records:
            break

        all_records.extend(records)
        next_page = data.get("meta", {}).get("nextPage")
        if next_page is None:
            break
        page = next_page

    return all_records[:limit]


def trades_to_dataframe(trades: list[dict], market: str) -> pl.DataFrame:
    """Convert raw trade records to a polars DataFrame.

    Handles both Data API format (human-readable strings) and legacy DLOB format.
    """
    if not trades:
        return pl.DataFrame()

    rows = []
    for t in trades:
        try:
            ts_raw = t.get("ts") or t.get("timestamp") or t.get("fillerRewardTs")
            if ts_raw is None:
                continue

            ts_val = int(ts_raw)
            # Detect seconds vs milliseconds
            if ts_val < 1e12:
                ts = datetime.fromtimestamp(ts_val, tz=timezone.utc)
            else:
                ts = datetime.fromtimestamp(ts_val / 1000, tz=timezone.utc)

            # Data API returns pre-formatted strings like "84.560990", "32.470000000"
            price_raw = t.get("oraclePrice", t.get("price", "0"))
            price = float(price_raw)
            size_raw = t.get("baseAssetAmountFilled", t.get("size", t.get("baseAssetAmount", "0")))
            size = float(size_raw)

            # Legacy DLOB format uses raw integers (1e9 base, 1e6 price)
            if size > 1e6:
                size = size / 1e9
            if price > 1e6 and market == "SOL":
                price = price / 1e6
            elif price > 1e8:
                price = price / 1e6

            quote_filled = float(t.get("quoteAssetAmountFilled", 0))
            taker_fee = float(t.get("takerFee", 0))
            maker_rebate = float(t.get("makerRebate", 0))

            side = t.get("takerOrderDirection", t.get("side", "unknown"))
            if side == "long":
                side = "buy"
            elif side == "short":
                side = "sell"

            rows.append({
                "timestamp": ts,
                "price": price,
                "size": abs(size),
                "quote_filled": quote_filled,
                "taker_fee": taker_fee,
                "maker_rebate": maker_rebate,
                "side": side,
                "market": market,
                "action_explanation": t.get("actionExplanation", ""),
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
        "quote_filled": pl.Float64,
        "taker_fee": pl.Float64,
        "maker_rebate": pl.Float64,
        "side": pl.Utf8,
        "market": pl.Utf8,
        "action_explanation": pl.Utf8,
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
