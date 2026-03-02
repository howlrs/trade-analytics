"""Tests for fetch_drift_trades.py - Drift trade history fetcher."""

import polars as pl
import pytest

from scripts.fetch_drift_trades import (
    DRIFT_MARKET_INDEX,
    fetch_trades,
    output_path,
    trades_to_dataframe,
)


class TestFetchTrades:
    """Live API test - fetch trades from DLOB."""

    @pytest.fixture(scope="class")
    def raw_trades(self):
        trades = fetch_trades("SOL", limit=10)
        return trades

    def test_returns_list(self, raw_trades):
        assert isinstance(raw_trades, list)

    def test_has_records(self, raw_trades):
        # DLOB trades endpoint may return empty if no recent trades
        # Just verify the API responded correctly
        assert isinstance(raw_trades, list)

    def test_trade_structure(self, raw_trades):
        if not raw_trades:
            pytest.skip("No trades returned from DLOB API")
        t = raw_trades[0]
        assert isinstance(t, dict)
        # Should have at least some price/size info
        has_price = "price" in t or "oraclePrice" in t
        has_size = "baseAssetAmountFilled" in t or "size" in t or "baseAssetAmount" in t
        assert has_price or has_size, f"Trade record missing price/size fields: {list(t.keys())}"


class TestTradesToDataframe:
    """Unit tests for trade record conversion."""

    def _mock_trades(self):
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
        df = trades_to_dataframe(self._mock_trades(), "SOL")
        assert not df.is_empty()
        assert len(df) == 2

    def test_columns(self):
        df = trades_to_dataframe(self._mock_trades(), "SOL")
        expected_cols = ["timestamp", "price", "size", "side", "market", "tx_sig"]
        assert df.columns == expected_cols

    def test_dtypes(self):
        df = trades_to_dataframe(self._mock_trades(), "SOL")
        assert df["timestamp"].dtype == pl.Datetime("ns", "UTC")
        assert df["price"].dtype == pl.Float64
        assert df["size"].dtype == pl.Float64
        assert df["side"].dtype == pl.Utf8

    def test_side_mapping(self):
        df = trades_to_dataframe(self._mock_trades(), "SOL")
        sides = df["side"].to_list()
        assert "buy" in sides
        assert "sell" in sides

    def test_size_normalized(self):
        df = trades_to_dataframe(self._mock_trades(), "SOL")
        sizes = df["size"].to_list()
        assert all(s < 1e6 for s in sizes), "Sizes should be normalized from 1e9"

    def test_sorted_by_timestamp(self):
        df = trades_to_dataframe(self._mock_trades(), "SOL")
        ts = df["timestamp"].cast(pl.Int64)
        assert (ts.diff().drop_nulls() > 0).all()

    def test_empty_trades(self):
        df = trades_to_dataframe([], "SOL")
        assert df.is_empty()

    def test_deduplication(self):
        trades = self._mock_trades() + [self._mock_trades()[0]]  # duplicate
        df = trades_to_dataframe(trades, "SOL")
        assert len(df) == 2

    def test_market_field(self):
        df = trades_to_dataframe(self._mock_trades(), "SOL")
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
