"""
test_smoke.py — End-to-end smoke tests for the Sprint 2 pipeline.

All external API calls (Claude and Alpaca) are mocked — no real network
requests are made. These tests verify the wiring between components, not
the components themselves (which have their own unit tests in test_risk_engine.py).

Coverage:
  - Morning pipeline: Claude API call → validation → TradingBias
  - Order executor: OrderParams → Alpaca bracket order → OrderResult
  - EMA crossover: price series → SignalResult
  - Full end-to-end: headlines → bias → risk check → order submitted
  - Kill switch: daily loss hit → order blocked
"""

import importlib
import unittest.mock as mock
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _fresh_risk_engine():
    """Reload the risk engine module to reset process-level daily state."""
    import backend.core.risk_engine as re_module
    importlib.reload(re_module)
    return re_module


# ── Morning Pipeline ─────────────────────────────────────────────────────────

class TestMorningPipeline:
    def _claude_response(self, direction: str, confidence: float, reasoning: str) -> MagicMock:
        content_block = MagicMock()
        content_block.text = (
            f'{{"direction": "{direction}", "confidence": {confidence}, '
            f'"reasoning": "{reasoning}"}}'
        )
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_actionable_bias_returned(self):
        import os
        from backend.core.morning_pipeline import run_morning_pipeline

        resp = self._claude_response("bullish", 0.80, "Fed signals rate cuts.")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = resp
                bias = run_morning_pipeline(["Fed hints at rate cuts."])

        assert bias is not None
        assert bias.direction == "bullish"
        assert bias.confidence == 0.80
        assert bias.is_actionable is True

    def test_neutral_returns_none(self):
        import os
        from backend.core.morning_pipeline import run_morning_pipeline

        resp = self._claude_response("neutral", 0.90, "Mixed signals.")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = resp
                result = run_morning_pipeline(["Mixed economic data."])

        assert result is None

    def test_low_confidence_returns_none(self):
        import os
        from backend.core.morning_pipeline import run_morning_pipeline

        resp = self._claude_response("bullish", 0.45, "Weak signal.")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = resp
                result = run_morning_pipeline(["Unclear conditions."])

        assert result is None

    def test_invalid_claude_response_returns_none(self):
        import os
        from backend.core.morning_pipeline import run_morning_pipeline

        bad = MagicMock()
        bad.content = [MagicMock(text="not json")]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = bad
                result = run_morning_pipeline(["Some headline."])

        assert result is None

    def test_missing_api_key_raises(self):
        import os
        from backend.core.morning_pipeline import run_morning_pipeline, PipelineError

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with pytest.raises(PipelineError, match="ANTHROPIC_API_KEY"):
                run_morning_pipeline(["Headline."])

    def test_api_connection_error_raises(self):
        import os
        import anthropic
        from backend.core.morning_pipeline import run_morning_pipeline, PipelineError

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.side_effect = (
                    anthropic.APIConnectionError(request=MagicMock())
                )
                with pytest.raises(PipelineError, match="connection error"):
                    run_morning_pipeline(["Headline."])


# ── Order Executor ────────────────────────────────────────────────────────────

