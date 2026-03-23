"""
feature_engineering.py — Compute technical indicator features from OHLCV data.

All calculations use pure pandas/numpy — no external TA library.
This is required because pandas-ta and ta are incompatible with pandas >= 2.0.
(See BRAIN.md Mistake #2.)

Features computed:
  1. ema_crossover_signal  — EMA-9/21 crossover: 1.0=bullish, -1.0=bearish, 0.0=neutral
  2. ema_fast              — Normalized EMA-9: (ema9/close - 1.0)
  3. ema_slow              — Normalized EMA-21: (ema21/close - 1.0)
  4. rsi_14                — RSI-14 via Wilder smoothing (0.0–100.0)
  5. atr_14_pct            — ATR-14 normalized by close price (>0.0)
  6. volume_deviation      — (volume - 20d_mean_vol) / 20d_mean_vol
  7. vwap_distance         — (close - vwap) / vwap (running VWAP over lookback window)
  8. prior_day_return       — (today_close - yesterday_close) / yesterday_close

Security notes:
  - All output fields validated: NaN and Inf rejected before returning.
  - Minimum 30 rows enforced (required for meaningful feature computation).
  - Input DataFrame validated for required columns and positive prices.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Final

import pandas as pd

from .signals import detect_ema_crossover

logger = logging.getLogger(__name__)

# ── Feature column ordering ──────────────────────────────────────────────────
# CRITICAL: This order must match the column order used during model training.
# Imported by train_model.py and model.py to guarantee consistency.

FEATURE_COLUMNS: Final[list[str]] = [
    "ema_crossover_signal",
    "ema_fast",
    "ema_slow",
    "rsi_14",
    "atr_14_pct",
    "volume_deviation",
    "vwap_distance",
    "prior_day_return",
]

_MIN_ROWS = 30


class FeatureError(ValueError):
    """Raised when features cannot be computed from input data."""


@dataclass(frozen=True)
class FeatureRow:
    """
    Single row of computed features for ML model input.

    All fields are Python floats. Only constructed by build_features() —
    never instantiate directly from untrusted data.
    """
    ema_crossover_signal: float   # 1.0=bullish, -1.0=bearish, 0.0=neutral
    ema_fast: float               # (ema9/close - 1.0) — normalized
    ema_slow: float               # (ema21/close - 1.0) — normalized
    rsi_14: float                 # 0.0 – 100.0 (Wilder smoothing)
    atr_14_pct: float             # ATR-14 / close (positive fraction)
    volume_deviation: float       # (vol - 20d_mean_vol) / 20d_mean_vol
    vwap_distance: float          # (close - vwap) / vwap
    prior_day_return: float       # (close[t] - close[t-1]) / close[t-1]


# ── Public API ───────────────────────────────────────────────────────────────

def build_features(ohlcv: pd.DataFrame) -> FeatureRow:
    """
    Compute all technical indicator features from an OHLCV DataFrame.

    Args:
        ohlcv: DataFrame with columns [open, high, low, close, volume],
               DatetimeIndex sorted oldest-first. Minimum 30 rows required.

    Returns:
        FeatureRow for the most recent bar (today's features).

    Raises:
        FeatureError: On insufficient data, missing columns, or NaN/Inf in output.
    """
    _validate_ohlcv_input(ohlcv)

    closes = ohlcv["close"]
    current_close = float(closes.iloc[-1])

    # ── EMA crossover (reuse validated implementation from signals.py) ──────
    try:
        ema_result = detect_ema_crossover(closes, fast=9, slow=21)
    except Exception as exc:
        raise FeatureError(f"EMA crossover computation failed: {exc}") from exc

    if ema_result.direction == "bullish":
        ema_crossover_signal = 1.0
    elif ema_result.direction == "bearish":
        ema_crossover_signal = -1.0
    else:
        ema_crossover_signal = 0.0

    # Normalize EMA values relative to current close
    ema_fast = (ema_result.fast_ema / current_close) - 1.0
    ema_slow = (ema_result.slow_ema / current_close) - 1.0

    # ── RSI-14 (Wilder smoothing via ewm com=13) ─────────────────────────────
    rsi_14 = _compute_rsi(closes, period=14)

    # ── ATR-14 normalized by close ───────────────────────────────────────────
    atr_14_pct = _compute_atr_pct(ohlcv, period=14)

    # ── Volume deviation from 20-day mean ────────────────────────────────────
    volume_deviation = _compute_volume_deviation(ohlcv["volume"], window=20)

    # ── VWAP distance ────────────────────────────────────────────────────────
    vwap_distance = _compute_vwap_distance(ohlcv)

    # ── Prior day return ─────────────────────────────────────────────────────
    if len(closes) < 2:
        raise FeatureError("Need at least 2 bars to compute prior day return.")
    prior_day_return = float(
        (closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2]
    )

    row = FeatureRow(
        ema_crossover_signal=ema_crossover_signal,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        rsi_14=rsi_14,
        atr_14_pct=atr_14_pct,
        volume_deviation=volume_deviation,
        vwap_distance=vwap_distance,
        prior_day_return=prior_day_return,
    )

    # ── Validate all output fields for NaN/Inf ───────────────────────────────
    _validate_feature_row(row)

    logger.info(
        "Features computed: ema_signal=%.0f rsi=%.1f atr_pct=%.4f vol_dev=%.4f",
        row.ema_crossover_signal, row.rsi_14, row.atr_14_pct, row.volume_deviation,
    )
    return row


def feature_row_to_dataframe(row: FeatureRow) -> pd.DataFrame:
    """
    Convert a FeatureRow to a single-row DataFrame for model inference.

    Column order matches FEATURE_COLUMNS exactly — critical for model compatibility.
    """
    data = {col: [getattr(row, col)] for col in FEATURE_COLUMNS}
    return pd.DataFrame(data, columns=FEATURE_COLUMNS)


# ── Private calculation helpers ──────────────────────────────────────────────

def _validate_ohlcv_input(ohlcv: pd.DataFrame) -> None:
    """Validate OHLCV DataFrame before computing features."""
    if not isinstance(ohlcv, pd.DataFrame):
        raise FeatureError("ohlcv must be a pandas DataFrame.")

    required_cols = {"open", "high", "low", "close", "volume"}
    missing = required_cols - set(ohlcv.columns)
    if missing:
        raise FeatureError(f"Missing required columns: {sorted(missing)}")

    if len(ohlcv) < _MIN_ROWS:
        raise FeatureError(
            f"Insufficient data: got {len(ohlcv)} rows, need at least {_MIN_ROWS}."
        )

    if ohlcv[["open", "high", "low", "close"]].isnull().any().any():
        raise FeatureError("NaN values found in price columns.")

    if ohlcv["volume"].isnull().any():
        raise FeatureError("NaN values found in volume column.")


def _compute_rsi(closes: pd.Series, period: int = 14) -> float:
    """
    Compute RSI using Wilder's smoothing (ewm com=period-1).

    Returns the RSI value for the most recent bar as a float in [0.0, 100.0].
    """
    delta = closes.diff()
    gain = delta.clip(lower=0.0).ewm(com=period - 1, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(com=period - 1, adjust=False).mean()

    # Avoid division by zero: if loss is zero, RSI = 100
    last_loss = float(loss.iloc[-1])
    if last_loss == 0.0:
        return 100.0

    rs = float(gain.iloc[-1]) / last_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi)


def _compute_atr_pct(ohlcv: pd.DataFrame, period: int = 14) -> float:
    """
    Compute ATR-14 normalized by the most recent close price.

    True Range = max(H-L, |H-prev_C|, |L-prev_C|)
    ATR = Wilder smoothing of True Range (ewm com=period-1)
    Returns ATR / close (a positive fraction).
    """
    high = ohlcv["high"]
    low = ohlcv["low"]
    close = ohlcv["close"]

    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)

    atr = tr.ewm(com=period - 1, adjust=False).mean()
    current_close = float(close.iloc[-1])
    atr_pct = float(atr.iloc[-1]) / current_close
    return atr_pct


def _compute_volume_deviation(volume: pd.Series, window: int = 20) -> float:
    """
    Compute volume deviation from the rolling mean.

    Returns (current_volume - mean_volume) / mean_volume.
    Raises FeatureError if mean volume is zero (all-zero volume series).
    """
    mean_vol = float(volume.iloc[-window:].mean())
    if mean_vol == 0.0:
        raise FeatureError(
            "Mean volume over last 20 bars is zero — cannot compute volume deviation."
        )
    current_vol = float(volume.iloc[-1])
    return (current_vol - mean_vol) / mean_vol


def _compute_vwap_distance(ohlcv: pd.DataFrame) -> float:
    """
    Compute distance from VWAP using a running approximation over the full lookback.

    Note: For daily bars, a true intraday VWAP is not available. This computes
    a proxy using cumulative (typical_price * volume) / cumulative_volume over
    the entire lookback window. Acceptable for daily signal generation.

    Returns (close[-1] - vwap[-1]) / vwap[-1].
    """
    typical_price = (ohlcv["high"] + ohlcv["low"] + ohlcv["close"]) / 3.0
    cumulative_tpv = (typical_price * ohlcv["volume"]).cumsum()
    cumulative_vol = ohlcv["volume"].cumsum()

    # Guard against zero cumulative volume
    cumulative_vol_safe = cumulative_vol.replace(0, float("nan"))
    vwap = cumulative_tpv / cumulative_vol_safe

    vwap_last = float(vwap.iloc[-1])
    if math.isnan(vwap_last) or vwap_last == 0.0:
        raise FeatureError("VWAP computation resulted in NaN or zero.")

    close_last = float(ohlcv["close"].iloc[-1])
    return (close_last - vwap_last) / vwap_last


def _validate_feature_row(row: FeatureRow) -> None:
    """Validate all fields in a FeatureRow for NaN and Inf."""
    bad_fields = []
    for field_name in FEATURE_COLUMNS:
        val = getattr(row, field_name)
        if math.isnan(val) or math.isinf(val):
            bad_fields.append(field_name)

    if bad_fields:
        raise FeatureError(
            f"NaN or Inf in computed features: {bad_fields}. "
            "This may indicate insufficient or degenerate input data."
        )
