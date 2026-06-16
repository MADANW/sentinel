"""
bias_writer.py — Write the morning bias result to a local JSON file for MT5 EA consumption.

The MT5 EAs poll this file via MQL5 FileOpen/FileReadString before entering any position.
If the file is absent, stale (> BIAS_MAX_AGE_HOURS), or unreadable, the EA skips the trade.

File location:
  Default: /tmp/algo-bot-bias.json
  Override: set BIAS_FILE_PATH environment variable.

File format (UTF-8 JSON, written atomically):
  {
    "direction":  "bullish" | "bearish" | "neutral",
    "confidence": 0.0 – 1.0,
    "reasoning":  "...",
    "timestamp":  "2026-06-15T09:45:00+00:00"   (ISO 8601 UTC)
  }

Security:
  - Written to /tmp by default (not committed, not served).
  - Atomic write via .tmp → os.replace to avoid partial reads by MT5.
  - MQL5 EAs must validate timestamp age before trusting the direction.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .bias_validator import TradingBias

logger = logging.getLogger(__name__)

# MT5 EAs reject bias files older than this
BIAS_MAX_AGE_HOURS: int = 8

_DEFAULT_PATH = Path("/tmp/algo-bot-bias.json")


def _bias_file_path() -> Path:
    raw = os.environ.get("BIAS_FILE_PATH")
    return Path(raw) if raw else _DEFAULT_PATH


def write_bias_file(bias: TradingBias | None) -> Path:
    """
    Write morning bias to the shared JSON file consumed by MT5 EAs.

    Args:
        bias: TradingBias from run_morning_pipeline, or None if no trade today.
              When None, writes direction="neutral" so EAs know the pipeline ran
              but produced no signal (as opposed to the file being absent/stale).

    Returns:
        Path of the written file.

    Raises:
        OSError: If the file cannot be written.
    """
    path = _bias_file_path()

    payload: dict = {
        "direction":  bias.direction if bias is not None else "neutral",
        "confidence": round(float(bias.confidence), 4) if bias is not None else 0.0,
        "reasoning":  (bias.reasoning[:500] if bias is not None and bias.reasoning else ""),
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)

    logger.info(
        "Bias file written: path=%s direction=%s confidence=%.4f",
        path, payload["direction"], payload["confidence"],
    )
    return path


def read_bias_file() -> dict | None:
    """
    Read and validate the bias file. Returns None if absent, unreadable, or stale.
    Intended for testing and debugging — MT5 EAs read the file directly via MQL5 I/O.
    """
    from datetime import timedelta

    path = _bias_file_path()
    if not path.exists():
        logger.warning("Bias file not found: %s", path)
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Bias file unreadable: %s", exc)
        return None

    try:
        written_at = datetime.fromisoformat(payload["timestamp"])
    except (KeyError, ValueError) as exc:
        logger.error("Bias file missing/invalid timestamp: %s", exc)
        return None

    age = datetime.now(timezone.utc) - written_at
    if age > timedelta(hours=BIAS_MAX_AGE_HOURS):
        logger.warning("Bias file stale: age=%s > %dh", age, BIAS_MAX_AGE_HOURS)
        return None

    return payload
