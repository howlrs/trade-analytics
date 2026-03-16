#!/usr/bin/env python3
"""Bulk fetch historical Drift Protocol data from Data API.

Fetches Trades, Candles (OHLCV), and Funding Rates for SOL-PERP.

Usage:
    python scripts/fetch_drift_historical.py --type trades --start 2023-01-01
    python scripts/fetch_drift_historical.py --type candles --start 2022-11-15
    python scripts/fetch_drift_historical.py --type funding --start 2023-01-01
    python scripts/fetch_drift_historical.py --all --start 2023-01-01
"""

import argparse
import csv
import io
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BASE_URL = "https://data.api.drift.trade"
SYMBOL = "SOL-PERP"


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch Drift historical data")
    parser.add_argument(
        "--type",
        choices=["trades", "candles", "funding"],
        help="Data type to fetch",
    )
    parser.add_argument("--all", action="store_true", help="Fetch all data types")
    parser.add_argument(
        "--start",
        type=str,
        default="2023-01-01",
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--symbol",
        default="SOL-PERP",
        help="Market symbol (default: SOL-PERP)",
    )
    return parser.parse_args()


# --- Trades ---


def fetch_trades_day(symbol: str, d: date) -> pl.DataFrame:
    """Fetch all trades for a single day via CSV endpoint."""
    all_rows: list[dict] = []

    for page in [1, 2]:
        url = f"{BASE_URL}/market/{symbol}/trades/{d.year}/{d.month}/{d.day}"
        params = {"format": "csv", "page": page}

        try:
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code == 500:
                if page == 1:
                    logger.warning(f"  500 error for {d} page {page}, skipping day")
                break
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"  Error fetching {d} page {page}: {e}")
            break

        text = resp.text.strip()
        if not text:
            break

        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            break
        all_rows.extend(rows)

        # If fewer than 4999 data rows, no next page
        if len(rows) < 4999:
            break

    if not all_rows:
        return pl.DataFrame()

    records = []
    for r in all_rows:
        try:
            ts_val = int(r["ts"])
            records.append(
                {
                    "timestamp": datetime.fromtimestamp(ts_val, tz=timezone.utc),
                    "price": float(r["oraclePrice"]),
                    "size": float(r["baseAssetAmountFilled"]),
                    "quote_filled": float(r["quoteAssetAmountFilled"]),
                    "taker_fee": float(r["takerFee"]),
                    "maker_rebate": float(r["makerRebate"]),
                    "side": "buy" if r["takerOrderDirection"] == "long" else "sell",
                    "action_explanation": r["actionExplanation"],
                    "tx_sig": r["txSig"],
                }
            )
        except (ValueError, KeyError):
            continue

    if not records:
        return pl.DataFrame()

    schema = {
        "timestamp": pl.Datetime("ns", "UTC"),
        "price": pl.Float64,
        "size": pl.Float64,
        "quote_filled": pl.Float64,
        "taker_fee": pl.Float64,
        "maker_rebate": pl.Float64,
        "side": pl.Utf8,
        "action_explanation": pl.Utf8,
        "tx_sig": pl.Utf8,
    }
    return pl.DataFrame(records, schema=schema)


def fetch_all_trades(symbol: str, start: date, end: date):
    """Fetch trades day by day and save to parquet."""
    out_path = DATA_DIR / f"drift_{symbol.replace('-', '_').lower()}_trades_historical.parquet"

    # Resume from existing file
    existing_dates: set[date] = set()
    df_existing = None
    if out_path.exists():
        df_existing = pl.read_parquet(out_path)
        existing_dates = set(
            df_existing["timestamp"]
            .cast(pl.Date)
            .unique()
            .to_list()
        )
        logger.info(f"Existing file has {len(df_existing)} rows, {len(existing_dates)} days")

    current = start
    total_new = 0
    batch: list[pl.DataFrame] = []
    batch_count = 0

    while current <= end:
        if current in existing_dates:
            current += timedelta(days=1)
            continue

        df_day = fetch_trades_day(symbol, current)
        if not df_day.is_empty():
            batch.append(df_day)
            total_new += len(df_day)
            logger.info(f"  {current}: {len(df_day)} trades (total new: {total_new})")
        else:
            logger.debug(f"  {current}: no trades")

        batch_count += 1

        # Flush every 30 days
        if batch_count % 30 == 0 and batch:
            _flush_trades(out_path, df_existing, batch)
            df_existing = pl.read_parquet(out_path)
            batch = []
            logger.info(f"  Checkpoint saved: {len(df_existing)} total rows")

        current += timedelta(days=1)
        time.sleep(0.2)  # rate limit

    # Final flush
    if batch:
        _flush_trades(out_path, df_existing, batch)

    if out_path.exists():
        df_final = pl.read_parquet(out_path)
        logger.info(f"Trades complete: {len(df_final)} rows, {out_path}")
        logger.info(
            f"Date range: {df_final['timestamp'].min()} to {df_final['timestamp'].max()}"
        )


def _flush_trades(out_path: Path, df_existing: pl.DataFrame | None, batch: list[pl.DataFrame]):
    parts = batch.copy()
    if df_existing is not None and not df_existing.is_empty():
        parts.insert(0, df_existing)
    df_all = pl.concat(parts)
    df_all = df_all.unique(subset=["timestamp", "tx_sig"]).sort("timestamp")
    df_all.write_parquet(out_path)


# --- Candles ---


