"""src/features.py のテスト."""

from pathlib import Path

import polars as pl
import pytest

from src.features import FEATURE_COLS, CROSS_FEATURE_COLS, build_features, build_all_features, get_feature_cols

DATA_DIR = Path(__file__).parent.parent / "data"


@pytest.fixture(scope="module")
def btc_features():
    return build_features("BTC", DATA_DIR)


@pytest.fixture(scope="module")
def eth_features():
    btc_basis = pl.read_parquet(DATA_DIR / "binance_btcusdt_basis_1h.parquet").sort("timestamp")
    return build_features("ETH", DATA_DIR, btc_basis=btc_basis)


class TestBuildFeaturesBTC:
    def test_has_all_feature_columns(self, btc_features):
        for col in FEATURE_COLS:
            assert col in btc_features.columns, f"Missing column: {col}"

    def test_no_cross_asset_for_btc(self, btc_features):
        assert "btc_basis_rate" not in btc_features.columns

    def test_has_target(self, btc_features):
        assert "target" in btc_features.columns
        assert "fwd_8h" in btc_features.columns

    def test_target_is_binary(self, btc_features):
        values = btc_features["target"].unique().sort().to_list()
        assert values == [-1, 1]

    def test_timestamp_sorted(self, btc_features):
        ts = btc_features["timestamp"]
        assert ts.is_sorted()

    def test_row_count_reasonable(self, btc_features):
        # 1年分の1hデータ ≈ 8760行、多少の欠落を許容
        assert btc_features.height > 5000

    def test_feature_dtypes_are_numeric(self, btc_features):
        for col in FEATURE_COLS:
            assert btc_features[col].dtype in (pl.Float64, pl.Float32, pl.Int64, pl.Int32), \
                f"{col} has dtype {btc_features[col].dtype}"

    def test_no_all_null_features(self, btc_features):
        for col in FEATURE_COLS:
            assert btc_features[col].null_count() < btc_features.height, \
                f"{col} is entirely null"

    def test_fwd_return_no_nulls(self, btc_features):
        assert btc_features["fwd_8h"].null_count() == 0


class TestBuildFeaturesETH:
    def test_has_cross_asset_feature(self, eth_features):
        assert "btc_basis_rate" in eth_features.columns

    def test_cross_asset_not_all_null(self, eth_features):
        assert eth_features["btc_basis_rate"].null_count() < eth_features.height


class TestBuildAllFeatures:
    @pytest.fixture(scope="class")
    def all_features(self):
        return build_all_features(DATA_DIR, tokens=["BTC", "ETH"])

    def test_returns_dict(self, all_features):
        assert isinstance(all_features, dict)
        assert "BTC" in all_features
        assert "ETH" in all_features

    def test_eth_has_cross_asset(self, all_features):
        assert "btc_basis_rate" in all_features["ETH"].columns

    def test_btc_no_cross_asset(self, all_features):
        assert "btc_basis_rate" not in all_features["BTC"].columns


class TestGetFeatureCols:
    def test_btc_no_cross(self):
        cols = get_feature_cols("BTC")
        assert "btc_basis_rate" not in cols

    def test_eth_has_cross(self):
        cols = get_feature_cols("ETH")
        assert "btc_basis_rate" in cols

    def test_returns_list(self):
        cols = get_feature_cols("SOL")
        assert isinstance(cols, list)
        assert len(cols) == len(FEATURE_COLS) + len(CROSS_FEATURE_COLS)
