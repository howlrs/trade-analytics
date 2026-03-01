"""Tests for fetch_ohlcv.py - uses live API with minimal requests."""

import pandas as pd
import pytest

from scripts.fetch_ohlcv import (
    create_exchange,
    fetch_all_ohlcv,
    output_path,
    to_dataframe,
    to_ms,
)


class TestToMs:
    def test_basic(self):
        ms = to_ms("2026-01-01")
        assert ms == 1767225600000

    def test_different_date(self):
        ms = to_ms("2025-03-01")
        assert ms == 1740787200000


class TestCreateExchange:
    def test_binance(self):
        ex = create_exchange("binance")
        assert ex.id == "binance"
        assert ex.enableRateLimit is True

    def test_bybit(self):
        ex = create_exchange("bybit")
        assert ex.id == "bybit"
        assert ex.enableRateLimit is True


class TestOutputPath:
    def test_auto_path(self):
        p = output_path("binance", "BTC/USDT:USDT", "1h")
        assert p.name == "binance_btcusdt_1h.parquet"

    def test_custom_path(self):
        p = output_path("binance", "BTC/USDT:USDT", "1h", "/tmp/test.parquet")
        assert str(p) == "/tmp/test.parquet"

    def test_eth_symbol(self):
        p = output_path("bybit", "ETH/USDT:USDT", "4h")
        assert p.name == "bybit_ethusdt_4h.parquet"


class TestFetchAndTransform:
    """Live API tests - fetch minimal data to verify correctness."""

    @pytest.fixture(scope="class")
    def binance_data(self):
        ex = create_exchange("binance")
        since = to_ms("2026-02-28")
        end = to_ms("2026-03-01")
        data = fetch_all_ohlcv(ex, "BTC/USDT:USDT", "1h", since, end)
        return to_dataframe(data)

    @pytest.fixture(scope="class")
    def bybit_data(self):
        ex = create_exchange("bybit")
        since = to_ms("2026-02-28")
        end = to_ms("2026-03-01")
        data = fetch_all_ohlcv(ex, "BTC/USDT:USDT", "1h", since, end)
        return to_dataframe(data)

    def test_binance_record_count(self, binance_data):
        assert len(binance_data) == 24

    def test_binance_columns(self, binance_data):
        assert list(binance_data.columns) == ["timestamp", "open", "high", "low", "close", "volume"]

    def test_binance_no_duplicates(self, binance_data):
        assert binance_data["timestamp"].is_unique

    def test_binance_sorted(self, binance_data):
        assert binance_data["timestamp"].is_monotonic_increasing

    def test_binance_dtypes(self, binance_data):
        assert pd.api.types.is_datetime64_any_dtype(binance_data["timestamp"])
        assert pd.api.types.is_float_dtype(binance_data["close"])

    def test_binance_values_reasonable(self, binance_data):
        assert (binance_data["high"] >= binance_data["low"]).all()
        assert (binance_data["volume"] > 0).all()

    def test_bybit_record_count(self, bybit_data):
        assert len(bybit_data) == 24

    def test_bybit_columns(self, bybit_data):
        assert list(bybit_data.columns) == ["timestamp", "open", "high", "low", "close", "volume"]

    def test_bybit_no_duplicates(self, bybit_data):
        assert bybit_data["timestamp"].is_unique

    def test_bybit_values_reasonable(self, bybit_data):
        assert (bybit_data["high"] >= bybit_data["low"]).all()

    def test_parquet_roundtrip(self, binance_data, tmp_path):
        path = tmp_path / "test.parquet"
        binance_data.to_parquet(path, engine="pyarrow", index=False)
        loaded = pd.read_parquet(path)
        assert len(loaded) == len(binance_data)
        assert list(loaded.columns) == list(binance_data.columns)
