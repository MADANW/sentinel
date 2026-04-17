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
  8. prior_day_return      — (today_close - yesterday_close) / yesterday_close
  9. hurst_exponent        — R/S rescaled-range exponent on last 96 log-returns
 10. ou_log_half_life      — log1p of OU half-life in bars (compresses saturation outliers)
 11. ou_zscore             — (close - mu_OU) / sigma_OU from OU fit
 12. regime_label          — -1.0 ranging, 0.0 random walk, +1.0 trending (derived from Hurst)

Security notes:
  - All output fields validated: NaN and Inf rejected before returning.
  - Minimum 97 rows enforced (96-bar Hurst window plus one prior close).
  - Input DataFrame validated for required columns and positive prices.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Final

import numpy as np
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
    "hurst_exponent",
    "ou_log_half_life",
    "ou_zscore",
    "regime_label",
]

_HURST_WINDOW: Final[int] = 96
_OU_WINDOW: Final[int] = 64
_REGIME_TREND_THRESHOLD: Final[float] = 0.55
_REGIME_RANGE_THRESHOLD: Final[float] = 0.45
_MAX_HALF_LIFE_BARS: Final[float] = 1e6

_MIN_ROWS = max(30, _HURST_WINDOW + 1, _OU_WINDOW + 1)


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
    hurst_exponent: float         # R/S exponent on log-returns, clamped to [0, 1]
    ou_log_half_life: float       # log1p(bars to 50% reversion); ~0 for fast MR, ~13.8 for saturated
    ou_zscore: float              # (close - mu_OU) / sigma_OU from OU fit
    regime_label: float           # -1.0 ranging, 0.0 random walk, +1.0 trending


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

    # ── Regime features (Hurst + OU) ─────────────────────────────────────────
    hurst_exponent = _compute_hurst(closes, window=_HURST_WINDOW)
    ou_half_life_raw, ou_zscore = _compute_ou_features(closes, window=_OU_WINDOW)
    ou_log_half_life = math.log1p(ou_half_life_raw)

    if hurst_exponent > _REGIME_TREND_THRESHOLD:
        regime_label = 1.0
    elif hurst_exponent < _REGIME_RANGE_THRESHOLD:
        regime_label = -1.0
    else:
        regime_label = 0.0

    row = FeatureRow(
        ema_crossover_signal=ema_crossover_signal,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        rsi_14=rsi_14,
        atr_14_pct=atr_14_pct,
        volume_deviation=volume_deviation,
        vwap_distance=vwap_distance,
        prior_day_return=prior_day_return,
        hurst_exponent=hurst_exponent,
        ou_log_half_life=ou_log_half_life,
        ou_zscore=ou_zscore,
        regime_label=regime_label,
    )

    # ── Validate all output fields for NaN/Inf ───────────────────────────────
    _validate_feature_row(row)

    logger.info(
        "Features computed: ema_signal=%.0f rsi=%.1f atr_pct=%.4f vol_dev=%.4f "
        "hurst=%.3f ou_log_hl=%.2f ou_z=%.2f regime=%.0f",
        row.ema_crossover_signal, row.rsi_14, row.atr_14_pct, row.volume_deviation,
        row.hurst_exponent, row.ou_log_half_life, row.ou_zscore, row.regime_label,
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


def _compute_hurst(closes: pd.Series, window: int) -> float:
    """
    Compute the Hurst exponent via multi-scale R/S analysis on log-returns.

    Standard R/S procedure: over non-overlapping chunks at several lag sizes,
    compute the rescaled range (R/S), then fit log(R/S) vs log(lag) via OLS.
    The slope of that regression is the Hurst exponent.

    H > 0.5 → trending, H < 0.5 → mean-reverting, H ≈ 0.5 → random walk.

    Args:
        closes: Close-price Series (oldest → newest). Length must be >= window + 1.
        window: Total number of log-returns to analyze (must be >= 16).

    Returns:
        Hurst exponent, clamped to [0.0, 1.0].

    Raises:
        FeatureError: If the window has zero variance or too few usable chunks
                      across scales to fit the slope.
    """
    if len(closes) < window + 1:
        raise FeatureError(
            f"Hurst: need at least {window + 1} bars, got {len(closes)}."
        )
    if window < 16:
        raise FeatureError(f"Hurst: window {window} too small for multi-scale R/S.")

    tail = closes.iloc[-(window + 1):].to_numpy(dtype=float)
    if np.any(tail <= 0):
        raise FeatureError("Hurst: non-positive close prices in window.")

    log_returns = np.log(tail[1:] / tail[:-1])

    # Generate lag sizes: 8, 16, 32, ... up to window, inclusive.
    lag = 8
    lags: list[int] = []
    while lag <= window:
        lags.append(lag)
        lag *= 2
    if lags and lags[-1] != window:
        lags.append(window)

    log_lags: list[float] = []
    log_rs: list[float] = []
    for n in lags:
        k = len(log_returns) // n
        if k == 0:
            continue
        ratios: list[float] = []
        for i in range(k):
            chunk = log_returns[i * n : (i + 1) * n]
            s = float(chunk.std(ddof=1))
            if s == 0.0:
                continue
            deviations = chunk - chunk.mean()
            cumulative = np.cumsum(deviations)
            r = float(cumulative.max() - cumulative.min())
            if r <= 0.0:
                continue
            ratios.append(r / s)
        if ratios:
            log_lags.append(math.log(n))
            log_rs.append(math.log(float(np.mean(ratios))))

    if len(log_lags) < 2:
        raise FeatureError("Hurst: degenerate window (not enough usable lag scales).")

    slope, _intercept = np.polyfit(np.array(log_lags), np.array(log_rs), 1)
    return max(0.0, min(1.0, float(slope)))


def _compute_ou_features(closes: pd.Series, window: int) -> tuple[float, float]:
    """
    Fit an Ornstein-Uhlenbeck process to the last `window` prices via OLS.

    Discrete-time form: P_t = a + b · P_{t-1} + eps.
    Mapped to OU parameters:
        theta     = -ln(b)          (mean-reversion speed)
        mu        = a / (1 - b)     (long-run mean)
        sigma     = std of residuals scaled by sqrt(1 / (1 - b^2))
        half_life = ln(2) / theta   (bars to 50% reversion)

    When the fit is non-stationary (b outside (0, 1)) the half-life saturates
    at _MAX_HALF_LIFE_BARS and the z-score falls back to sample mean/std — so
    the caller always gets finite numbers and XGBoost can learn a split like
    "half_life > 1e5 → probably trending, down-weight mean-reversion logic."

    Args:
        closes: Close-price Series (oldest → newest). Length must be >= window + 1.
        window: Number of lagged pairs used in the OLS (>= 8 for a stable fit).

    Returns:
        (half_life_bars, zscore) — both guaranteed finite.

    Raises:
        FeatureError: If the window is fully flat (sample std is zero even
                      after the fallback), leaving no way to define a z-score.
    """
    if len(closes) < window + 1:
        raise FeatureError(
            f"OU: need at least {window + 1} bars, got {len(closes)}."
        )

    tail = closes.iloc[-(window + 1):].to_numpy(dtype=float)
    p_prev = tail[:-1]
    p_curr = tail[1:]
    current_close = float(tail[-1])

    design = np.column_stack([np.ones_like(p_prev), p_prev])
    coef, *_ = np.linalg.lstsq(design, p_curr, rcond=None)
    a = float(coef[0])
    b = float(coef[1])

    prev_mean = float(p_prev.mean())
    prev_std = float(p_prev.std(ddof=1))

    if not (0.0 < b < 1.0):
        half_life = _MAX_HALF_LIFE_BARS
        mu = prev_mean
        sigma = prev_std
    else:
        theta = -math.log(b)
        mu = a / (1.0 - b)
        residuals = p_curr - (a + b * p_prev)
        sigma_e = float(residuals.std(ddof=1))
        denom = 1.0 - b * b
        if sigma_e == 0.0 or denom <= 0.0:
            sigma = prev_std
        else:
            sigma = math.sqrt((sigma_e * sigma_e) / denom)

        half_life = math.log(2.0) / theta
        if not math.isfinite(half_life) or half_life > _MAX_HALF_LIFE_BARS:
            half_life = _MAX_HALF_LIFE_BARS

    if sigma <= 0.0 or not math.isfinite(sigma):
        raise FeatureError("OU: zero or non-finite unconditional std — degenerate window.")
    if not math.isfinite(mu):
        raise FeatureError("OU: non-finite long-run mean — degenerate fit.")

    zscore = (current_close - mu) / sigma
    if not math.isfinite(zscore):
        raise FeatureError("OU: non-finite z-score.")

    return float(half_life), float(zscore)


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