def fetch_all_candles(symbol: str, start: date, end: date):
    """Fetch 1h candles using cursor-based pagination.

    The API's startTs parameter acts as a cursor: it returns records
    *before* the given timestamp, newest-first. We paginate backwards
    from `end` to `start`.
    """
    out_path = DATA_DIR / f"drift_{symbol.replace('-', '_').lower()}_candles_1h.parquet"

    start_ts = int(datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.combine(end, datetime.min.time()).replace(tzinfo=timezone.utc).timestamp())

    all_records: list[dict] = []
    cursor = end_ts  # Start from the end, paginate backwards
    max_retries = 3

    while cursor > start_ts:
        url = f"{BASE_URL}/market/{symbol}/candles/60"
        params = {"startTs": cursor, "limit": 1000}

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
                    logger.error(f"  Candle fetch failed at cursor={cursor}: {e}")
                    break
                logger.warning(f"  Candle fetch retry {retries}/{max_retries}: {e}")
                time.sleep(2)

        if data is None or not data.get("success"):
            break

        records = data.get("records", [])
        if not records:
            break

        all_records.extend(records)
        oldest_ts = min(r["ts"] for r in records)
        cursor = oldest_ts  # Move cursor to oldest record

        dt = datetime.fromtimestamp(oldest_ts, tz=timezone.utc)
        logger.info(f"  Candles batch: {len(records)} records, oldest: {dt} (total: {len(all_records)})")

        if len(records) < 1000:
            break  # No more data

        time.sleep(0.2)

    if not all_records:
        logger.warning("No candle data retrieved")
        return

    rows = []
    for r in all_records:
        rows.append(
            {
                "timestamp": datetime.fromtimestamp(r["ts"], tz=timezone.utc),
                "open": r["oracleOpen"],
                "high": r["oracleHigh"],
                "low": r["oracleLow"],
                "close": r["oracleClose"],
                "fill_open": r.get("fillOpen", 0.0),
                "fill_high": r.get("fillHigh", 0.0),
                "fill_low": r.get("fillLow", 0.0),
                "fill_close": r.get("fillClose", 0.0),
                "volume_quote": r.get("quoteVolume", 0.0),
                "volume_base": r.get("baseVolume", 0.0),
            }
        )

    schema = {
        "timestamp": pl.Datetime("ns", "UTC"),
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "fill_open": pl.Float64,
        "fill_high": pl.Float64,
        "fill_low": pl.Float64,
        "fill_close": pl.Float64,
        "volume_quote": pl.Float64,
        "volume_base": pl.Float64,
    }

    df = pl.DataFrame(rows, schema=schema)
    df = df.unique(subset=["timestamp"]).sort("timestamp")

    # Merge with existing
    if out_path.exists():
        df_existing = pl.read_parquet(out_path)
        df = pl.concat([df_existing, df])
        df = df.unique(subset=["timestamp"]).sort("timestamp")

    df.write_parquet(out_path)
    logger.info(f"Candles complete: {len(df)} rows, {out_path}")
    logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")


# --- Funding Rates ---


def fetch_all_funding(symbol: str, start: date, end: date):
    """Fetch funding rates day by day."""
    out_path = DATA_DIR / f"drift_{symbol.replace('-', '_').lower()}_funding_rates.parquet"

    existing_dates: set[date] = set()
    df_existing = None
    if out_path.exists():
        df_existing = pl.read_parquet(out_path)
        existing_dates = set(df_existing["timestamp"].cast(pl.Date).unique().to_list())
        logger.info(f"Existing file has {len(df_existing)} rows")

    all_records: list[dict] = []
    current = start

    while current <= end:
        if current in existing_dates:
            current += timedelta(days=1)
            continue

        url = f"{BASE_URL}/market/{symbol}/fundingRates/{current.year}/{current.month}/{current.day}"
        try:
            resp = requests.get(url, params={"limit": 50}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"  FR fetch error for {current}: {e}")
            current += timedelta(days=1)
            time.sleep(1)
            continue

        records = data.get("records", [])
        for r in records:
            all_records.append(
                {
                    "timestamp": datetime.fromtimestamp(int(r["ts"]), tz=timezone.utc),
                    "funding_rate": float(r["fundingRate"]),
                    "funding_rate_long": float(r.get("fundingRateLong", r["fundingRate"])),
                    "funding_rate_short": float(r.get("fundingRateShort", r["fundingRate"])),
                    "oracle_price_twap": float(r.get("oraclePriceTwap", 0)),
                    "mark_price_twap": float(r.get("markPriceTwap", 0)),
                }
            )

        if records:
            logger.debug(f"  {current}: {len(records)} FR records")

        current += timedelta(days=1)
        time.sleep(0.1)

    if not all_records and df_existing is None:
        logger.warning("No funding rate data retrieved")
        return

    schema = {
        "timestamp": pl.Datetime("ns", "UTC"),
        "funding_rate": pl.Float64,
        "funding_rate_long": pl.Float64,
        "funding_rate_short": pl.Float64,
        "oracle_price_twap": pl.Float64,
        "mark_price_twap": pl.Float64,
    }

    if all_records:
        df_new = pl.DataFrame(all_records, schema=schema)
        if df_existing is not None and not df_existing.is_empty():
            df = pl.concat([df_existing, df_new])
        else:
            df = df_new
    else:
        df = df_existing

    df = df.unique(subset=["timestamp"]).sort("timestamp")
    df.write_parquet(out_path)
    logger.info(f"Funding rates complete: {len(df)} rows, {out_path}")
    logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")


def main():
    args = parse_args()
    symbol = args.symbol
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    types = []
    if args.all:
        types = ["candles", "funding", "trades"]
    elif args.type:
        types = [args.type]
    else:
        logger.error("Specify --type or --all")
        return

    for t in types:
        logger.info(f"=== Fetching {t} for {symbol} from {start} to {end} ===")
        if t == "trades":
            fetch_all_trades(symbol, start, end)
        elif t == "candles":
            fetch_all_candles(symbol, start, end)
        elif t == "funding":
            fetch_all_funding(symbol, start, end)


if __name__ == "__main__":
    main()
