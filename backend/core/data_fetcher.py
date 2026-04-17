"""
data_fetcher.py — Fetch OHLCV and news headlines from Alpaca APIs.

Security notes:
  - All price data validated before returning: positive prices, no NaN,
    minimum row count, plausible close range.
  - Headlines sanitized: HTML stripped, truncated to 300 chars,
    non-printable characters removed, allowlist regex applied.
  - API errors on headlines return an empty list (headlines are optional
    context for Claude review, not a hard gate).
  - API credentials always read from environment variables, never hardcoded.
  - Transient API errors retried with exponential backoff (max 3 attempts).
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

logger = logging.getLogger(__name__)

# Characters allowed in sanitized headline text — mirrors bias_validator._SAFE_TEXT_PATTERN
# Defined locally to avoid coupling to a private name in another module
_HEADLINE_SAFE_PATTERN = re.compile(r'^[\w\s.,;:!?()\-\'\"\/\+\%\$\#\@\&\*\[\]]+$')
_HEADLINE_MAX_LEN = 300
_HEADLINE_MAX_COUNT = 50

_MIN_OHLCV_ROWS = 30
_CLOSE_MIN = 0.01
_CLOSE_MAX = 1_000_000.0

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds


class DataFetcherError(ValueError):
    """Raised when data cannot be fetched or fails validation."""


# ── Internal retry helper ────────────────────────────────────────────────────

def _with_retries(fn, *args, **kwargs):
    """Execute fn with exponential backoff on transient errors.

    Retries up to _MAX_RETRIES times. Raises the last exception on exhaustion.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = _BACKOFF_BASE ** attempt
            logger.warning(
                "API attempt %d/%d failed (%s), retrying in %.1fs",
                attempt + 1, _MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
    raise last_exc  # unreachable, but satisfies type checkers


# ── OHLCV fetching ───────────────────────────────────────────────────────────

def fetch_ohlcv(ticker: str, days: int = 60) -> pd.DataFrame:
    """
    Fetch OHLCV daily bars from Alpaca historical data API.

    Args:
        ticker: Equity ticker symbol, e.g. "SPY".
        days:   Number of calendar days to look back. Default 60.

    Returns:
        DataFrame with columns [open, high, low, close, volume],
        DatetimeIndex (UTC), sorted oldest-first.
        Guaranteed to have at least _MIN_OHLCV_ROWS (30) rows.

    Raises:
        DataFetcherError on missing credentials, API errors, or validation failure.
    """
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise DataFetcherError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")

    end = datetime.now(timezone.utc)
    # Add buffer for weekends/holidays — request more calendar days than trading days
    start = end - timedelta(days=max(days * 2, 90))

    # Default to IEX feed — works on free-tier Alpaca subscriptions.
    # SIP (consolidated tape) requires a paid data plan; override via ALPACA_DATA_FEED=sip if available.
    feed_name = os.environ.get("ALPACA_DATA_FEED", "iex").lower()
    feed = {"iex": DataFeed.IEX, "sip": DataFeed.SIP, "otc": DataFeed.OTC}.get(
        feed_name, DataFeed.IEX
    )

    client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=ticker.upper(),
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        limit=days,
        feed=feed,
    )

    try:
        bars = _with_retries(client.get_stock_bars, request)
    except Exception as exc:
        raise DataFetcherError(
            f"Alpaca API error fetching daily bars for {ticker}: {exc}"
        ) from exc

    df = bars.df
    if df is None or df.empty:
        raise DataFetcherError(f"No bars returned for {ticker}.")

    # bars.df may carry a MultiIndex (symbol, timestamp) — drop the symbol level
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel(0)

    df = df.sort_index()

    # Normalize column names to lowercase
    df.columns = [c.lower() for c in df.columns]

    # Keep only the required columns
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataFetcherError(f"Missing columns in Alpaca response: {missing}")
    df = df[required]

    # ── Validation ─────────────────────────────────────────────────────────
    _validate_ohlcv(df, ticker)

    logger.info("Fetched %d OHLCV bars for %s", len(df), ticker)
    return df


