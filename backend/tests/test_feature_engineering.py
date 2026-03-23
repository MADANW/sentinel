"""
test_feature_engineering.py — Unit tests for feature_engineering.py.

Uses synthetic OHLCV data — no real API calls.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest


def _make_ohlcv(rows: int = 60, base_price: float = 500.0,
                trend: float = 0.001) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame with a slight upward trend."""
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    close = base_price * (1 + trend) ** np.arange(rows)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": 1_000_000.0 + np.arange(rows) * 100,
        },
        index=idx,
    )


class TestBuildFeatures:
    def test_returns_feature_row(self):
        from backend.core.feature_engineering import build_features, FeatureRow
        ohlcv = _make_ohlcv(60)
        result = build_features(ohlcv)
        assert isinstance(result, FeatureRow)

    def test_all_fields_are_floats(self):
        from backend.core.feature_engineering import build_features, FEATURE_COLUMNS
        ohlcv = _make_ohlcv(60)
        result = build_features(ohlcv)
        for col in FEATURE_COLUMNS:
            assert isinstance(getattr(result, col), float), f"{col} is not a float"

    def test_rsi_within_range(self):
        from backend.core.feature_engineering import build_features
        ohlcv = _make_ohlcv(60)
        result = build_features(ohlcv)
        assert 0.0 <= result.rsi_14 <= 100.0

    def test_atr_pct_positive(self):
        from backend.core.feature_engineering import build_features
        ohlcv = _make_ohlcv(60)
        result = build_features(ohlcv)
        assert result.atr_14_pct > 0.0

    def test_ema_crossover_signal_valid_values(self):
        from backend.core.feature_engineering import build_features
        ohlcv = _make_ohlcv(60)
        result = build_features(ohlcv)
        assert result.ema_crossover_signal in (-1.0, 0.0, 1.0)

    def test_volume_deviation_calculation(self):
        from backend.core.feature_engineering import build_features
        # Flat volume — deviation should be close to 0
        ohlcv = _make_ohlcv(60)
        ohlcv["volume"] = 1_000_000.0  # exactly flat
        result = build_features(ohlcv)
        # Last bar has same volume as mean → deviation ≈ 0
        assert abs(result.volume_deviation) < 1e-6

    def test_insufficient_data_raises(self):
        from backend.core.feature_engineering import build_features, FeatureError
        ohlcv = _make_ohlcv(10)
        with pytest.raises(FeatureError, match="[Ii]nsufficient"):
            build_features(ohlcv)

    def test_missing_column_raises(self):
        from backend.core.feature_engineering import build_features, FeatureError
        ohlcv = _make_ohlcv(60).drop(columns=["high"])
        with pytest.raises(FeatureError, match="[Mm]issing"):
            build_features(ohlcv)

    def test_nan_in_close_raises(self):
        from backend.core.feature_engineering import build_features, FeatureError
        ohlcv = _make_ohlcv(60)
        ohlcv.loc[ohlcv.index[10], "close"] = float("nan")
        with pytest.raises(FeatureError):
            build_features(ohlcv)

    def test_zero_volume_mean_raises(self):
        from backend.core.feature_engineering import build_features, FeatureError
        ohlcv = _make_ohlcv(60)
        ohlcv["volume"] = 0.0  # all-zero volume → mean = 0 → division guard
        with pytest.raises(FeatureError):
            build_features(ohlcv)


class TestFeatureColumns:
    def test_feature_columns_order_matches_feature_row(self):
        """FEATURE_COLUMNS must exactly match FeatureRow field order."""
        from backend.core.feature_engineering import FEATURE_COLUMNS, FeatureRow
        import dataclasses
        field_names = [f.name for f in dataclasses.fields(FeatureRow)]
        assert FEATURE_COLUMNS == field_names

    def test_feature_row_to_dataframe_shape(self):
        from backend.core.feature_engineering import (
            build_features, feature_row_to_dataframe, FEATURE_COLUMNS
        )
        ohlcv = _make_ohlcv(60)
        row = build_features(ohlcv)
        df = feature_row_to_dataframe(row)
        assert df.shape == (1, len(FEATURE_COLUMNS))
        assert list(df.columns) == FEATURE_COLUMNS

    def test_feature_row_to_dataframe_values_match(self):
        from backend.core.feature_engineering import (
            build_features, feature_row_to_dataframe, FEATURE_COLUMNS
        )
        ohlcv = _make_ohlcv(60)
        row = build_features(ohlcv)
        df = feature_row_to_dataframe(row)
        for col in FEATURE_COLUMNS:
            assert df[col].iloc[0] == getattr(row, col)
