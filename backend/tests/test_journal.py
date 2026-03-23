"""
Tests for journal.py — all Supabase calls are mocked.

These tests verify the journalling logic, not the Supabase client itself.
"""

import unittest.mock as mock
from unittest.mock import MagicMock, patch

import pytest


def _mock_client_for_insert(returned_id: str = "trade-uuid-001") -> MagicMock:
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": returned_id}
    ]
    return client


def _mock_client_for_update(found: bool = True) -> MagicMock:
    client = MagicMock()
    data = [{"id": "trade-uuid-001", "status": "closed"}] if found else []
    (client.table.return_value
     .update.return_value
     .eq.return_value
     .execute.return_value.data) = data
    return client


def _mock_client_for_select(trades: list | None = None) -> MagicMock:
    client = MagicMock()
    (client.table.return_value
     .select.return_value
     .gte.return_value
     .order.return_value
     .execute.return_value.data) = trades
    return client


_ENV = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test-service-key",
}


class TestLogTrade:
    def test_returns_trade_id(self):
        import os
        from backend.core.journal import log_trade

        with patch.dict(os.environ, _ENV):
            with patch("backend.core.journal._client", return_value=_mock_client_for_insert()):
                trade_id = log_trade(
                    symbol="SPY",
                    direction="bullish",
                    qty=20,
                    entry_price=500.0,
                    stop_price=495.0,
                    take_profit_price=510.0,
                    alpaca_order_id="alpaca-123",
                    bias_confidence=0.82,
                    bias_reasoning="Fed rate cut expected.",
                )
        assert trade_id == "trade-uuid-001"

    def test_symbol_uppercased(self):
        import os
        from backend.core.journal import log_trade

        mock_client = _mock_client_for_insert()
        with patch.dict(os.environ, _ENV):
            with patch("backend.core.journal._client", return_value=mock_client):
                log_trade(
                    symbol="spy",  # lowercase
                    direction="bullish",
                    qty=20,
                    entry_price=500.0,
                    stop_price=495.0,
                    take_profit_price=510.0,
                )
        insert_call = mock_client.table.return_value.insert.call_args[0][0]
        assert insert_call["symbol"] == "SPY"

    def test_bias_reasoning_truncated_to_500(self):
        import os
        from backend.core.journal import log_trade

        mock_client = _mock_client_for_insert()
        long_reasoning = "x" * 600

        with patch.dict(os.environ, _ENV):
            with patch("backend.core.journal._client", return_value=mock_client):
                log_trade(
                    symbol="SPY",
                    direction="bullish",
                    qty=10,
                    entry_price=500.0,
                    stop_price=495.0,
                    take_profit_price=510.0,
                    bias_reasoning=long_reasoning,
                )
        insert_call = mock_client.table.return_value.insert.call_args[0][0]
        assert len(insert_call["bias_reasoning"]) == 500

    def test_neutral_direction_rejected(self):
        import os
        from backend.core.journal import log_trade

        with patch.dict(os.environ, _ENV):
            with pytest.raises(ValueError, match="direction"):
                log_trade(
                    symbol="SPY",
                    direction="neutral",
                    qty=20,
                    entry_price=500.0,
                    stop_price=495.0,
                    take_profit_price=510.0,
                )

    def test_zero_qty_rejected(self):
        import os
        from backend.core.journal import log_trade

        with patch.dict(os.environ, _ENV):
            with pytest.raises(ValueError, match="qty"):
                log_trade(
                    symbol="SPY",
                    direction="bullish",
                    qty=0,
                    entry_price=500.0,
                    stop_price=495.0,
                    take_profit_price=510.0,
                )

    def test_missing_credentials_raises(self):
        import os
        from backend.core.journal import log_trade, JournalError

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
            with pytest.raises(JournalError, match="SUPABASE_URL"):
                log_trade(
                    symbol="SPY",
                    direction="bullish",
                    qty=20,
                    entry_price=500.0,
                    stop_price=495.0,
                    take_profit_price=510.0,
                )

    def test_supabase_returns_no_data_raises(self):
        import os
        from backend.core.journal import log_trade, JournalError

        mock_client = MagicMock()
        mock_client.table.return_value.insert.return_value.execute.return_value.data = []

        with patch.dict(os.environ, _ENV):
            with patch("backend.core.journal._client", return_value=mock_client):
                with pytest.raises(JournalError, match="no data"):
                    log_trade(
                        symbol="SPY",
                        direction="bullish",
                        qty=10,
                        entry_price=500.0,
                        stop_price=495.0,
                        take_profit_price=510.0,
                    )


class TestCloseTrade:
    def test_close_sets_status_and_pnl(self):
        import os
        from backend.core.journal import close_trade

        mock_client = _mock_client_for_update(found=True)
        with patch.dict(os.environ, _ENV):
            with patch("backend.core.journal._client", return_value=mock_client):
                close_trade("trade-uuid-001", fill_price=508.50, pnl_pct=0.0085)

        update_payload = mock_client.table.return_value.update.call_args[0][0]
        assert update_payload["status"] == "closed"
        assert update_payload["fill_price"] == 508.50
        assert update_payload["pnl_pct"] == 0.0085

    def test_not_found_raises(self):
        import os
        from backend.core.journal import close_trade, JournalError

        with patch.dict(os.environ, _ENV):
            with patch("backend.core.journal._client", return_value=_mock_client_for_update(found=False)):
                with pytest.raises(JournalError, match="not found"):
                    close_trade("nonexistent-id", fill_price=500.0, pnl_pct=0.0)


class TestGetTodaysTrades:
    def test_returns_trades(self):
        import os
        from backend.core.journal import get_todays_trades

        mock_trades = [
            {"id": "t1", "symbol": "SPY", "direction": "bullish", "status": "open"},
        ]
        with patch.dict(os.environ, _ENV):
            with patch("backend.core.journal._client", return_value=_mock_client_for_select(mock_trades)):
                result = get_todays_trades()

        assert len(result) == 1
        assert result[0]["symbol"] == "SPY"

    def test_none_from_supabase_returns_empty_list(self):
        import os
        from backend.core.journal import get_todays_trades

        with patch.dict(os.environ, _ENV):
            with patch("backend.core.journal._client", return_value=_mock_client_for_select(None)):
                result = get_todays_trades()

        assert result == []
