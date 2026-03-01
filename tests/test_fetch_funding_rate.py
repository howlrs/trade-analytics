"""Tests for fetch_funding_rate.py - uses live API with minimal requests."""

import pandas as pd
import pytest

from scripts.fetch_funding_rate import (
    create_exchange,
    fetch_all_funding_rates,
    output_path,
    to_dataframe,
    to_ms,
)


class TestOutputPath:
    def test_auto_path(self):
        p = output_path("binance", "BTC/USDT:USDT")
        assert p.name == "binance_btcusdt_funding_rate.parquet"

    def test_bybit_eth(self):
        p = output_path("bybit", "ETH/USDT:USDT")
        assert p.name == "bybit_ethusdt_funding_rate.parquet"


class TestFetchAndTransform:
    """Live API tests - fetch minimal data."""

    @pytest.fixture(scope="class")
    def binance_fr(self):
        ex = create_exchange("binance")
        since = to_ms("2026-02-25")
        end = to_ms("2026-03-01")
        data = fetch_all_funding_rates(ex, "BTC/USDT:USDT", since, end)
        return to_dataframe(data)

    @pytest.fixture(scope="class")
    def bybit_fr(self):
        ex = create_exchange("bybit")
        since = to_ms("2026-02-25")
        end = to_ms("2026-03-01")
        data = fetch_all_funding_rates(ex, "BTC/USDT:USDT", since, end)
        return to_dataframe(data)

    def test_binance_has_records(self, binance_fr):
        # 4 days * 3 per day = ~12 records
        assert len(binance_fr) >= 10

    def test_binance_columns(self, binance_fr):
        assert "timestamp" in binance_fr.columns
        assert "funding_rate" in binance_fr.columns

    def test_binance_no_duplicates(self, binance_fr):
        assert binance_fr["timestamp"].is_unique

    def test_binance_funding_rate_range(self, binance_fr):
        # Funding rates are typically small values
        assert (binance_fr["funding_rate"].abs() < 0.1).all()

    def test_bybit_has_records(self, bybit_fr):
        assert len(bybit_fr) >= 10

    def test_bybit_no_duplicates(self, bybit_fr):
        assert bybit_fr["timestamp"].is_unique

    def test_bybit_funding_rate_range(self, bybit_fr):
        assert (bybit_fr["funding_rate"].abs() < 0.1).all()

    def test_parquet_roundtrip(self, binance_fr, tmp_path):
        path = tmp_path / "test_fr.parquet"
        binance_fr.to_parquet(path, engine="pyarrow", index=False)
        loaded = pd.read_parquet(path)
        assert len(loaded) == len(binance_fr)
