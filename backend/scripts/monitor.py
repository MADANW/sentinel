"""
monitor.py — Position monitor: polls Alpaca for closed fills, updates journal.

Runs as a long-lived process (managed by sentinel-monitor.service). Polls every
POLL_INTERVAL_SECONDS for positions/orders that closed since the last check, then
calls journal.close_trade() to update the Supabase record with fill price and P&L.

Design:
  - Only touches trades that are 'open' in the journal (status == 'open').
  - Matches journal entries to Alpaca orders by alpaca_order_id.
  - Computes pnl_pct as (fill_price - entry_price) / entry_price * direction_sign.
  - Stops cleanly on SIGTERM (systemd sends this on unit stop).

Usage:
    python -m backend.scripts.monitor            # runs forever
    python -m backend.scripts.monitor --once     # single poll, then exit (for testing)

Exit codes:
    0 — Clean shutdown (SIGTERM or --once completed).
    1 — Configuration error (missing credentials).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS: int = 60


# ---------------------------------------------------------------------------
# Alpaca helpers
# ---------------------------------------------------------------------------

def _alpaca_client():
    from alpaca.trading.client import TradingClient

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")

    paper = os.environ.get("TRADING_ENV", "paper").lower() != "live"
    return TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)


def _fetch_closed_orders_today(client) -> dict[str, dict]:
    """
    Return {order_id: {fill_price, filled_qty, status}} for orders closed today.
    Only returns filled orders (status == 'filled').
    """
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    today_utc = date.today().isoformat()

    request = GetOrdersRequest(
        status=QueryOrderStatus.CLOSED,
        after=today_utc,
        limit=100,
    )

    try:
        orders = client.get_orders(filter=request)
    except Exception as exc:
        logger.error("Failed to fetch orders from Alpaca: %s", exc)
        return {}

    result: dict[str, dict] = {}
    for order in orders:
        if str(order.status) != "filled":
            continue
        fill = float(order.filled_avg_price) if order.filled_avg_price else None
        if fill is None:
            continue
        result[str(order.id)] = {
            "fill_price": fill,
            "filled_qty": int(order.filled_qty or 0),
            "status": str(order.status),
        }
    return result


# ---------------------------------------------------------------------------
# Journal helpers
# ---------------------------------------------------------------------------

def _get_open_journal_trades() -> list[dict]:
    from backend.core.journal import _client as journal_client, JournalError

    try:
        client = journal_client()
        result = (
            client.table("trades")
            .select("id, alpaca_order_id, direction, entry_price, qty")
            .eq("status", "open")
            .execute()
        )
        return result.data or []
    except JournalError as exc:
        logger.error("Failed to fetch open trades from journal: %s", exc)
        return []


def _close_journal_trade(trade_id: str, fill_price: float, pnl_pct: float) -> None:
    from backend.core.journal import close_trade, JournalError

    try:
        close_trade(trade_id, fill_price=fill_price, pnl_pct=pnl_pct)
    except JournalError as exc:
        logger.error("Failed to close trade %s in journal: %s", trade_id, exc)


# ---------------------------------------------------------------------------
# Core poll logic
# ---------------------------------------------------------------------------

def _compute_pnl_pct(entry_price: float, fill_price: float, direction: str) -> float:
    if entry_price <= 0:
        return 0.0
    raw = (fill_price - entry_price) / entry_price
    return raw if direction == "bullish" else -raw


def poll_once() -> int:
    """Run one poll cycle. Returns count of trades closed."""
    try:
        client = _alpaca_client()
    except RuntimeError as exc:
        logger.critical("Alpaca client init failed: %s", exc)
        return 0

    closed_orders = _fetch_closed_orders_today(client)
    if not closed_orders:
        logger.debug("No closed orders found this poll.")
        return 0

    open_trades = _get_open_journal_trades()
    if not open_trades:
        logger.debug("No open journal trades to reconcile.")
        return 0

    closed_count = 0
    for trade in open_trades:
        order_id = trade.get("alpaca_order_id")
        if not order_id or order_id not in closed_orders:
            continue

        order = closed_orders[order_id]
        fill_price = order["fill_price"]
        entry_price = float(trade["entry_price"])
        direction = trade["direction"]

        pnl_pct = _compute_pnl_pct(entry_price, fill_price, direction)

        logger.info(
            "Closing trade: id=%s order=%s fill=%.4f pnl=%.4f%%",
            trade["id"], order_id, fill_price, pnl_pct * 100,
        )
        _close_journal_trade(trade["id"], fill_price=fill_price, pnl_pct=pnl_pct)
        closed_count += 1

    return closed_count


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

_running = True


def _handle_sigterm(signum, frame):
    global _running
    logger.info("SIGTERM received — shutting down monitor.")
    _running = False


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="sentinel position monitor")
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single poll then exit (for testing).",
    )
    args = parser.parse_args(argv)

    # Load .env if present (for local runs)
    env_path = __import__("pathlib").Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    signal.signal(signal.SIGTERM, _handle_sigterm)

    logger.info("Monitor started. Poll interval: %ds.", POLL_INTERVAL_SECONDS)

    if args.once:
        count = poll_once()
        logger.info("Single poll complete. Trades closed: %d", count)
        return 0

    global _running
    while _running:
        try:
            count = poll_once()
            if count:
                logger.info("Poll: closed %d trade(s).", count)
            else:
                logger.debug("Poll: no trades to close.")
        except Exception as exc:
            logger.error("Unexpected poll error (continuing): %s", exc)

        for _ in range(POLL_INTERVAL_SECONDS):
            if not _running:
                break
            time.sleep(1)

    logger.info("Monitor stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
