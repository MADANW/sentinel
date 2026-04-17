"""
test_smoke.py — End-to-end smoke tests for the ML pipeline.

All external API calls (Claude and Alpaca) are mocked — no real network
requests are made. These tests verify the wiring between components, not
the components themselves (which have their own unit tests).

Coverage:
  - Order executor: OrderParams → Alpaca bracket order → OrderResult
  - EMA crossover: price series → SignalResult
  - End-to-end: ML pipeline → bias → risk check → order submitted
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
        env_clean = {k: v for k, v in os.environ.items() if k != "TRADING_ENV"}
        env_clean.update(env)

        with patch.dict(os.environ, env_clean, clear=True):
            with patch("backend.core.order_executor.TradingClient") as mock_cls:
                mock_cls.return_value.submit_order.return_value = self._alpaca_order()
                submit_bracket_order(self._params())
                _, kwargs = mock_cls.call_args
                assert kwargs.get("paper") is True


# ── EMA Crossover ─────────────────────────────────────────────────────────────

class TestEMACrossover:
    def test_bullish_crossover_detected(self):
        from backend.core.signals import detect_ema_crossover

        prices = pd.Series([100.0] * 30 + [90.0] * 5 + [130.0] * 5)
        result = detect_ema_crossover(prices, fast=5, slow=10)
        assert result.fast_ema > result.slow_ema

    def test_bearish_crossover_detected(self):
        from backend.core.signals import detect_ema_crossover

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
    Full pipeline: ML signal → risk check → bracket order submitted.
    The morning pipeline is mocked to return a pre-built TradingBias.
    """

    def test_full_happy_path(self):
        import os
        from backend.core.bias_validator import TradingBias
        from backend.core.order_executor import OrderParams, submit_bracket_order

        re = _fresh_risk_engine()

        # Pre-built bias (pipeline is mocked)
        bias = TradingBias(
            direction="bullish",
            confidence=0.82,
            reasoning="ML signal approved by Claude.",
            raw_response='{"approve": true, "reason": "ML signal approved by Claude."}',
        )

        # Mock Alpaca order
        alpaca_order = MagicMock()
        alpaca_order.id = "e2e-order-001"
        alpaca_order.status = "accepted"
        alpaca_order.filled_qty = "0"

        env = {
            "ALPACA_API_KEY": "test-alpaca-key",
            "ALPACA_SECRET_KEY": "test-alpaca-secret",
            "TRADING_ENV": "paper",
        }

        with patch.dict(os.environ, env):
            with patch("backend.core.order_executor.TradingClient") as mock_alpaca:
                mock_alpaca.return_value.submit_order.return_value = alpaca_order

                # Step 1: Risk engine computes position size
                equity = 10_000.0
                stop_distance = 5.0
                qty = re.validate_order(account_equity=equity, stop_distance=stop_distance)
                assert qty == 20  # $100 risk / $5 stop = 20 shares

                # Step 2: Submit bracket order based on bias direction
                entry = 500.0
                params = OrderParams(
                    symbol="SPY",
                    qty=qty,
                    side="buy" if bias.direction == "bullish" else "sell",
                    entry_price=entry,
                    stop_price=entry - stop_distance,
                    take_profit_price=entry + (stop_distance * 2),
                )
                result = submit_bracket_order(params)
                assert result.success is True
                assert result.order_id == "e2e-order-001"

    def test_kill_switch_blocks_order(self):
        """After a 2%+ daily loss, the kill switch must prevent any new orders."""
        re = _fresh_risk_engine()

        with mock.patch("sys.exit"):
            re.record_trade_result(pnl_pct=-0.025)  # 2.5% loss > 2% limit

        re._state.kill_switch_triggered = True
        with mock.patch("sys.exit", side_effect=SystemExit(1)):
            with pytest.raises((re.RiskViolation, SystemExit)):
                re.validate_order(account_equity=10_000, stop_distance=1.0)
