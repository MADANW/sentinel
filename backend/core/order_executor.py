"""
order_executor.py — Alpaca bracket order submission.

Submits a bracket order (entry + stop-loss + take-profit) to Alpaca.
Paper trading is the default; live trading requires TRADING_ENV=live.

Caller responsibilities (enforced by convention, not this module):
  1. Call risk_engine.check_kill_switch() before invoking submit_bracket_order().
  2. Compute qty via risk_engine.validate_order() — do not pass arbitrary quantities.
  3. Call risk_engine.record_trade_result() when the trade closes.

This module never calls the risk engine directly — it handles only order I/O.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrderParams:
    """
    Validated order parameters.

    qty must come from risk_engine.validate_order() — never set it manually.
    stop_price and take_profit_price are in dollars per share.
    """
    symbol: str
    qty: int            # whole shares; from risk_engine.validate_order()
    side: str           # "buy" | "sell"
    entry_price: float  # informational; market order fills at market price
    stop_price: float
    take_profit_price: float


@dataclass(frozen=True)
class OrderResult:
    success: bool
    order_id: str | None
    status: str
    filled_qty: int
    error: str | None = None


class OrderExecutionError(RuntimeError):
    """Raised on unrecoverable configuration errors (e.g. missing credentials)."""


def submit_bracket_order(params: OrderParams) -> OrderResult:
    """
    Submit a bracket order to Alpaca.

    Returns an OrderResult — always returns, never raises on Alpaca API errors.
    Caller should check result.success before calling record_trade_result().

    Args:
        params: Validated order parameters. See OrderParams.

    Raises:
        OrderExecutionError: If ALPACA_API_KEY or ALPACA_SECRET_KEY are missing.
    """
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise OrderExecutionError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set."
        )

    trading_env = os.environ.get("TRADING_ENV", "paper").lower()
    paper = trading_env != "live"

    if paper:
        logger.info("Paper trading mode (TRADING_ENV=%r).", trading_env)
    else:
        logger.warning("LIVE TRADING MODE ACTIVE — real money at risk.")

    side = OrderSide.BUY if params.side == "buy" else OrderSide.SELL

    request = MarketOrderRequest(
        symbol=params.symbol.upper(),
        qty=params.qty,
        side=side,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=round(params.take_profit_price, 2)),
        stop_loss=StopLossRequest(stop_price=round(params.stop_price, 2)),
    )

    client = TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)

    try:
        order = client.submit_order(request)
    except Exception as exc:
        logger.error(
            "Alpaca order submission failed: symbol=%s qty=%d side=%s error=%s",
            params.symbol, params.qty, params.side, exc,
        )
        return OrderResult(
            success=False,
            order_id=None,
            status="failed",
            filled_qty=0,
            error=str(exc),
        )

    filled_qty = int(order.filled_qty) if order.filled_qty is not None else 0

    logger.info(
        "Order submitted: id=%s symbol=%s qty=%d side=%s status=%s",
        order.id, params.symbol, params.qty, params.side, order.status,
    )
    return OrderResult(
        success=True,
        order_id=str(order.id),
        status=str(order.status),
        filled_qty=filled_qty,
    )
