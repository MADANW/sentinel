"""
pipeline_logger.py — Record one row per main.py run into the pipeline_runs table.

Called by main.py after run_morning_pipeline() completes (regardless of outcome).
The dashboard reads the latest row to display gate status for each gate card.

Security notes:
  - Uses SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (server-side only, same as journal.py).
  - ml_signal validated against an explicit allowlist before insert.
  - claude_reason truncated to 500 chars before insert.
  - Caller (main.py) must wrap this in try/except — a logging failure must never
    abort the process, especially after a bracket order has already been submitted.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_VALID_ML_SIGNALS = frozenset({"bullish", "bearish", "dead_zone"})
_CLAUDE_REASON_MAX_LEN = 500


class PipelineLoggerError(RuntimeError):
    """Raised when a pipeline_runs insert fails."""


def _client():
    """Create a Supabase client. Raises PipelineLoggerError on missing credentials."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise PipelineLoggerError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set."
        )

    try:
        from supabase import create_client
    except ImportError as exc:
        raise PipelineLoggerError(f"supabase-py not installed: {exc}") from exc

    return create_client(url, key)


def log_pipeline_run(
    *,
    ticker: str,
    ml_probability: float | None = None,
    ml_signal: str | None = None,       # 'bullish' | 'bearish' | 'dead_zone'
    mc_hit_rate: float | None = None,
    mc_passed: bool | None = None,
    claude_approved: bool | None = None,
    claude_reason: str | None = None,
    trade_submitted: bool = False,
    skip_reason: str | None = None,
) -> str:
    """
    Insert one row into pipeline_runs.

    Args:
        ticker:          Ticker symbol this run was for.
        ml_probability:  Raw ML model output probability (0.0–1.0).
        ml_signal:       Classified direction: 'bullish', 'bearish', or 'dead_zone'.
        mc_hit_rate:     Monte Carlo hit-target rate (0.0–1.0).
        mc_passed:       Whether the MC gate passed (hit_rate >= 0.55).
        claude_approved: Whether Claude approved the signal (strict bool).
        claude_reason:   Claude's reason string (sanitized, truncated to 500 chars).
        trade_submitted: True if a bracket order was successfully submitted.
        skip_reason:     Human-readable explanation when trade_submitted=False.

    Returns:
        UUID of the inserted row.

    Raises:
        ValueError:            If ml_signal is not a valid value.
        PipelineLoggerError:   On missing credentials or Supabase errors.
    """
    if ml_signal is not None and ml_signal not in _VALID_ML_SIGNALS:
        raise ValueError(
            f"ml_signal must be one of {sorted(_VALID_ML_SIGNALS)}, got {ml_signal!r}."
        )

    client = _client()

    row: dict[str, Any] = {
        "ticker": ticker.upper(),
        "trade_submitted": trade_submitted,
    }

    if ml_probability is not None:
        row["ml_probability"] = round(float(ml_probability), 4)
    if ml_signal is not None:
        row["ml_signal"] = ml_signal
    if mc_hit_rate is not None:
        row["mc_hit_rate"] = round(float(mc_hit_rate), 4)
    if mc_passed is not None:
        row["mc_passed"] = bool(mc_passed)
    if claude_approved is not None:
        row["claude_approved"] = bool(claude_approved)
    if claude_reason is not None:
        row["claude_reason"] = claude_reason[:_CLAUDE_REASON_MAX_LEN]
    if skip_reason is not None:
        row["skip_reason"] = skip_reason

    result = client.table("pipeline_runs").insert(row).execute()

    if not result.data:
        raise PipelineLoggerError("Supabase insert returned no data.")

    run_id: str = result.data[0]["id"]
    logger.info(
        "Pipeline run logged: id=%s ticker=%s trade_submitted=%s",
        run_id, ticker, trade_submitted,
    )
    return run_id
