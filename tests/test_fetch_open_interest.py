"""Tests for fetch_open_interest.py - uses live API with minimal requests."""

import pandas as pd
import pytest

from scripts.fetch_open_interest import (
    binance_oi_to_df,
    bybit_oi_to_df,
    fetch_binance_oi,
    fetch_bybit_oi,
    output_path,
    to_ms,
)


class TestOutputPath:
    def test_auto_path(self):
        p = output_path("binance", "BTC/USDT:USDT")
        assert p.name == "binance_btcusdt_open_interest.parquet"

    def test_bybit_eth(self):
        p = output_path("bybit", "ETH/USDT:USDT")
        assert p.name == "bybit_ethusdt_open_interest.parquet"


class TestBinanceOI:
    """Binance OI - limited to last 30 days."""

    @pytest.fixture(scope="class")
    def binance_oi(self):
        data = fetch_binance_oi("BTC/USDT:USDT", "1d")
        return binance_oi_to_df(data)

    def test_has_records(self, binance_oi):
        assert len(binance_oi) >= 20

    def test_columns(self, binance_oi):
        assert "timestamp" in binance_oi.columns
        assert "open_interest" in binance_oi.columns
        assert "open_interest_value" in binance_oi.columns

    def test_no_duplicates(self, binance_oi):
        assert binance_oi["timestamp"].is_unique

    def test_oi_positive(self, binance_oi):
        assert (binance_oi["open_interest"] > 0).all()


class TestBybitOI:
    """Bybit OI - cursor pagination, limited fetch for testing."""

    @pytest.fixture(scope="class")
    def bybit_oi(self):
        start = to_ms("2026-02-01")
        end = to_ms("2026-03-01")
        data = fetch_bybit_oi("BTC/USDT:USDT", "1d", start, end)
        return bybit_oi_to_df(data)

    def test_has_records(self, bybit_oi):
        assert len(bybit_oi) >= 20

    def test_columns(self, bybit_oi):
        assert "timestamp" in bybit_oi.columns
        assert "open_interest" in bybit_oi.columns

    def test_no_duplicates(self, bybit_oi):
        assert bybit_oi["timestamp"].is_unique

    def test_oi_positive(self, bybit_oi):
        assert (bybit_oi["open_interest"] > 0).all()

    def test_parquet_roundtrip(self, bybit_oi, tmp_path):
        path = tmp_path / "test_oi.parquet"
        bybit_oi.to_parquet(path, engine="pyarrow", index=False)
        loaded = pd.read_parquet(path)
        assert len(loaded) == len(bybit_oi)