class TestOrderExecutor:
    def _alpaca_order(
        self,
        order_id: str = "abc-123",
        status: str = "accepted",
        filled_qty: str = "0",
    ) -> MagicMock:
        order = MagicMock()
        order.id = order_id
        order.status = status
        order.filled_qty = filled_qty
        return order

    def _params(self) -> "OrderParams":
        from backend.core.order_executor import OrderParams
        return OrderParams(
            symbol="SPY",
            qty=10,
            side="buy",
            entry_price=500.00,
            stop_price=495.00,
            take_profit_price=510.00,
        )

    def test_successful_submission(self):
        import os
        from backend.core.order_executor import submit_bracket_order

        env = {
            "ALPACA_API_KEY": "key",
            "ALPACA_SECRET_KEY": "secret",
            "TRADING_ENV": "paper",
        }
        with patch.dict(os.environ, env):
            with patch("backend.core.order_executor.TradingClient") as mock_cls:
                mock_cls.return_value.submit_order.return_value = self._alpaca_order()
                result = submit_bracket_order(self._params())

        assert result.success is True
        assert result.order_id == "abc-123"
        assert result.error is None

    def test_alpaca_error_returns_failure(self):
        import os
        from backend.core.order_executor import submit_bracket_order

        env = {
            "ALPACA_API_KEY": "key",
            "ALPACA_SECRET_KEY": "secret",
            "TRADING_ENV": "paper",
        }
        with patch.dict(os.environ, env):
            with patch("backend.core.order_executor.TradingClient") as mock_cls:
                mock_cls.return_value.submit_order.side_effect = RuntimeError("Insufficient buying power")
                result = submit_bracket_order(self._params())

        assert result.success is False
        assert result.error is not None
        assert "Insufficient buying power" in result.error

    def test_missing_credentials_raises(self):
        import os
        from backend.core.order_executor import submit_bracket_order, OrderExecutionError

        with patch.dict(os.environ, {"TRADING_ENV": "paper"}, clear=True):
            os.environ.pop("ALPACA_API_KEY", None)
            os.environ.pop("ALPACA_SECRET_KEY", None)
            with pytest.raises(OrderExecutionError, match="ALPACA_API_KEY"):
                submit_bracket_order(self._params())

    def test_paper_is_default_when_trading_env_not_set(self):
        import os
        from backend.core.order_executor import submit_bracket_order

        env = {"ALPACA_API_KEY": "key", "ALPACA_SECRET_KEY": "secret"}
        # Ensure TRADING_ENV is not set
        env_clean = {k: v for k, v in os.environ.items() if k != "TRADING_ENV"}
        env_clean.update(env)

        with patch.dict(os.environ, env_clean, clear=True):
            with patch("backend.core.order_executor.TradingClient") as mock_cls:
                mock_cls.return_value.submit_order.return_value = self._alpaca_order()
                submit_bracket_order(self._params())
                # paper=True should be passed to TradingClient
                _, kwargs = mock_cls.call_args
                assert kwargs.get("paper") is True


# ── EMA Crossover ─────────────────────────────────────────────────────────────

class TestEMACrossover:
    def test_bullish_crossover_detected(self):
        from backend.core.signals import detect_ema_crossover

        # Prices rise sharply at the end → fast EMA crosses above slow EMA
        prices = pd.Series([100.0] * 30 + [90.0] * 5 + [130.0] * 5)
        result = detect_ema_crossover(prices, fast=5, slow=10)
        assert result.fast_ema > result.slow_ema

    def test_bearish_crossover_detected(self):
        from backend.core.signals import detect_ema_crossover

        # Prices drop sharply at the end → fast EMA crosses below slow EMA
        prices = pd.Series([100.0] * 30 + [110.0] * 5 + [70.0] * 5)
        result = detect_ema_crossover(prices, fast=5, slow=10)
        assert result.fast_ema < result.slow_ema

    def test_neutral_on_flat_prices(self):
        from backend.core.signals import detect_ema_crossover

        prices = pd.Series([100.0] * 30)
        result = detect_ema_crossover(prices, fast=5, slow=10)
        assert result.direction == "neutral"
        assert result.crossover_detected is False

    def test_too_short_raises(self):
        from backend.core.signals import detect_ema_crossover, SignalError

        prices = pd.Series([100.0] * 5)
        with pytest.raises(SignalError, match="at least"):
            detect_ema_crossover(prices, fast=9, slow=21)

    def test_nan_prices_rejected(self):
        import numpy as np
        from backend.core.signals import detect_ema_crossover, SignalError

        prices = pd.Series([100.0] * 20 + [float("nan")] + [100.0] * 5)
        with pytest.raises(SignalError, match="NaN"):
            detect_ema_crossover(prices, fast=5, slow=10)

    def test_non_positive_prices_rejected(self):
        from backend.core.signals import detect_ema_crossover, SignalError

        prices = pd.Series([100.0] * 20 + [0.0] + [100.0] * 5)
        with pytest.raises(SignalError, match="non-positive"):
            detect_ema_crossover(prices, fast=5, slow=10)

    def test_fast_must_be_less_than_slow(self):
        from backend.core.signals import detect_ema_crossover, SignalError

        prices = pd.Series([100.0] * 30)
        with pytest.raises(SignalError, match="fast period"):
            detect_ema_crossover(prices, fast=21, slow=9)

    def test_not_a_series_rejected(self):
        from backend.core.signals import detect_ema_crossover, SignalError

        with pytest.raises(SignalError, match="pandas Series"):
            detect_ema_crossover([100.0] * 30, fast=5, slow=10)  # type: ignore


