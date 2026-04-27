"""
morning_pipeline.py — ML-first morning trading pipeline.

Architecture:
  fetch_ohlcv() → build_features() → predict_direction() → simulate()
  → [ML >= 0.6 AND MC hit_rate >= 0.55] → claude_review() → TradingBias | None

Security notes:
  - All external data validated before use (prices, features, ML output).
  - Claude is a veto gate only — it cannot generate a trade signal.
  - All Claude output must pass parse_claude_review() before use.
  - Returns None (no trade) on any gate failure — never raises on gate logic.
  - Only raises PipelineError on configuration/credential failures.
  - Ticker input validated: 1-5 uppercase ASCII letters only.
"""

from __future__ import annotations

import logging
import math
import os
import re as _re

import anthropic

from .bias_validator import (
    ClaudeReviewError,
    ClaudeReviewResult,
    TradingBias,
    build_review_prompt,
    parse_claude_review,
)
from .data_fetcher import DataFetcherError, fetch_headlines, fetch_ohlcv
from .feature_engineering import FeatureError, build_features, feature_row_to_dataframe
from .model import ModelError, ModelTamperingError, predict_direction
from .monte_carlo import MonteCarloError, SimulationResult, simulate

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 256  # review response is short: {"approve": bool, "reason": "..."}

# Gate thresholds
_ML_BULLISH_THRESHOLD: float = 0.60
_ML_BEARISH_THRESHOLD: float = 0.40
_MC_HIT_RATE_THRESHOLD: float = 0.55

# Ticker validation: 1-5 uppercase ASCII letters
_TICKER_PATTERN = _re.compile(r'^[A-Z]{1,5}$')


class PipelineError(RuntimeError):
    """Raised when the pipeline cannot run due to configuration or API failure."""


# ── Ticker validation ─────────────────────────────────────────────────────────

def _validate_ticker(ticker: str) -> str:
    """
    Normalize and validate a ticker symbol.

    Args:
        ticker: Raw ticker input (e.g. "spy" or "SPY").

    Returns:
        Normalized uppercase ticker.

    Raises:
        ValueError: If ticker is not 1-5 uppercase ASCII letters after normalization.
    """
    if not isinstance(ticker, str):
        raise ValueError(f"ticker must be a string, got {type(ticker).__name__}")
    normalized = ticker.strip().upper()
    if not _TICKER_PATTERN.match(normalized):
        raise ValueError(
            f"Invalid ticker {ticker!r}. Must be 1-5 uppercase ASCII letters "
            f"(e.g. 'SPY', 'AAPL'). Got: {normalized!r}"
        )
    return normalized


# ── Configuration check ───────────────────────────────────────────────────────

def _check_required_env_vars() -> None:
    """Raise PipelineError if any required API keys are missing."""
    required = {
        "ANTHROPIC_API_KEY": "Claude API",
        "ALPACA_API_KEY": "Alpaca API",
        "ALPACA_SECRET_KEY": "Alpaca API",
    }
    missing = [f"{var} ({desc})" for var, desc in required.items()
               if not os.environ.get(var)]
    if missing:
        raise PipelineError(
            f"Missing required environment variables: {', '.join(missing)}"
        )


# ── Monte Carlo helper ────────────────────────────────────────────────────────

def _run_monte_carlo(ohlcv, direction: str) -> SimulationResult:
    """
    Compute Monte Carlo simulation inputs from OHLCV data.

    Derives annualized volatility and drift from recent price history,
    then calls simulate() with a 2:1 reward:risk structure.
    """
    returns = ohlcv["close"].pct_change().dropna()

    if len(returns) < 14:
        raise MonteCarloError(
            f"Insufficient return history for Monte Carlo: got {len(returns)} bars."
        )

    daily_vol = float(returns.std())
    annualized_vol = daily_vol * math.sqrt(252)

    daily_drift = float(returns.mean())
    annualized_drift = daily_drift * 252

    current_price = float(ohlcv["close"].iloc[-1])

    # ATR-based stop distance: mean absolute return over last 14 bars × 1.5
    atr_pct = float(returns.abs().tail(14).mean())
    stop_loss_pct = max(atr_pct * 1.5, 0.005)   # floor at 0.5%
    take_profit_pct = stop_loss_pct * 2.0         # 2:1 R:R

    # For bearish direction, negate the drift to model the short perspective
    effective_drift = annualized_drift if direction == "bullish" else -annualized_drift

    return simulate(
        current_price=current_price,
        volatility=annualized_vol,
        drift=effective_drift,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        n_paths=1_000,
        horizon_minutes=390,
    )


# ── Claude review helper ──────────────────────────────────────────────────────

