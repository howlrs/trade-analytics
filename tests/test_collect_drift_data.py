"""Tests for collect_drift_data.py - DLOB L2 snapshot collection."""

import polars as pl
import pytest

from scripts.collect_drift_data import (
    DRIFT_MARKET_INDEX,
    NUM_LEVELS,
    fetch_binance_bbo,
    fetch_drift_l2,
    flush_buffer,
    output_path,
    parse_l2_snapshot,
    snapshot_schema,
)


class TestDriftL2Fetch:
    """Live API test - fetch one L2 snapshot from Drift DLOB."""

    @pytest.fixture(scope="class")
    def l2_data(self):
        data = fetch_drift_l2("SOL", depth=20)
        assert data is not None, "Drift DLOB API returned None"
        return data

    def test_has_bids_and_asks(self, l2_data):
        assert "bids" in l2_data or "asks" in l2_data

    def test_bids_structure(self, l2_data):
        bids = l2_data.get("bids", [])
        if bids:
            bid = bids[0]
            assert "price" in bid
            assert "size" in bid

    def test_asks_structure(self, l2_data):
        asks = l2_data.get("asks", [])
        if asks:
            ask = asks[0]
            assert "price" in ask
            assert "size" in ask

    def test_prices_are_numeric(self, l2_data):
        bids = l2_data.get("bids", [])
        asks = l2_data.get("asks", [])
        if bids:
            assert float(bids[0]["price"]) > 0
        if asks:
            assert float(asks[0]["price"]) > 0


class TestBinanceBBO:
    """Live API test - fetch Binance BBO."""

    @pytest.fixture(scope="class")
    def bbo_data(self):
        data = fetch_binance_bbo("SOL")
        assert data is not None, "Binance BBO API returned None"
        return data

    def test_has_bid_ask(self, bbo_data):
        assert "bidPrice" in bbo_data
        assert "askPrice" in bbo_data

    def test_prices_positive(self, bbo_data):
        assert float(bbo_data["bidPrice"]) > 0
        assert float(bbo_data["askPrice"]) > 0

    def test_bid_less_than_ask(self, bbo_data):
        assert float(bbo_data["bidPrice"]) <= float(bbo_data["askPrice"])


class TestParseL2Snapshot:
    """Unit tests for snapshot parsing logic."""

    def _mock_l2(self):
        return {
            "bids": [
                {"price": "150.50", "size": "100", "sources": {"dlob": "100"}},
                {"price": "150.40", "size": "200", "sources": {"vamm": "200"}},
            ],
            "asks": [
                {"price": "150.60", "size": "80", "sources": {"vamm": "80"}},
                {"price": "150.70", "size": "150", "sources": {"dlob": "150"}},
            ],
        }

    def _mock_binance_bbo(self):
        return {
            "bidPrice": "150.55",
            "askPrice": "150.57",
            "symbol": "SOLUSDT",
        }

    def test_basic_parse(self):
        row = parse_l2_snapshot(self._mock_l2(), self._mock_binance_bbo(), "SOL")
        assert row["market"] == "SOL"
        assert row["drift_bid1_price"] == 150.50
        assert row["drift_ask1_price"] == 150.60
        assert row["drift_bid1_source"] == "dlob"
        assert row["drift_ask1_source"] == "vamm"

    def test_spread_bp(self):
        row = parse_l2_snapshot(self._mock_l2(), self._mock_binance_bbo(), "SOL")
        mid = (150.50 + 150.60) / 2
        expected_spread = (150.60 - 150.50) / mid * 10000
        assert abs(row["drift_spread_bp"] - expected_spread) < 0.01

    def test_drift_mid(self):
        row = parse_l2_snapshot(self._mock_l2(), self._mock_binance_bbo(), "SOL")
        assert row["drift_mid"] == pytest.approx((150.50 + 150.60) / 2)

    def test_binance_mid(self):
        row = parse_l2_snapshot(self._mock_l2(), self._mock_binance_bbo(), "SOL")
        assert row["binance_mid"] == pytest.approx((150.55 + 150.57) / 2)

    def test_cex_dex_divergence(self):
        row = parse_l2_snapshot(self._mock_l2(), self._mock_binance_bbo(), "SOL")
        assert row["cex_dex_divergence_bp"] is not None
        assert isinstance(row["cex_dex_divergence_bp"], float)

    def test_no_binance_bbo(self):
        row = parse_l2_snapshot(self._mock_l2(), None, "SOL")
        assert row["binance_bid"] is None
        assert row["binance_ask"] is None
        assert row["cex_dex_divergence_bp"] is None

    def test_empty_orderbook(self):
        row = parse_l2_snapshot({"bids": [], "asks": []}, None, "SOL")
        assert row["drift_bid1_price"] is None
        assert row["drift_ask1_price"] is None
        assert row["drift_mid"] is None

    def test_level2_parsed(self):
        row = parse_l2_snapshot(self._mock_l2(), None, "SOL")
        assert row["drift_bid2_price"] == 150.40
        assert row["drift_ask2_price"] == 150.70
        assert row["drift_bid2_source"] == "vamm"
        assert row["drift_ask2_source"] == "dlob"


