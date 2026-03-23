"""
test_morning_pipeline_new.py — Integration tests for the ML-first morning pipeline.

All external API calls are mocked (Alpaca OHLCV, ML model, Monte Carlo, Claude).
Tests verify the full gate logic: ML gate, MC gate, Claude review gate, and
various failure modes.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ── Shared test fixtures ──────────────────────────────────────────────────────

def _make_ohlcv(rows: int = 60) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame for testing."""
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    close = 500.0 + np.arange(rows, dtype=float)
    return pd.DataFrame(
        {
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )


def _make_feature_df():
    """Single-row feature DataFrame for mocking build_features output."""
    from backend.core.feature_engineering import FEATURE_COLUMNS, FeatureRow, feature_row_to_dataframe
    row = FeatureRow(
        ema_crossover_signal=1.0,
        ema_fast=0.001,
        ema_slow=-0.001,
        rsi_14=60.0,
        atr_14_pct=0.01,
        volume_deviation=0.05,
        vwap_distance=0.002,
        prior_day_return=0.003,
    )
    return row, feature_row_to_dataframe(row)


def _make_mc_result(hit_target_rate: float = 0.60):
    """SimulationResult for mocking monte_carlo.simulate."""
    from backend.core.monte_carlo import SimulationResult
    hit_stop = (1.0 - hit_target_rate) * 0.7
    neither = 1.0 - hit_target_rate - hit_stop
    return SimulationResult(
        hit_target_rate=hit_target_rate,
        hit_stop_rate=hit_stop,
        neither_rate=neither,
        expected_pnl_pct=hit_target_rate * 0.02 - hit_stop * 0.01,
        n_paths=1000,
    )


def _claude_approve_response(approve: bool, reason: str = "Test reason.") -> MagicMock:
    """Mock Claude API response for review."""
    import json
    content = MagicMock()
    content.text = json.dumps({"approve": approve, "reason": reason})
    response = MagicMock()
    response.content = [content]
    return response


_BASE_ENV = {
    "ANTHROPIC_API_KEY": "test-key",
    "ALPACA_API_KEY": "test-alpaca-key",
    "ALPACA_SECRET_KEY": "test-alpaca-secret",
}


# ── Happy paths ───────────────────────────────────────────────────────────────

class TestHappyPaths:
    def _run_pipeline(self, ml_prob: float, mc_hit_rate: float = 0.60,
                      claude_approve: bool = True, ticker: str = "SPY"):
        """Run the full pipeline with all dependencies mocked."""
        from backend.core.morning_pipeline import run_morning_pipeline

        ohlcv = _make_ohlcv()
        _, features_df = _make_feature_df()
        mc_result = _make_mc_result(mc_hit_rate)
        claude_resp = _claude_approve_response(claude_approve)

        with patch.dict(os.environ, _BASE_ENV):
            with patch("backend.core.morning_pipeline.fetch_ohlcv", return_value=ohlcv):
                with patch("backend.core.morning_pipeline.fetch_headlines", return_value=[]):
                    with patch("backend.core.morning_pipeline.build_features") as mock_feat:
                        mock_feat.return_value = _make_feature_df()[0]
                        with patch("backend.core.morning_pipeline.feature_row_to_dataframe",
                                   return_value=features_df):
                            with patch("backend.core.morning_pipeline.predict_direction",
                                       return_value=ml_prob):
                                with patch("backend.core.morning_pipeline.simulate",
                                           return_value=mc_result):
                                    with patch("anthropic.Anthropic") as mock_claude:
                                        mock_claude.return_value.messages.create.return_value = claude_resp
                                        return run_morning_pipeline(ticker)

    def test_full_happy_path_bullish(self):
        result = self._run_pipeline(ml_prob=0.75)
        assert result is not None
        assert result.direction == "bullish"
        assert result.confidence == 0.75
        assert result.is_actionable is True

    def test_full_happy_path_bearish(self):
        result = self._run_pipeline(ml_prob=0.30)
        assert result is not None
        assert result.direction == "bearish"

    def test_ticker_normalized_to_uppercase(self):
        result = self._run_pipeline(ml_prob=0.75, ticker="spy")
        assert result is not None  # lowercase ticker accepted, normalized

    def test_bias_reasoning_comes_from_claude(self):
        from backend.core.morning_pipeline import run_morning_pipeline

        ohlcv = _make_ohlcv()
        _, features_df = _make_feature_df()
        mc_result = _make_mc_result()

        import json
        reason = "No major events detected."
        content = MagicMock()
        content.text = json.dumps({"approve": True, "reason": reason})
        claude_resp = MagicMock()
        claude_resp.content = [content]

        with patch.dict(os.environ, _BASE_ENV):
            with patch("backend.core.morning_pipeline.fetch_ohlcv", return_value=ohlcv):
                with patch("backend.core.morning_pipeline.fetch_headlines", return_value=[]):
                    with patch("backend.core.morning_pipeline.build_features") as mock_feat:
                        mock_feat.return_value = _make_feature_df()[0]
                        with patch("backend.core.morning_pipeline.feature_row_to_dataframe",
                                   return_value=features_df):
                            with patch("backend.core.morning_pipeline.predict_direction",
                                       return_value=0.70):
                                with patch("backend.core.morning_pipeline.simulate",
                                           return_value=mc_result):
                                    with patch("anthropic.Anthropic") as mock_claude:
                                        mock_claude.return_value.messages.create.return_value = claude_resp
                                        result = run_morning_pipeline("SPY")

        assert result is not None
        assert result.reasoning == reason


# ── Gate failure tests ────────────────────────────────────────────────────────

class TestGateFailures:
    def _run_with_overrides(self, **kwargs):
        from backend.core.morning_pipeline import run_morning_pipeline

        ohlcv = _make_ohlcv()
        _, features_df = _make_feature_df()
        mc_result = _make_mc_result(kwargs.get("mc_hit_rate", 0.60))
        ml_prob = kwargs.get("ml_prob", 0.75)
        claude_approve = kwargs.get("claude_approve", True)
        claude_resp = _claude_approve_response(claude_approve)

        ohlcv_side_effect = kwargs.get("ohlcv_side_effect", None)
        feature_side_effect = kwargs.get("feature_side_effect", None)
        model_side_effect = kwargs.get("model_side_effect", None)
        mc_side_effect = kwargs.get("mc_side_effect", None)

        with patch.dict(os.environ, _BASE_ENV):
            ohlcv_mock = (
                patch("backend.core.morning_pipeline.fetch_ohlcv",
                      side_effect=ohlcv_side_effect)
                if ohlcv_side_effect
                else patch("backend.core.morning_pipeline.fetch_ohlcv", return_value=ohlcv)
            )
            with ohlcv_mock:
                with patch("backend.core.morning_pipeline.fetch_headlines", return_value=[]):
                    feat_mock = (
                        patch("backend.core.morning_pipeline.build_features",
                              side_effect=feature_side_effect)
                        if feature_side_effect
                        else patch("backend.core.morning_pipeline.build_features",
                                   return_value=_make_feature_df()[0])
                    )
                    with feat_mock:
                        with patch("backend.core.morning_pipeline.feature_row_to_dataframe",
                                   return_value=features_df):
                            model_mock = (
                                patch("backend.core.morning_pipeline.predict_direction",
                                      side_effect=model_side_effect)
                                if model_side_effect
                                else patch("backend.core.morning_pipeline.predict_direction",
                                           return_value=ml_prob)
                            )
                            with model_mock:
                                mc_mock = (
                                    patch("backend.core.morning_pipeline.simulate",
                                          side_effect=mc_side_effect)
                                    if mc_side_effect
                                    else patch("backend.core.morning_pipeline.simulate",
                                               return_value=mc_result)
                                )
                                with mc_mock:
                                    with patch("anthropic.Anthropic") as mock_claude:
                                        mock_claude.return_value.messages.create.return_value = claude_resp
                                        return run_morning_pipeline("SPY")

    def test_ml_dead_zone_returns_none(self):
        result = self._run_with_overrides(ml_prob=0.50)
        assert result is None

    def test_ml_gate_exactly_at_threshold_passes(self):
        result = self._run_with_overrides(ml_prob=0.60)
        assert result is not None
        assert result.direction == "bullish"

    def test_mc_gate_fails_returns_none(self):
        result = self._run_with_overrides(mc_hit_rate=0.40)
        assert result is None

    def test_claude_vetoes_returns_none(self):
        result = self._run_with_overrides(claude_approve=False)
        assert result is None

    def test_ohlcv_fetch_fails_returns_none(self):
        from backend.core.data_fetcher import DataFetcherError
        result = self._run_with_overrides(
            ohlcv_side_effect=DataFetcherError("Alpaca error")
        )
        assert result is None

    def test_feature_engineering_fails_returns_none(self):
        from backend.core.feature_engineering import FeatureError
        result = self._run_with_overrides(
            feature_side_effect=FeatureError("NaN in features")
        )
        assert result is None

    def test_model_tampering_returns_none(self):
        from backend.core.model import ModelTamperingError
        result = self._run_with_overrides(
            model_side_effect=ModelTamperingError("Hash mismatch")
        )
        assert result is None

    def test_model_error_returns_none(self):
        from backend.core.model import ModelError
        result = self._run_with_overrides(
            model_side_effect=ModelError("Model file not found")
        )
        assert result is None

    def test_claude_invalid_json_returns_none(self):
        from backend.core.morning_pipeline import run_morning_pipeline

        ohlcv = _make_ohlcv()
        _, features_df = _make_feature_df()
        mc_result = _make_mc_result()

        bad_content = MagicMock()
        bad_content.text = "not valid json at all"
        bad_resp = MagicMock()
        bad_resp.content = [bad_content]

        with patch.dict(os.environ, _BASE_ENV):
            with patch("backend.core.morning_pipeline.fetch_ohlcv", return_value=ohlcv):
                with patch("backend.core.morning_pipeline.fetch_headlines", return_value=[]):
                    with patch("backend.core.morning_pipeline.build_features",
                               return_value=_make_feature_df()[0]):
                        with patch("backend.core.morning_pipeline.feature_row_to_dataframe",
                                   return_value=features_df):
                            with patch("backend.core.morning_pipeline.predict_direction",
                                       return_value=0.75):
                                with patch("backend.core.morning_pipeline.simulate",
                                           return_value=mc_result):
                                    with patch("anthropic.Anthropic") as mock_claude:
                                        mock_claude.return_value.messages.create.return_value = bad_resp
                                        result = run_morning_pipeline("SPY")

        assert result is None


# ── Ticker validation tests ───────────────────────────────────────────────────

class TestTickerValidation:
    def test_invalid_ticker_raises_value_error(self):
        from backend.core.morning_pipeline import run_morning_pipeline

        with patch.dict(os.environ, _BASE_ENV):
            with pytest.raises(ValueError, match="[Ii]nvalid ticker"):
                run_morning_pipeline("12SPY!")

    def test_too_long_ticker_raises_value_error(self):
        from backend.core.morning_pipeline import run_morning_pipeline

        with patch.dict(os.environ, _BASE_ENV):
            with pytest.raises(ValueError, match="[Ii]nvalid ticker"):
                run_morning_pipeline("ABCDEF")  # 6 chars, max is 5

    def test_empty_ticker_raises_value_error(self):
        from backend.core.morning_pipeline import run_morning_pipeline

        with patch.dict(os.environ, _BASE_ENV):
            with pytest.raises(ValueError):
                run_morning_pipeline("")


# ── Configuration error tests ─────────────────────────────────────────────────

class TestConfigErrors:
    def test_missing_api_key_raises_pipeline_error(self):
        from backend.core.morning_pipeline import run_morning_pipeline, PipelineError

        env_no_key = {k: v for k, v in _BASE_ENV.items() if k != "ANTHROPIC_API_KEY"}
        env_clean = {k: v for k, v in os.environ.items()
                     if k not in ("ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY")}
        env_clean.update(env_no_key)

        with patch.dict(os.environ, env_clean, clear=True):
            with pytest.raises(PipelineError):
                run_morning_pipeline("SPY")
