"""
journal.py — Trade journal: log and update trades in Supabase.

Called after order submission and after trade close. Provides the data
source for the Next.js dashboard.

Security notes:
  - Uses SUPABASE_SERVICE_ROLE_KEY (server-side only — never expose to browser).
  - bias_reasoning is truncated to 500 chars before storage.
  - No raw SQL — all queries go through the Supabase Python client.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class JournalError(RuntimeError):
    """Raised when a journal operation fails."""


def _client():
    """Create a Supabase client. Raises JournalError on missing credentials."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise JournalError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set."
        )

    try:
        from supabase import create_client
    except ImportError as exc:
        raise JournalError(f"supabase-py not installed: {exc}") from exc

    return create_client(url, key)


def log_trade(
    *,
    symbol: str,
    direction: str,
    qty: int,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
    alpaca_order_id: str | None = None,
    bias_confidence: float | None = None,
    bias_reasoning: str | None = None,
) -> str:
    """
    Insert a new open trade into the journal.

    Returns the UUID of the inserted row.

    Args:
        symbol:            Ticker symbol, e.g. "SPY".
        direction:         "bullish" or "bearish". "neutral" is rejected.
        qty:               Number of shares (must be positive).
        entry_price:       Intended entry price.
        stop_price:        Stop-loss price.
        take_profit_price: Take-profit price.
        alpaca_order_id:   Alpaca order UUID (optional).
        bias_confidence:   Claude confidence score 0.0–1.0 (optional).
        bias_reasoning:    Claude reasoning string (optional, truncated to 500 chars).

    Raises:
        ValueError:    On invalid direction or qty.
        JournalError:  On missing credentials or Supabase errors.
    """
    if direction not in ("bullish", "bearish"):
        raise ValueError(
            f"direction must be 'bullish' or 'bearish', got {direction!r}."
        )
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}.")

    client = _client()

    row: dict[str, Any] = {
        "symbol": symbol.upper(),
        "direction": direction,
        "qty": qty,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
        "status": "open",
    }
    if alpaca_order_id is not None:
        row["alpaca_order_id"] = alpaca_order_id
    if bias_confidence is not None:
        row["bias_confidence"] = round(float(bias_confidence), 3)
    if bias_reasoning is not None:
        row["bias_reasoning"] = bias_reasoning[:500]

    result = client.table("trades").insert(row).execute()

    if not result.data:
        raise JournalError("Supabase insert returned no data.")

    trade_id: str = result.data[0]["id"]
    logger.info(
        "Trade logged: id=%s symbol=%s direction=%s qty=%d",
        trade_id, symbol, direction, qty,
    )
    return trade_id


def close_trade(trade_id: str, *, fill_price: float, pnl_pct: float) -> None:
    """
    Mark a trade as closed with its actual fill price and realised P&L.

    Args:
        trade_id:   UUID of the trade to close.
        fill_price: Actual closing fill price.
        pnl_pct:    Realised P&L as a fraction of account equity
                    (e.g. -0.008 = -0.8%).

    Raises:
        JournalError: If the trade is not found or the update fails.
    """
    client = _client()

    result = (
        client.table("trades")
        .update({
            "status": "closed",
            "fill_price": fill_price,
            "pnl_pct": round(float(pnl_pct), 6),
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", trade_id)
        .execute()
    )

    if not result.data:
        raise JournalError(
            f"Trade {trade_id!r} not found or update failed."
        )

    logger.info(
        "Trade closed: id=%s fill=%.4f pnl=%.2f%%",
        trade_id, fill_price, pnl_pct * 100,
    )


def get_todays_trades() -> list[dict]:
    """
    Fetch all trades opened today (UTC), ordered newest-first.

    Returns an empty list if there are no trades or on Supabase errors.
    """
    client = _client()

    today = date.today().isoformat()  # "YYYY-MM-DD"

    result = (
        client.table("trades")
        .select("*")
        .gte("created_at", today)
        .order("created_at", desc=True)
        .execute()
    )

    return result.data or []
