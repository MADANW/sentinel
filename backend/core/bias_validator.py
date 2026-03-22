"""
bias_validator.py — Validate and sanitize Claude API responses before they
influence any trading decision.

Claude is a text model. Its output must be treated as UNTRUSTED INPUT.
This module parses the structured JSON response and rejects anything that
doesn't conform to an explicit schema — no exceptions, no fallbacks that
silently trade on bad data.

Threat model:
  - Prompt injection in news headlines injected into the Claude prompt
  - Model hallucination producing out-of-range confidence values
  - Malformed JSON causing silent defaults that look like valid signals
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from .risk_engine import MIN_CONFIDENCE_TO_TRADE

logger = logging.getLogger(__name__)

Bias = Literal["bullish", "bearish", "neutral"]

# Characters allowed in the reasoning field — whitespace + printable ASCII
# Reject anything that looks like code, SQL, or shell injection
_SAFE_TEXT_PATTERN = re.compile(r'^[\w\s.,;:!?()\-\'\"\/\+\%\$\#\@\&\*\[\]]+$')

# Maximum length for free-text reasoning to prevent resource exhaustion
_MAX_REASONING_LEN = 1000


@dataclass(frozen=True)
class TradingBias:
    """
    Validated, immutable trading bias parsed from a Claude API response.

    Only constructed by `parse_bias_response` — never instantiate directly
    from untrusted data.
    """
    direction: Bias
    confidence: float      # 0.0 – 1.0
    reasoning: str         # sanitized, truncated
    raw_response: str      # original JSON string for audit logging

    @property
    def is_actionable(self) -> bool:
        """True only if confidence clears the minimum threshold and direction is not neutral."""
        return (
            self.direction != "neutral"
            and self.confidence >= MIN_CONFIDENCE_TO_TRADE
        )


class BiasValidationError(ValueError):
    """Raised when Claude's response cannot be validated."""


def parse_bias_response(raw: str) -> TradingBias:
    """
    Parse and validate a Claude API response into a TradingBias.

    Expected JSON format:
    {
        "direction": "bullish" | "bearish" | "neutral",
        "confidence": 0.0-1.0,
        "reasoning": "..."
    }

    Raises:
        BiasValidationError on any validation failure. NEVER returns a
        TradingBias with invalid data — callers must handle the exception.
    """
    if not raw or not isinstance(raw, str):
        raise BiasValidationError("Empty or non-string response from Claude API.")

    if len(raw) > 10_000:
        raise BiasValidationError(
            f"Response suspiciously large ({len(raw)} chars). Possible injection attempt."
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BiasValidationError(f"Response is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise BiasValidationError(f"Expected JSON object, got {type(data).__name__}.")

    # ── direction ──────────────────────────────────────────────────────────
    direction = data.get("direction")
    if direction not in ("bullish", "bearish", "neutral"):
        raise BiasValidationError(
            f"Invalid direction: {direction!r}. Must be bullish, bearish, or neutral."
        )

    # ── confidence ────────────────────────────────────────────────────────
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)):
        raise BiasValidationError(
            f"Confidence must be a number, got {type(confidence).__name__}."
        )
    confidence = float(confidence)
    if not (0.0 <= confidence <= 1.0):
        raise BiasValidationError(
            f"Confidence {confidence} out of range [0.0, 1.0]."
        )

    # ── reasoning (sanitize free text) ────────────────────────────────────
    reasoning = data.get("reasoning", "")
    if not isinstance(reasoning, str):
        raise BiasValidationError("Reasoning must be a string.")

    reasoning = reasoning.strip()[:_MAX_REASONING_LEN]

    if reasoning and not _SAFE_TEXT_PATTERN.match(reasoning):
        logger.warning(
            "Reasoning field contains unusual characters — stripping to alphanumeric."
        )
        reasoning = re.sub(r'[^\w\s.,;:!?()\-]', '', reasoning)[:_MAX_REASONING_LEN]

    # ── unexpected keys (log but don't fail — schema may evolve) ──────────
    allowed_keys = {"direction", "confidence", "reasoning"}
    extra_keys = set(data.keys()) - allowed_keys
    if extra_keys:
        logger.warning("Unexpected keys in Claude response (ignored): %s", extra_keys)

    bias = TradingBias(
        direction=direction,
        confidence=confidence,
        reasoning=reasoning,
        raw_response=raw,
    )

    logger.info(
        "Bias validated: direction=%s confidence=%.2f actionable=%s",
        bias.direction, bias.confidence, bias.is_actionable,
    )
    return bias


def build_bias_prompt(headlines: list[str]) -> str:
    """
    Build the prompt sent to Claude, safely embedding news headlines.

    Headlines are treated as untrusted content and embedded inside a
    clearly delimited block to resist prompt injection.
    """
    if not headlines:
        raise ValueError("No headlines provided.")

    # Sanitize: strip leading/trailing whitespace, drop empty strings
    clean_headlines = [h.strip() for h in headlines if h and h.strip()]
    if not clean_headlines:
        raise ValueError("All headlines were empty after sanitization.")

    # Limit number of headlines to prevent token stuffing
    MAX_HEADLINES = 20
    if len(clean_headlines) > MAX_HEADLINES:
        logger.warning(
            "Truncating %d headlines to %d.", len(clean_headlines), MAX_HEADLINES
        )
        clean_headlines = clean_headlines[:MAX_HEADLINES]

    headline_block = "\n".join(f"- {h}" for h in clean_headlines)

    return f"""\
You are a structured trading signal generator. Analyze the following overnight
market headlines and return ONLY a JSON object with exactly these fields:

{{
  "direction": "bullish" | "bearish" | "neutral",
  "confidence": <float between 0.0 and 1.0>,
  "reasoning": "<one concise sentence>"
}}

Do not include any text outside the JSON object. Do not add extra fields.

<headlines>
{headline_block}
</headlines>
"""