def _validate_ohlcv(df: pd.DataFrame, ticker: str) -> None:
    """Validate OHLCV DataFrame. Raises DataFetcherError on any violation."""
    if len(df) < _MIN_OHLCV_ROWS:
        raise DataFetcherError(
            f"Insufficient data for {ticker}: got {len(df)} rows, "
            f"need at least {_MIN_OHLCV_ROWS}."
        )

    if df.isnull().any().any():
        null_cols = df.columns[df.isnull().any()].tolist()
        raise DataFetcherError(
            f"NaN values in OHLCV data for {ticker}: columns {null_cols}"
        )

    price_cols = ["open", "high", "low", "close"]
    for col in price_cols:
        if (df[col] <= 0).any():
            raise DataFetcherError(
                f"Non-positive price in column '{col}' for {ticker}."
            )

    if (df["volume"] < 0).any():
        raise DataFetcherError(f"Negative volume values for {ticker}.")

    close_min = df["close"].min()
    close_max = df["close"].max()
    if close_min < _CLOSE_MIN or close_max > _CLOSE_MAX:
        raise DataFetcherError(
            f"Close price out of plausible range for {ticker}: "
            f"min={close_min}, max={close_max}. "
            f"Expected ({_CLOSE_MIN}, {_CLOSE_MAX})."
        )


# ── Headlines fetching ───────────────────────────────────────────────────────

def fetch_headlines(ticker: str, limit: int = 10) -> list[str]:
    """
    Fetch recent news headlines from Alpaca News API.

    Returns a list of sanitized headline strings from the last 24 hours.
    Returns an empty list (does NOT raise) on API errors — headlines are
    optional context for Claude review, not a hard gate.

    Sanitization applied to each headline:
      - HTML tags stripped
      - Non-printable characters removed
      - Truncated to _HEADLINE_MAX_LEN (300) characters
      - Empty strings after sanitization are dropped

    Args:
        ticker: Equity ticker symbol, e.g. "SPY".
        limit:  Maximum number of headlines to return. Capped at _HEADLINE_MAX_COUNT (50).

    Raises:
        DataFetcherError: Only on missing API credentials.
    """
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise DataFetcherError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")

    # Cap limit to prevent token stuffing
    effective_limit = min(limit, _HEADLINE_MAX_COUNT)

    try:
        headlines: list[str] = _with_retries(
            _fetch_headlines_from_api, ticker, effective_limit, api_key, secret_key
        )
    except Exception as exc:
        logger.warning("Failed to fetch headlines for %s: %s — returning empty list.", ticker, exc)
        return []

    sanitized = [_sanitize_headline(h) for h in headlines]
    result = [h for h in sanitized if h]  # drop empty after sanitization

    logger.info("Fetched %d headlines for %s", len(result), ticker)
    return result


def _fetch_headlines_from_api(
    ticker: str, limit: int, api_key: str, secret_key: str
) -> list[str]:
    """Raw Alpaca News API call. Returns list of raw headline strings."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)

    client = NewsClient(api_key=api_key, secret_key=secret_key)
    request = NewsRequest(
        symbols=ticker.upper(),
        start=start,
        end=end,
        limit=limit,
    )

    news = client.get_news(request)

    # Extract only the headline field — never pass body, URL, or author upstream
    headlines = []
    news_items = news.news if hasattr(news, "news") else []
    for article in news_items:
        headline = getattr(article, "headline", None)
        if headline and isinstance(headline, str):
            headlines.append(headline)

    return headlines


def _sanitize_headline(raw: str) -> str:
    """
    Sanitize a single headline string.

    Applies:
      1. Strip HTML tags
      2. Remove non-printable characters
      3. Truncate to _HEADLINE_MAX_LEN
      4. Apply allowlist regex (strip remaining unsafe chars)
      5. Strip whitespace
    """
    if not isinstance(raw, str):
        return ""

    # 1. Strip HTML tags
    text = re.sub(r'<[^>]+>', '', raw)

    # 2. Remove non-printable characters (keep printable ASCII and common unicode)
    text = ''.join(c for c in text if c.isprintable())

    # 3. Truncate
    text = text[:_HEADLINE_MAX_LEN]

    # 4. Apply allowlist — strip chars outside the safe pattern
    if text and not _HEADLINE_SAFE_PATTERN.match(text):
        text = re.sub(r'[^\w\s.,;:!?()\-\'\"\/\+\%\$\#\@\&\*\[\]]', '', text)

    # 5. Strip whitespace
    return text.strip()
