"""
test_feature_engineering.py — Unit tests for feature_engineering.py.

Uses synthetic OHLCV data — no real API calls.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest


def _make_ohlcv(rows: int = 110, base_price: float = 500.0,
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


def _make_ou_series(
    rows: int, mu: float, theta: float, sigma: float, seed: int,
    base_price: float | None = None,
) -> pd.Series:
    """
    Simulate a discrete-time Ornstein-Uhlenbeck price path.

    dP_t = theta * (mu - P_{t-1}) + sigma * eps_t
    Useful for testing OU fit recovery and Hurst-below-0.5 behavior.
    """
    rng = np.random.default_rng(seed)
    prices = np.empty(rows)
    prices[0] = base_price if base_price is not None else mu
    for t in range(1, rows):
        prices[t] = prices[t - 1] + theta * (mu - prices[t - 1]) + sigma * rng.standard_normal()
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    return pd.Series(prices, index=idx, name="close")


class TestBuildFeatures:
    def test_returns_feature_row(self):
        from backend.core.feature_engineering import build_features, FeatureRow
        ohlcv = _make_ohlcv(110)
        result = build_features(ohlcv)
        assert isinstance(result, FeatureRow)

    def test_all_fields_are_floats(self):
        from backend.core.feature_engineering import build_features, FEATURE_COLUMNS
        ohlcv = _make_ohlcv(110)
        result = build_features(ohlcv)
        for col in FEATURE_COLUMNS:
            assert isinstance(getattr(result, col), float), f"{col} is not a float"

    def test_rsi_within_range(self):
        from backend.core.feature_engineering import build_features
        ohlcv = _make_ohlcv(110)
        result = build_features(ohlcv)
        assert 0.0 <= result.rsi_14 <= 100.0

    def test_atr_pct_positive(self):
        from backend.core.feature_engineering import build_features
        ohlcv = _make_ohlcv(110)
        result = build_features(ohlcv)
        assert result.atr_14_pct > 0.0

    def test_ema_crossover_signal_valid_values(self):
        from backend.core.feature_engineering import build_features
        ohlcv = _make_ohlcv(110)
        result = build_features(ohlcv)
        assert result.ema_crossover_signal in (-1.0, 0.0, 1.0)

    def test_volume_deviation_calculation(self):
        from backend.core.feature_engineering import build_features
        # Flat volume — deviation should be close to 0
        ohlcv = _make_ohlcv(110)
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
        ohlcv = _make_ohlcv(110).drop(columns=["high"])
        with pytest.raises(FeatureError, match="[Mm]issing"):
            build_features(ohlcv)

    def test_nan_in_close_raises(self):
        from backend.core.feature_engineering import build_features, FeatureError
        ohlcv = _make_ohlcv(110)
        ohlcv.loc[ohlcv.index[10], "close"] = float("nan")
        with pytest.raises(FeatureError):
            build_features(ohlcv)

    def test_zero_volume_mean_raises(self):
        from backend.core.feature_engineering import build_features, FeatureError
        ohlcv = _make_ohlcv(110)
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
        ohlcv = _make_ohlcv(110)
        row = build_features(ohlcv)
        df = feature_row_to_dataframe(row)
        assert df.shape == (1, len(FEATURE_COLUMNS))
        assert list(df.columns) == FEATURE_COLUMNS

    def test_feature_row_to_dataframe_values_match(self):
        from backend.core.feature_engineering import (
            build_features, feature_row_to_dataframe, FEATURE_COLUMNS
        )
        ohlcv = _make_ohlcv(110)
        row = build_features(ohlcv)
        df = feature_row_to_dataframe(row)
        for col in FEATURE_COLUMNS:
            assert df[col].iloc[0] == getattr(row, col)

    def test_feature_columns_includes_regime_features(self):
        from backend.core.feature_engineering import FEATURE_COLUMNS
        for col in ("hurst_exponent", "ou_log_half_life", "ou_zscore", "regime_label"):
            assert col in FEATURE_COLUMNS


class TestHurstExponent:
    def test_hurst_in_unit_interval(self):
        from backend.core.feature_engineering import _compute_hurst
        ohlcv = _make_ohlcv(110)
        h = _compute_hurst(ohlcv["close"], window=64)
        assert 0.0 <= h <= 1.0

    def test_hurst_deterministic(self):
        from backend.core.feature_engineering import _compute_hurst
        ohlcv = _make_ohlcv(110)
        h1 = _compute_hurst(ohlcv["close"], window=64)
        h2 = _compute_hurst(ohlcv["close"], window=64)
        assert h1 == h2

    def test_trending_greater_than_mean_reverting(self):
        """A strong trend should produce a larger Hurst than a mean-reverting series."""
        from backend.core.feature_engineering import _compute_hurst

        # Strongly trending: geometric growth with tiny noise
        rng = np.random.default_rng(seed=1)
        trend_close = 500.0 * (1.002 ** np.arange(200)) + rng.normal(0, 0.1, size=200)
        trend_series = pd.Series(trend_close)

        # Strongly mean-reverting OU series
        mr_series = _make_ou_series(rows=200, mu=500.0, theta=0.6, sigma=1.0, seed=2)

        h_trend = _compute_hurst(trend_series, window=64)
        h_mr = _compute_hurst(mr_series, window=64)
        assert h_trend > h_mr

    def test_flat_prices_raises(self):
        from backend.core.feature_engineering import _compute_hurst, FeatureError
        flat = pd.Series([100.0] * 80)
        with pytest.raises(FeatureError, match="Hurst"):
            _compute_hurst(flat, window=64)

    def test_insufficient_window_raises(self):
        from backend.core.feature_engineering import _compute_hurst, FeatureError
        short = pd.Series([100.0 + i for i in range(10)])
        with pytest.raises(FeatureError, match="Hurst"):
            _compute_hurst(short, window=64)


class TestOUFeatures:
    def test_ou_returns_finite_tuple(self):
        from backend.core.feature_engineering import _compute_ou_features
        series = _make_ou_series(rows=200, mu=500.0, theta=0.3, sigma=1.0, seed=3)
        half_life, zscore = _compute_ou_features(series, window=64)
        assert math.isfinite(half_life) and half_life > 0.0
        assert math.isfinite(zscore)

    def test_ou_half_life_reasonable_for_mean_reverting(self):
        """Strong mean reversion → half-life well below the saturation value."""
        from backend.core.feature_engineering import _compute_ou_features, _MAX_HALF_LIFE_BARS
        series = _make_ou_series(rows=200, mu=500.0, theta=0.5, sigma=1.0, seed=4)
        half_life, _ = _compute_ou_features(series, window=64)
        # Sanity: true theta=0.5 → half-life ≈ ln(2)/0.5 ≈ 1.39; allow generous fit variance
        assert half_life < 50.0
        assert half_life < _MAX_HALF_LIFE_BARS

    def test_ou_half_life_saturates_for_random_walk_with_drift(self):
        """A pure random walk with drift has b >= 1 → saturation path."""
        from backend.core.feature_engineering import _compute_ou_features, _MAX_HALF_LIFE_BARS
        rng = np.random.default_rng(seed=5)
        steps = rng.normal(loc=0.5, scale=0.3, size=200).cumsum()
        prices = pd.Series(500.0 + steps)
        half_life, zscore = _compute_ou_features(prices, window=64)
        # Saturation path: half_life pinned at the maximum, zscore still finite via fallback
        assert half_life == _MAX_HALF_LIFE_BARS
        assert math.isfinite(zscore)

    def test_ou_zscore_small_at_mean(self):
        from backend.core.feature_engineering import _compute_ou_features
        series = _make_ou_series(rows=200, mu=500.0, theta=0.4, sigma=0.5, seed=6)
        # Override last price to exactly the simulated mean
        series.iloc[-1] = 500.0
        _, zscore = _compute_ou_features(series, window=64)
        assert abs(zscore) < 1.0

    def test_ou_raises_on_flat_prices(self):
        from backend.core.feature_engineering import _compute_ou_features, FeatureError
        flat = pd.Series([100.0] * 80)
        with pytest.raises(FeatureError, match="OU"):
            _compute_ou_features(flat, window=64)

    def test_ou_insufficient_window_raises(self):
        from backend.core.feature_engineering import _compute_ou_features, FeatureError
        short = pd.Series([100.0 + i for i in range(10)])
        with pytest.raises(FeatureError, match="OU"):
            _compute_ou_features(short, window=64)


class TestRegimeLabel:
    def test_regime_label_values_valid(self):
        from backend.core.feature_engineering import build_features
        ohlcv = _make_ohlcv(110)
        row = build_features(ohlcv)
        assert row.regime_label in (-1.0, 0.0, 1.0)

    def test_regime_label_matches_hurst(self):
        """The regime label is a pure thresholding of the Hurst exponent."""
        from backend.core.feature_engineering import (
            build_features,
            _REGIME_RANGE_THRESHOLD,
            _REGIME_TREND_THRESHOLD,
        )
        ohlcv = _make_ohlcv(110)
        row = build_features(ohlcv)
        if row.hurst_exponent > _REGIME_TREND_THRESHOLD:
            assert row.regime_label == 1.0
        elif row.hurst_exponent < _REGIME_RANGE_THRESHOLD:
            assert row.regime_label == -1.0
        else:
            assert row.regime_label == 0.0