class TestSnapshotSchema:
    def test_schema_keys(self):
        schema = snapshot_schema()
        assert "timestamp" in schema
        assert "market" in schema
        assert "drift_bid1_price" in schema
        assert "drift_ask5_source" in schema
        assert "binance_mid" in schema
        assert "cex_dex_divergence_bp" in schema

    def test_schema_types(self):
        schema = snapshot_schema()
        assert schema["timestamp"] == pl.Datetime("ns", "UTC")
        assert schema["drift_bid1_price"] == pl.Float64
        assert schema["drift_bid1_source"] == pl.Utf8


class TestFlushBuffer:
    def test_flush_creates_file(self, tmp_path):
        row = parse_l2_snapshot(
            {
                "bids": [{"price": "100", "size": "10", "sources": {"dlob": "10"}}],
                "asks": [{"price": "101", "size": "10", "sources": {"vamm": "10"}}],
            },
            {"bidPrice": "100.5", "askPrice": "100.6"},
            "SOL",
        )
        flush_buffer([row], "SOL", tmp_path)

        out = output_path("SOL", tmp_path)
        assert out.exists()
        df = pl.read_parquet(out)
        assert len(df) == 1
        assert "drift_bid1_price" in df.columns

    def test_flush_appends(self, tmp_path):
        l2 = {
            "bids": [{"price": "100", "size": "10", "sources": {"dlob": "10"}}],
            "asks": [{"price": "101", "size": "10", "sources": {"vamm": "10"}}],
        }
        bbo = {"bidPrice": "100.5", "askPrice": "100.6"}

        row1 = parse_l2_snapshot(l2, bbo, "SOL")
        flush_buffer([row1], "SOL", tmp_path)

        import time
        time.sleep(0.01)  # Ensure different timestamp

        row2 = parse_l2_snapshot(l2, bbo, "SOL")
        flush_buffer([row2], "SOL", tmp_path)

        df = pl.read_parquet(output_path("SOL", tmp_path))
        assert len(df) == 2

    def test_flush_empty_buffer(self, tmp_path):
        flush_buffer([], "SOL", tmp_path)
        assert not output_path("SOL", tmp_path).exists()

    def test_parquet_roundtrip_schema(self, tmp_path):
        row = parse_l2_snapshot(
            {
                "bids": [{"price": "100", "size": "10", "sources": {"dlob": "10"}}],
                "asks": [{"price": "101", "size": "10", "sources": {"vamm": "10"}}],
            },
            {"bidPrice": "100.5", "askPrice": "100.6"},
            "SOL",
        )
        flush_buffer([row], "SOL", tmp_path)

        df = pl.read_parquet(output_path("SOL", tmp_path))
        expected_schema = snapshot_schema()
        for col, dtype in expected_schema.items():
            assert col in df.columns, f"Missing column: {col}"
            assert df[col].dtype == dtype, f"Column {col}: expected {dtype}, got {df[col].dtype}"
