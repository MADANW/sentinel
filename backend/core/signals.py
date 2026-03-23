"""
signals.py — EMA crossover signal detection.

Uses pandas ewm() for EMA calculation — no external TA library needed.
pandas is already a core dependency.

EMA crossover rules:
  - Bullish: fast EMA crosses ABOVE slow EMA on the most recent bar
  - Bearish: fast EMA crosses BELOW slow EMA on the most recent bar
  - Neutral: no crossover detected
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

import pandas as pd

logger = logging.getLogger(__name__)

Direction = Literal["bullish", "bearish", "neutral"]


@dataclass(frozen=True)
class SignalResult:
    direction: Direction
    fast_ema: float
    slow_ema: float
    crossover_detected: bool


class SignalError(ValueError):
    """Raised when an EMA signal cannot be computed."""


def detect_ema_crossover(
    prices: pd.Series,
    fast: int = 9,
    slow: int = 21,
) -> SignalResult:
    """
    Detect EMA crossover signal from a closing price series.

    A crossover is detected by comparing the sign of (fast_ema - slow_ema)
    between the last two bars. Sign change → crossover.

    Args:
        prices: Closing prices as a pandas Series, oldest first.
        fast:   Fast EMA period. Default 9.
        slow:   Slow EMA period. Default 21.

    Returns:
        SignalResult with direction, current EMA values, and crossover flag.

    Raises:
        SignalError if inputs are invalid or the series is too short.
    """
    if not isinstance(prices, pd.Series):
        raise SignalError("prices must be a pandas Series.")
    if fast >= slow:
        raise SignalError(
            f"fast period ({fast}) must be less than slow period ({slow})."
        )
    if len(prices) < slow + 1:
        raise SignalError(
            f"Need at least {slow + 1} data points to detect a crossover "
            f"(got {len(prices)})."
        )
    if prices.isnull().any():
        raise SignalError("Price series contains NaN values.")
    if (prices <= 0).any():
        raise SignalError("Price series contains non-positive values.")

    fast_ema = prices.ewm(span=fast, adjust=False).mean()
    slow_ema = prices.ewm(span=slow, adjust=False).mean()

    curr_diff = fast_ema.iloc[-1] - slow_ema.iloc[-1]
    prev_diff = fast_ema.iloc[-2] - slow_ema.iloc[-2]

    # Crossover = sign change between consecutive bars.
    # Explicit bool() cast: pandas comparisons return numpy.bool_, not Python bool.
    crossover_detected = bool((curr_diff > 0) != (prev_diff > 0))

    if crossover_detected and curr_diff > 0:
        direction: Direction = "bullish"
    elif crossover_detected and curr_diff < 0:
        direction = "bearish"
    else:
        direction = "neutral"

    result = SignalResult(
        direction=direction,
        fast_ema=round(float(fast_ema.iloc[-1]), 4),
        slow_ema=round(float(slow_ema.iloc[-1]), 4),
        crossover_detected=crossover_detected,
    )

    logger.info(
        "EMA signal: direction=%s fast=%.4f slow=%.4f crossover=%s",
        result.direction, result.fast_ema, result.slow_ema, result.crossover_detected,
    )
    return result


def fetch_ohlcv(symbol: str, timeframe: str = "1Day", limit: int = 50) -> pd.DataFrame:
    """
    Fetch OHLCV bars from Alpaca historical data API.

    Returns a DataFrame with columns: open, high, low, close, volume.
    Sorted oldest-first. Requires ALPACA_API_KEY and ALPACA_SECRET_KEY env vars.

    Args:
        symbol:    Ticker symbol, e.g. "SPY".
        timeframe: Bar timeframe. Supported: "1Day", "1Hour", "15Min", "5Min".
        limit:     Number of bars to fetch. Default 50.

    Raises:
        SignalError on missing credentials, unsupported timeframe, or API errors.
    """
    from datetime import datetime, timedelta, timezone

    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise SignalError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")

    _timeframe_map = {
        "1Day": TimeFrame.Day,
        "1Hour": TimeFrame.Hour,
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    }
    tf = _timeframe_map.get(timeframe)
    if tf is None:
        raise SignalError(
            f"Unsupported timeframe: {timeframe!r}. "
            f"Supported: {list(_timeframe_map)}."
        )

    end = datetime.now(timezone.utc)
    # Request enough calendar days to cover `limit` trading bars
    start = end - timedelta(days=limit * 2)

    client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=symbol.upper(),
        timeframe=tf,
        start=start,
        end=end,
        limit=limit,
    )

    try:
        bars = client.get_stock_bars(request)
    except Exception as exc:
        raise SignalError(
            f"Alpaca API error fetching {timeframe} bars for {symbol}: {exc}"
        ) from exc

    df = bars.df
    if df is None or df.empty:
        raise SignalError(f"No bars returned for {symbol}.")

    # bars.df may carry a MultiIndex (symbol, timestamp) — drop the symbol level
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel(0)

    return df.sort_index()
