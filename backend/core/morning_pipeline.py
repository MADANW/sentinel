"""
morning_pipeline.py — Morning bias pipeline: headlines → Claude → TradingBias.

Orchestrates the full Claude API call, response validation, and actionability
check. This is the entry point for each trading session's directional decision.

Security notes:
  - Headlines are embedded in the prompt via build_bias_prompt() which sanitizes
    and delimits the input to resist prompt injection.
  - Claude's response is never used raw — it must pass parse_bias_response().
  - If validation fails, the function returns None (no trade) rather than raising.
"""

from __future__ import annotations

import logging
import os

import anthropic

from .bias_validator import (
    BiasValidationError,
    TradingBias,
    build_bias_prompt,
    parse_bias_response,
)

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 512


class PipelineError(RuntimeError):
    """Raised when the pipeline cannot run due to configuration or API failure."""


def run_morning_pipeline(headlines: list[str]) -> TradingBias | None:
    """
    Full morning bias pipeline: headlines → Claude → validate → TradingBias.

    Args:
        headlines: List of overnight news headlines. Must be non-empty.

    Returns:
        A validated, actionable TradingBias, or None if:
          - Claude returns a neutral direction
          - Confidence is below MIN_CONFIDENCE_TO_TRADE
          - Claude's response fails validation (logged as an error)

    Raises:
        PipelineError: On missing API key or Claude API network/auth errors.
        ValueError:    If headlines list is empty or all blank (from build_bias_prompt).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise PipelineError(
            "ANTHROPIC_API_KEY is not set. Cannot call Claude API."
        )

    prompt = build_bias_prompt(headlines)

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIConnectionError as exc:
        raise PipelineError(f"Claude API connection error: {exc}") from exc
    except anthropic.AuthenticationError as exc:
        raise PipelineError(f"Claude API authentication failed: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise PipelineError(
            f"Claude API error {exc.status_code}: {exc.message}"
        ) from exc

    raw = response.content[0].text
    logger.debug("Claude raw response (first 200 chars): %s", raw[:200])

    try:
        bias = parse_bias_response(raw)
    except BiasValidationError as exc:
        logger.error("Claude response failed validation — skipping trade: %s", exc)
        return None

    if not bias.is_actionable:
        logger.info(
            "Bias not actionable (direction=%s confidence=%.0f%%) — skipping today.",
            bias.direction, bias.confidence * 100,
        )
        return None

    logger.info(
        "Morning pipeline complete: %s at %.0f%% confidence.",
        bias.direction, bias.confidence * 100,
    )
    return bias