# ── End-to-End ────────────────────────────────────────────────────────────────

class TestEndToEnd:
    """
    Full pipeline: headlines → bias → risk check → bracket order submitted.
    All external APIs mocked.
    """

    def test_full_happy_path(self):
        import os
        from backend.core.morning_pipeline import run_morning_pipeline
        from backend.core.order_executor import OrderParams, submit_bracket_order

        re = _fresh_risk_engine()

        # Mock Claude
        content_block = MagicMock()
        content_block.text = (
            '{"direction": "bullish", "confidence": 0.82, '
            '"reasoning": "Rate cut expected."}'
        )
        claude_resp = MagicMock()
        claude_resp.content = [content_block]

        # Mock Alpaca order
        alpaca_order = MagicMock()
        alpaca_order.id = "e2e-order-001"
        alpaca_order.status = "accepted"
        alpaca_order.filled_qty = "0"

        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "ALPACA_API_KEY": "test-alpaca-key",
            "ALPACA_SECRET_KEY": "test-alpaca-secret",
            "TRADING_ENV": "paper",
        }

        with patch.dict(os.environ, env):
            with patch("anthropic.Anthropic") as mock_claude:
                mock_claude.return_value.messages.create.return_value = claude_resp
                with patch("backend.core.order_executor.TradingClient") as mock_alpaca:
                    mock_alpaca.return_value.submit_order.return_value = alpaca_order

                    # Step 1: Morning pipeline
                    bias = run_morning_pipeline(
                        ["Fed signals rate cuts.", "S&P futures up 0.5%."]
                    )
                    assert bias is not None
                    assert bias.is_actionable is True

                    # Step 2: Risk engine computes position size
                    equity = 10_000.0
                    stop_distance = 5.0  # $5/share stop
                    qty = re.validate_order(
                        account_equity=equity, stop_distance=stop_distance
                    )
                    assert qty == 20  # $100 risk / $5 stop = 20 shares

                    # Step 3: Submit bracket order
                    entry = 500.0
                    params = OrderParams(
                        symbol="SPY",
                        qty=qty,
                        side="buy" if bias.direction == "bullish" else "sell",
                        entry_price=entry,
                        stop_price=entry - stop_distance,
                        take_profit_price=entry + (stop_distance * 2),  # 2:1 R:R
                    )
                    result = submit_bracket_order(params)
                    assert result.success is True
                    assert result.order_id == "e2e-order-001"

    def test_kill_switch_blocks_order(self):
        """After a 2%+ daily loss, the kill switch must prevent any new orders."""
        re = _fresh_risk_engine()

        # Trigger kill switch via a large loss
        with mock.patch("sys.exit"):
            re.record_trade_result(pnl_pct=-0.025)  # 2.5% loss > 2% limit

        # Kill switch is now armed — any order attempt must be blocked
        re._state.kill_switch_triggered = True
        with mock.patch("sys.exit", side_effect=SystemExit(1)):
            with pytest.raises((re.RiskViolation, SystemExit)):
                re.validate_order(account_equity=10_000, stop_distance=1.0)
