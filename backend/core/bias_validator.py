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


def build_review_prompt(
    ticker: str,
    ml_probability: float,
    mc_hit_rate: float,
    headlines: list[str],
    signal_direction: str,
) -> str:
    """
    Build the veto prompt sent to Claude for contextual review of an ML signal.

    Claude is explicitly told it is a veto, not the primary signal generator.
    The prompt biases toward approval — veto only on specific, articulable events.

    Headlines are sanitized and embedded in a clearly delimited block to resist
    prompt injection (same pattern as build_bias_prompt).
    """
    # Sanitize headlines (reuse same pattern as below)
    clean_headlines = [h.strip() for h in headlines if h and h.strip()]
    clean_headlines = clean_headlines[:20]  # cap to prevent token stuffing

    if clean_headlines:
        headline_block = "\n".join(f"- {h}" for h in clean_headlines)
    else:
        headline_block = "(No recent headlines available.)"

    return f"""\
You are a trading risk reviewer. An ML model and Monte Carlo simulation have
generated a trading signal. Your ONLY role is to veto if you see a clear,
specific news event that directly contradicts this signal. Default to approving.

Signal summary:
- Ticker: {ticker}
- Direction: {signal_direction}
- ML bullish probability: {ml_probability:.1%}
- Monte Carlo hit-target rate: {mc_hit_rate:.1%}

Recent news context:
<headlines>
{headline_block}
</headlines>

Return ONLY a JSON object with exactly these two fields:
{{"approve": true | false, "reason": "<one sentence>"}}

Approve UNLESS there is a clear, specific event that directly invalidates a
technical signal (e.g. earnings miss, Fed announcement, regulatory action,
force majeure). Do NOT veto based on general uncertainty or market conditions.
Do not include any text outside the JSON object.
"""


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


# ── Claude Review Result (ML pipeline) ──────────────────────────────────────

@dataclass(frozen=True)
class ClaudeReviewResult:
    """
    Validated Claude veto decision for an ML-generated signal.

    Only constructed by parse_claude_review() — never instantiate directly
    from untrusted data.
    """
    approve: bool
    reason: str        # sanitized, truncated
    raw_response: str  # original JSON string for audit logging


class ClaudeReviewError(ValueError):
    """Raised when Claude's review response cannot be validated."""


def parse_claude_review(raw: str) -> ClaudeReviewResult:
    """
    Parse and validate Claude's review decision for an ML-generated signal.

    Expected JSON format:
    {
        "approve": true | false,
        "reason": "..."
    }

    Security:
        - "approve" must be a JSON boolean (true/false). Rejects int 1, string "true",
          etc. — prevents truthy-bypass attacks.
        - "reason" sanitized with the same pattern as TradingBias.reasoning.
        - Response size capped at 5,000 chars.

    Raises:
        ClaudeReviewError on any validation failure. NEVER returns a
        ClaudeReviewResult with invalid data.
    """
    if not raw or not isinstance(raw, str):
        raise ClaudeReviewError("Empty or non-string response from Claude API.")

    if len(raw) > 5_000:
        raise ClaudeReviewError(
            f"Review response suspiciously large ({len(raw)} chars). "
            "Possible injection attempt."
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeReviewError(f"Review response is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ClaudeReviewError(f"Expected JSON object, got {type(data).__name__}.")

    # ── approve — MUST be a strict JSON boolean ────────────────────────────
    approve = data.get("approve")
    if not isinstance(approve, bool):
        raise ClaudeReviewError(
            f"'approve' must be a JSON boolean (true/false), "
            f"got {type(approve).__name__}: {approve!r}. "
            "Integer 1 and string 'true' are not accepted."
        )

    # ── reason (sanitize free text — same pattern as TradingBias.reasoning) ─
    reason = data.get("reason", "")
    if not isinstance(reason, str):
        raise ClaudeReviewError("'reason' must be a string.")

    reason = reason.strip()[:_MAX_REASONING_LEN]

    if reason and not _SAFE_TEXT_PATTERN.match(reason):
        logger.warning("Review reason contains unusual characters — stripping.")
        reason = re.sub(r'[^\w\s.,;:!?()\-]', '', reason)[:_MAX_REASONING_LEN]

    # ── Unexpected keys (log but don't fail) ──────────────────────────────
    allowed_keys = {"approve", "reason"}
    extra_keys = set(data.keys()) - allowed_keys
    if extra_keys:
        logger.warning("Unexpected keys in Claude review (ignored): %s", extra_keys)

    result = ClaudeReviewResult(approve=approve, reason=reason, raw_response=raw)
    logger.info("Claude review: approve=%s reason=%r", result.approve, result.reason[:80])
    return result
