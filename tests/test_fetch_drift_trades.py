"""Tests for fetch_drift_trades.py - Drift trade history fetcher."""

import polars as pl
import pytest

from scripts.fetch_drift_trades import (
    DRIFT_MARKET_INDEX,
    DRIFT_MARKET_SYMBOL,
    fetch_trades,
    output_path,
    trades_to_dataframe,
)


class TestFetchTrades:
    """Live API test - fetch trades from Data API."""

    @pytest.fixture(scope="class")
    def raw_trades(self):
        trades = fetch_trades("SOL", limit=10)
        return trades

    def test_returns_list(self, raw_trades):
        assert isinstance(raw_trades, list)

    def test_has_records(self, raw_trades):
        assert isinstance(raw_trades, list)
        assert len(raw_trades) > 0, "Data API should return trades for SOL-PERP"

    def test_trade_structure(self, raw_trades):
        t = raw_trades[0]
        assert isinstance(t, dict)
        assert "oraclePrice" in t
        assert "baseAssetAmountFilled" in t
        assert "takerOrderDirection" in t
        assert "txSig" in t
        assert "takerFee" in t
        assert "makerRebate" in t


class TestTradesToDataframe:
    """Unit tests for trade record conversion."""

    def _mock_trades_data_api(self):
        """Mock trades in Data API format (pre-formatted strings)."""
        return [
            {
                "ts": 1709300000,
                "oraclePrice": "150.500000",
                "baseAssetAmountFilled": "1.000000000",
                "quoteAssetAmountFilled": "150.500000",
                "takerFee": "0.052675",
                "makerRebate": "-0.003763",
                "takerOrderDirection": "long",
                "actionExplanation": "orderFilledWithMatch",
                "txSig": "abc123",
            },
            {
                "ts": 1709300010,
                "oraclePrice": "150.600000",
                "baseAssetAmountFilled": "0.500000000",
                "quoteAssetAmountFilled": "75.300000",
                "takerFee": "0.026355",
                "makerRebate": "-0.001883",
                "takerOrderDirection": "short",
                "actionExplanation": "orderFilledWithMatchJit",
                "txSig": "def456",
            },
        ]

    def _mock_trades(self):
        """Mock trades in legacy DLOB format (raw integers)."""
        return [
            {
                "ts": 1709300000,
                "price": "150500000",  # 150.5 in 1e6 precision
                "baseAssetAmountFilled": "1000000000",  # 1.0 SOL in 1e9
                "takerOrderDirection": "long",
                "txSig": "abc123",
            },
            {
                "ts": 1709300010,
                "price": "150600000",
                "baseAssetAmountFilled": "500000000",  # 0.5 SOL
                "takerOrderDirection": "short",
                "txSig": "def456",
            },
        ]

    def test_basic_conversion(self):
        df = trades_to_dataframe(self._mock_trades_data_api(), "SOL")
        assert not df.is_empty()
        assert len(df) == 2

    def test_data_api_format(self):
        """Data API returns pre-formatted decimal strings."""
        df = trades_to_dataframe(self._mock_trades_data_api(), "SOL")
        prices = df["price"].to_list()
        assert abs(prices[0] - 150.5) < 0.01
        assert abs(prices[1] - 150.6) < 0.01
        sizes = df["size"].to_list()
        assert abs(sizes[0] - 1.0) < 0.01
        assert abs(sizes[1] - 0.5) < 0.01
        # Fee fields
        assert df["taker_fee"][0] > 0
        assert df["maker_rebate"][0] < 0  # rebate is negative

    def test_columns(self):
        df = trades_to_dataframe(self._mock_trades_data_api(), "SOL")
        expected_cols = [
            "timestamp", "price", "size", "quote_filled",
            "taker_fee", "maker_rebate", "side", "market",
            "action_explanation", "tx_sig",
        ]
        assert df.columns == expected_cols

    def test_dtypes(self):
        df = trades_to_dataframe(self._mock_trades_data_api(), "SOL")
        assert df["timestamp"].dtype == pl.Datetime("ns", "UTC")
        assert df["price"].dtype == pl.Float64
        assert df["size"].dtype == pl.Float64
        assert df["taker_fee"].dtype == pl.Float64
        assert df["maker_rebate"].dtype == pl.Float64
        assert df["side"].dtype == pl.Utf8

    def test_side_mapping(self):
        df = trades_to_dataframe(self._mock_trades_data_api(), "SOL")
        sides = df["side"].to_list()
        assert "buy" in sides
        assert "sell" in sides

    def test_size_normalized(self):
        df = trades_to_dataframe(self._mock_trades(), "SOL")
        sizes = df["size"].to_list()
        assert all(s < 1e6 for s in sizes), "Sizes should be normalized from 1e9"

    def test_legacy_format(self):
        """Legacy DLOB format with raw integer precision still works."""
        df = trades_to_dataframe(self._mock_trades(), "SOL")
        prices = df["price"].to_list()
        assert all(p < 1000 for p in prices), "Prices should be normalized from 1e6"
        sizes = df["size"].to_list()
        assert all(s < 100 for s in sizes), "Sizes should be normalized from 1e9"

    def test_sorted_by_timestamp(self):
        df = trades_to_dataframe(self._mock_trades_data_api(), "SOL")
        ts = df["timestamp"].cast(pl.Int64)
        assert (ts.diff().drop_nulls() > 0).all()

    def test_empty_trades(self):
        df = trades_to_dataframe([], "SOL")
        assert df.is_empty()

    def test_deduplication(self):
        trades = self._mock_trades_data_api() + [self._mock_trades_data_api()[0]]
        df = trades_to_dataframe(trades, "SOL")
        assert len(df) == 2

    def test_market_field(self):
        df = trades_to_dataframe(self._mock_trades_data_api(), "SOL")
        assert (df["market"] == "SOL").all()


class TestOutputPath:
    def test_sol_path(self):
        from pathlib import Path
        p = output_path("SOL", Path("/tmp"))
        assert p.name == "drift_solusdc_trades.parquet"

    def test_btc_path(self):
        from pathlib import Path
        p = output_path("BTC", Path("/tmp"))
        assert p.name == "drift_btcusdc_trades.parquet"
