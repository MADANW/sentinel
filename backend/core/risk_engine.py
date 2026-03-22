"""
risk_engine.py — Immutable risk constants and kill-switch enforcement.

RULES:
  - These values are HARDCODED. No env vars, no config files, no overrides.
  - Any attempt to change them requires a code review and a new commit.
  - The kill switch is checked before EVERY order submission.
  - If the kill switch is triggered, the process exits — it does not pause.

Do not add parameters, flags, or any mechanism to relax these limits at runtime.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Final

logger = logging.getLogger(__name__)

# ── Immutable Risk Constants ────────────────────────────────────────────────

MAX_RISK_PER_TRADE: Final[float] = 0.01       # 1% of account equity per trade
MAX_DAILY_LOSS: Final[float] = 0.02           # 2% daily loss → kill switch fires
MAX_TRADES_PER_DAY: Final[int] = 3            # Hard cap; no exceptions
MIN_CONFIDENCE_TO_TRADE: Final[float] = 0.60  # Claude bias must be ≥ 60% confident

# Sanity-check: these must never be mutated after import
_RISK_CONSTANTS_HASH: Final[str] = (
    f"{MAX_RISK_PER_TRADE}|{MAX_DAILY_LOSS}|{MAX_TRADES_PER_DAY}|{MIN_CONFIDENCE_TO_TRADE}"
)


# ── State (process-local, reset each day) ──────────────────────────────────

@dataclass
class DailyState:
    date: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    trades_executed: int = 0
    realized_pnl_pct: float = 0.0  # negative = loss
    kill_switch_triggered: bool = False

    def reset_if_new_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self.date != today:
            logger.info("New trading day %s — resetting daily state.", today)
            self.date = today
            self.trades_executed = 0
            self.realized_pnl_pct = 0.0
            self.kill_switch_triggered = False


_state = DailyState()


# ── Public API ──────────────────────────────────────────────────────────────

class RiskViolation(Exception):
    """Raised when a trade would violate a risk rule."""


def check_kill_switch() -> None:
    """
    Call before submitting any order.
    Raises RiskViolation (and exits the process) if the kill switch has fired.
    """
    _state.reset_if_new_day()

    if _state.kill_switch_triggered:
        _hard_stop("Kill switch already triggered for today.")

    if _state.realized_pnl_pct <= -MAX_DAILY_LOSS:
        _trigger_kill_switch(
            f"Daily loss limit reached: {_state.realized_pnl_pct:.2%} ≤ -{MAX_DAILY_LOSS:.2%}"
        )

    if _state.trades_executed >= MAX_TRADES_PER_DAY:
        raise RiskViolation(
            f"Max trades per day reached ({MAX_TRADES_PER_DAY}). No more orders today."
        )


def validate_order(account_equity: float, stop_distance: float) -> float:
    """
    Compute position size (shares) that risks exactly MAX_RISK_PER_TRADE of equity.

    Args:
        account_equity: Current account equity in dollars.
        stop_distance:  Distance from entry to stop-loss in dollars per share.

    Returns:
        Number of shares to buy (floored to whole shares).

    Raises:
        RiskViolation if inputs are invalid or kill switch is active.
    """
    check_kill_switch()

    if account_equity <= 0:
        raise RiskViolation(f"Invalid account equity: {account_equity}")
    if stop_distance <= 0:
        raise RiskViolation(f"Stop distance must be positive, got {stop_distance}")

    dollar_risk = account_equity * MAX_RISK_PER_TRADE
    shares = dollar_risk / stop_distance

    if shares < 1:
        raise RiskViolation(
            f"Position size too small ({shares:.2f} shares). "
            f"Increase stop distance or account equity."
        )

    result = int(shares)  # floor to whole shares
    logger.info(
        "Position size: %d shares | risk $%.2f (%.1f%% of $%.2f equity)",
        result, dollar_risk, MAX_RISK_PER_TRADE * 100, account_equity,
    )
    return result


def record_trade_result(pnl_pct: float) -> None:
    """
    Call after each trade closes to update daily P&L and trade count.
    pnl_pct: trade P&L as a fraction of account equity (e.g. -0.008 = -0.8%).
    """
    _state.reset_if_new_day()
    _state.trades_executed += 1
    _state.realized_pnl_pct += pnl_pct

    logger.info(
        "Trade recorded: pnl=%.2f%% | daily_total=%.2f%% | trades=%d/%d",
        pnl_pct * 100,
        _state.realized_pnl_pct * 100,
        _state.trades_executed,
        MAX_TRADES_PER_DAY,
    )

    if _state.realized_pnl_pct <= -MAX_DAILY_LOSS:
        _trigger_kill_switch(
            f"Daily loss limit hit after trade close: {_state.realized_pnl_pct:.2%}"
        )


def get_daily_state_summary() -> dict:
    """Return a read-only snapshot of daily state for logging/dashboard."""
    _state.reset_if_new_day()
    return {
        "date": _state.date.isoformat(),
        "trades_executed": _state.trades_executed,
        "max_trades": MAX_TRADES_PER_DAY,
        "realized_pnl_pct": round(_state.realized_pnl_pct * 100, 4),
        "daily_loss_limit_pct": MAX_DAILY_LOSS * 100,
        "kill_switch_triggered": _state.kill_switch_triggered,
    }


# ── Internal helpers ────────────────────────────────────────────────────────

def _trigger_kill_switch(reason: str) -> None:
    _state.kill_switch_triggered = True
    logger.critical("KILL SWITCH TRIGGERED: %s", reason)
    _hard_stop(reason)


def _hard_stop(reason: str) -> None:
    """Log and exit the process. This is intentionally unrecoverable today."""
    logger.critical("HARD STOP — bot process exiting. Reason: %s", reason)
    # Flush logs before exit
    logging.shutdown()
    sys.exit(1)


# ── Self-integrity check ────────────────────────────────────────────────────

def assert_constants_unchanged() -> None:
    """
    Call at startup to verify no one monkeypatched the risk constants.
    This is a last-resort tamper detection — it won't catch compiled bytecode changes.
    """
    current = (
        f"{MAX_RISK_PER_TRADE}|{MAX_DAILY_LOSS}|{MAX_TRADES_PER_DAY}|{MIN_CONFIDENCE_TO_TRADE}"
    )
    if current != _RISK_CONSTANTS_HASH:
        logger.critical(
            "RISK CONSTANT TAMPERING DETECTED. Expected: %s | Got: %s",
            _RISK_CONSTANTS_HASH, current,
        )
        sys.exit(1)