def _call_claude_review(
    ticker: str,
    ml_probability: float,
    mc_result: SimulationResult,
    headlines: list[str],
    direction: str,
) -> ClaudeReviewResult:
    """
    Call Claude for a contextual veto review of the ML + MC signal.

    Raises:
        PipelineError: On missing API key or Claude API errors.
        ClaudeReviewError: On invalid Claude response (caller handles this).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise PipelineError("ANTHROPIC_API_KEY is not set. Cannot call Claude API.")

    prompt = build_review_prompt(
        ticker=ticker,
        ml_probability=ml_probability,
        mc_hit_rate=mc_result.hit_target_rate,
        headlines=headlines,
        signal_direction=direction,
    )

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
    logger.debug("Claude review raw response (first 200 chars): %s", raw[:200])

    return parse_claude_review(raw)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_morning_pipeline(ticker: str) -> TradingBias | None:
    """
    Full ML-first morning trading pipeline.

    Args:
        ticker: Equity ticker symbol, e.g. "SPY". Case-insensitive; normalized to uppercase.

    Returns:
        A validated TradingBias if all gates pass (ML, Monte Carlo, Claude review).
        None if any gate fails — log entries explain the reason.

    Raises:
        PipelineError: On missing API keys or Claude API credential errors.
        ValueError: On invalid ticker format.
    """
    ticker = _validate_ticker(ticker)

    # ── Gate 0: Configuration check ─────────────────────────────────────────
    _check_required_env_vars()

    # ── Stage 1: Data fetching ───────────────────────────────────────────────
    try:
        ohlcv = fetch_ohlcv(ticker, days=150)
    except DataFetcherError as exc:
        logger.error("OHLCV fetch failed — no trade today: %s", exc)
        return None

    # Headlines are optional context — empty list is acceptable
    headlines = fetch_headlines(ticker, limit=10)

    # ── Stage 2: Feature engineering ────────────────────────────────────────
    try:
        feature_row = build_features(ohlcv)
        features_df = feature_row_to_dataframe(feature_row)
    except FeatureError as exc:
        logger.error("Feature engineering failed — no trade today: %s", exc)
        return None

    # ── Stage 3: ML gate ─────────────────────────────────────────────────────
    try:
        ml_probability = predict_direction(features_df)
    except ModelTamperingError as exc:
        logger.critical("MODEL TAMPERING DETECTED — no trade today: %s", exc)
        return None
    except ModelError as exc:
        logger.error("Model error — no trade today: %s", exc)
        return None

    if ml_probability >= _ML_BULLISH_THRESHOLD:
        direction = "bullish"
    elif ml_probability <= _ML_BEARISH_THRESHOLD:
        direction = "bearish"
    else:
        logger.info(
            "ML gate: probability %.4f in dead zone [%.2f, %.2f] — no trade today.",
            ml_probability, _ML_BEARISH_THRESHOLD, _ML_BULLISH_THRESHOLD,
        )
        return None

    logger.info(
        "ML gate PASSED: direction=%s ml_probability=%.4f", direction, ml_probability
    )

    # ── Stage 4: Monte Carlo gate ────────────────────────────────────────────
    try:
        mc_result = _run_monte_carlo(ohlcv, direction)
    except MonteCarloError as exc:
        logger.error("Monte Carlo simulation failed — no trade today: %s", exc)
        return None

    if mc_result.hit_target_rate < _MC_HIT_RATE_THRESHOLD:
        logger.info(
            "MC gate FAILED: hit_target_rate=%.4f < %.2f — no trade today.",
            mc_result.hit_target_rate, _MC_HIT_RATE_THRESHOLD,
        )
        return None

    logger.info(
        "MC gate PASSED: hit_target_rate=%.4f expected_pnl=%.3f%%",
        mc_result.hit_target_rate, mc_result.expected_pnl_pct * 100,
    )

    # ── Stage 5: Claude review gate ──────────────────────────────────────────
    try:
        review = _call_claude_review(ticker, ml_probability, mc_result, headlines, direction)
    except PipelineError:
        raise  # credential/config errors propagate
    except ClaudeReviewError as exc:
        logger.error("Claude review response invalid — no trade today: %s", exc)
        return None
    except Exception as exc:
        logger.error("Claude review failed unexpectedly — no trade today: %s", exc)
        return None

    if not review.approve:
        logger.info(
            "Claude review VETOED trade: reason=%r — no trade today.", review.reason
        )
        return None

    logger.info("Claude review APPROVED: %r", review.reason)

    # ── Stage 6: Construct TradingBias for downstream ────────────────────────
    bias = TradingBias(
        direction=direction,
        confidence=ml_probability,
        reasoning=review.reason,
        raw_response=review.raw_response,
    )

    logger.info(
        "Morning pipeline COMPLETE: direction=%s confidence=%.4f actionable=%s",
        bias.direction, bias.confidence, bias.is_actionable,
    )
    return bias
