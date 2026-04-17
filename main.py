"""
main.py — Daily trading session orchestrator.

Run once each morning before market open:
  python main.py --ticker SPY

No user input required after invocation. All gates are automated.

Security:
  - assert_constants_unchanged() called at startup to detect risk constant tampering.
  - Paper trading by default; TRADING_ENV=live required for real capital.
  - Journal failure does NOT abort — trade was already submitted.

Exit codes:
  0 — Success (trade submitted or pipeline returned no-trade cleanly).
  1 — Unrecoverable configuration or infrastructure error.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("main")


def _fetch_account_equity() -> float:
    """
    Fetch current account equity from Alpaca.

    Raises:
        RuntimeError: On missing credentials or API failure.
    """
    from alpaca.trading.client import TradingClient

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")

    trading_env = os.environ.get("TRADING_ENV", "paper").lower()
    paper = trading_env != "live"

    try:
        client = TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)
        account = client.get_account()
        equity = float(account.equity)
        if equity <= 0:
            raise RuntimeError(f"Account equity non-positive: {equity}")
        logger.info("Account equity: $%.2f (%s)", equity, "paper" if paper else "LIVE")
        return equity
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch account equity: {exc}") from exc


def _fetch_current_price(ticker: str) -> float:
    """
    Fetch the latest trade price for a ticker from Alpaca.

    Raises:
        RuntimeError: On missing credentials or API failure.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestTradeRequest

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")

    try:
        client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
        request = StockLatestTradeRequest(symbol_or_symbols=ticker)
        trades = client.get_stock_latest_trade(request)
        price = float(trades[ticker].price)
        logger.info("Current price for %s: $%.4f", ticker, price)
        return price
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch current price for {ticker}: {exc}") from exc


def _compute_stop_distance(ticker: str) -> float:
    """
    Compute ATR-based stop distance from recent OHLCV data.

    Returns the stop distance in dollars per share.
    """
    from backend.core.data_fetcher import fetch_ohlcv

    ohlcv = fetch_ohlcv(ticker, days=20)
    returns = ohlcv["close"].pct_change().abs().dropna()
    atr_pct = float(returns.tail(14).mean())
    current_price = float(ohlcv["close"].iloc[-1])
    stop_distance = current_price * atr_pct * 1.5
    stop_distance = max(stop_distance, 0.01)  # floor at 1 cent
    logger.info("Stop distance for %s: $%.4f (%.4f%%)", ticker, stop_distance, atr_pct * 100)
    return stop_distance


def main(ticker: str) -> int:
    """
    Run the full morning pipeline for a single ticker.

    Returns:
        0 — Success (trade submitted or gates failed cleanly).
        1 — Unrecoverable error (missing config, infrastructure failure).
    """
    from backend.core.risk_engine import assert_constants_unchanged

    # ── Startup integrity check ──────────────────────────────────────────────
    assert_constants_unchanged()

    # ── Run morning pipeline ─────────────────────────────────────────────────
    from backend.core.morning_pipeline import PipelineError, run_morning_pipeline

    try:
        bias = run_morning_pipeline(ticker)
    except PipelineError as exc:
        logger.critical("Pipeline configuration error: %s", exc)
        return 1
    except ValueError as exc:
        logger.critical("Invalid input: %s", exc)
        return 1

    if bias is None:
        logger.info("No trade today — pipeline returned no signal.")
        # Log the pipeline run (gate details not available at this level — morning_pipeline
        # logs gate-specific reasons; here we record the top-level outcome only)
        _log_pipeline_run_safe(ticker=ticker, trade_submitted=False, skip_reason="gate_failed")
        return 0

    logger.info(
        "Signal: direction=%s confidence=%.4f reasoning=%r",
        bias.direction, bias.confidence, bias.reasoning,
    )

    # ── Fetch account state ──────────────────────────────────────────────────
    try:
        account_equity = _fetch_account_equity()
        current_price = _fetch_current_price(ticker)
        stop_distance = _compute_stop_distance(ticker)
    except RuntimeError as exc:
        logger.critical("Failed to fetch account/market data: %s", exc)
        return 1

    # ── Risk engine ──────────────────────────────────────────────────────────
    from backend.core.risk_engine import RiskViolation, validate_order

    try:
        qty = validate_order(account_equity=account_equity, stop_distance=stop_distance)
    except RiskViolation as exc:
        logger.warning("Risk engine rejected order: %s", exc)
        return 0

    side = "buy" if bias.direction == "bullish" else "sell"
    if side == "buy":
        stop_price = current_price - stop_distance
        take_profit_price = current_price + stop_distance * 2.0
    else:
        stop_price = current_price + stop_distance
        take_profit_price = current_price - stop_distance * 2.0

    # ── Submit order ─────────────────────────────────────────────────────────
    from backend.core.order_executor import (
        OrderExecutionError,
        OrderParams,
        submit_bracket_order,
    )

    params = OrderParams(
        symbol=ticker,
        qty=qty,
        side=side,
        entry_price=current_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
    )

    try:
        result = submit_bracket_order(params)
    except OrderExecutionError as exc:
        logger.critical("Order execution configuration error: %s", exc)
        return 1

    if not result.success:
        logger.error("Order submission failed: %s", result.error)
        return 0

    logger.info(
        "Order submitted: ticker=%s side=%s qty=%d order_id=%s",
        ticker, side, qty, result.order_id,
    )

    # ── Journal ──────────────────────────────────────────────────────────────
    from backend.core.journal import log_trade

    try:
        log_trade(
            symbol=ticker,
            direction=bias.direction,
            qty=qty,
            entry_price=current_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            alpaca_order_id=result.order_id,
            bias_confidence=bias.confidence,
            bias_reasoning=bias.reasoning,
        )
        logger.info("Trade logged to journal.")
    except Exception as exc:
        # Journal failure does NOT abort — the trade was already submitted
        logger.error("Journal logging failed (trade already submitted): %s", exc)

    # ── Pipeline run log ─────────────────────────────────────────────────────
    _log_pipeline_run_safe(
        ticker=ticker,
        trade_submitted=result.success,
        skip_reason=None if result.success else result.error,
    )

    return 0


def _log_pipeline_run_safe(
    *,
    ticker: str,
    trade_submitted: bool,
    skip_reason: str | None,
) -> None:
    """
    Log the pipeline outcome to pipeline_runs. Non-fatal — never raises.
    """
    from backend.core.pipeline_logger import log_pipeline_run

    try:
        log_pipeline_run(
            ticker=ticker,
            trade_submitted=trade_submitted,
            skip_reason=skip_reason,
        )
    except Exception as exc:
        # Pipeline log failure must not abort — it is observability only
        logger.error("Pipeline run logging failed (non-fatal): %s", exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="algo-bot morning pipeline — runs once per trading session."
    )
    parser.add_argument(
        "--ticker",
        required=True,
        help="Ticker symbol to trade, e.g. SPY",
    )
    args = parser.parse_args()
    sys.exit(main(ticker=args.ticker))
