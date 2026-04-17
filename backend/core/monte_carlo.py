"""
monte_carlo.py — Geometric Brownian Motion simulation for trade outcome estimation.

Simulates 1,000+ price paths to estimate the probability of hitting a take-profit
target before a stop-loss within a trading horizon (default: 1 full trading day).

Math: GBM price path
  S(t) = S(0) * exp((μ - σ²/2)t + σ√t * Z)
  where Z ~ N(0,1), μ = annualized drift, σ = annualized volatility

Security notes:
  - All inputs validated with math.isfinite() before any numpy operations.
  - n_paths clamped to [100, 10000] — prevents resource exhaustion.
  - Output rates validated to sum to 1.0 within 1e-9 tolerance.
  - numpy.random.default_rng() used (new-style Generator API, not legacy np.random).
    Note: This is for simulation, not cryptographic use — S311 bandit warning
    does not apply here.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Final

import numpy as np

logger = logging.getLogger(__name__)

_MAX_PATHS: Final[int] = 10_000
_MIN_PATHS: Final[int] = 100
_MINUTES_PER_YEAR: Final[float] = 252.0 * 390.0  # trading minutes per year


class MonteCarloError(ValueError):
    """Raised when simulation inputs are invalid or internal accounting fails."""


@dataclass(frozen=True)
class SimulationResult:
    """
    Outcome of a Monte Carlo simulation run.

    All rates are fractions in [0.0, 1.0] and sum to 1.0.
    """
    hit_target_rate: float    # fraction of paths that hit take-profit before stop-loss
    hit_stop_rate: float      # fraction of paths that hit stop-loss before take-profit
    neither_rate: float       # fraction that expired within horizon without triggering
    expected_pnl_pct: float   # probability-weighted expected return
    n_paths: int              # actual paths simulated (after clamping)


# ── Public API ───────────────────────────────────────────────────────────────

def simulate(
    current_price: float,
    volatility: float,           # annualized volatility, e.g. 0.20 = 20%
    drift: float,                # annualized drift, e.g. 0.08 = 8%
    take_profit_pct: float,      # e.g. 0.02 = 2% above entry (positive)
    stop_loss_pct: float,        # e.g. 0.01 = 1% below entry (positive value)
    n_paths: int = 1_000,
    horizon_minutes: int = 390,  # 390 = 1 full US trading day
    seed: int | None = None,     # optional seed for reproducibility (tests only)
) -> SimulationResult:
    """
    Simulate price paths using Geometric Brownian Motion.

    Args:
        current_price:    Current asset price (must be positive finite).
        volatility:       Annualized volatility (must be positive finite).
        drift:            Annualized drift/mean return (must be finite).
        take_profit_pct:  Take-profit threshold as fraction of price (0, 1].
        stop_loss_pct:    Stop-loss threshold as fraction of price (0, 1].
        n_paths:          Number of simulation paths. Clamped to [100, 10000].
        horizon_minutes:  Simulation horizon in minutes. Default 390 (1 trading day).
        seed:             RNG seed for reproducibility. None = fresh randomness.

    Returns:
        SimulationResult with hit rates and expected P&L.

    Raises:
        MonteCarloError on invalid inputs or internal accounting error.
    """
    # ── Input validation ─────────────────────────────────────────────────────
    if not math.isfinite(current_price) or current_price <= 0:
        raise MonteCarloError(
            f"current_price must be a positive finite number, got {current_price!r}"
        )
    if not math.isfinite(volatility) or volatility <= 0:
        raise MonteCarloError(
            f"volatility must be a positive finite number, got {volatility!r}"
        )
    if not math.isfinite(drift):
        raise MonteCarloError(f"drift must be finite, got {drift!r}")
    if not (0.0 < take_profit_pct <= 1.0):
        raise MonteCarloError(
            f"take_profit_pct must be in (0, 1], got {take_profit_pct!r}"
        )
    if not (0.0 < stop_loss_pct <= 1.0):
        raise MonteCarloError(
            f"stop_loss_pct must be in (0, 1], got {stop_loss_pct!r}"
        )
    if horizon_minutes <= 0:
        raise MonteCarloError(
            f"horizon_minutes must be positive, got {horizon_minutes!r}"
        )

    # Clamp n_paths (silently — log if adjusted)
    clamped = min(max(n_paths, _MIN_PATHS), _MAX_PATHS)
    if clamped != n_paths:
        logger.warning(
            "n_paths=%d clamped to %d (allowed range [%d, %d])",
            n_paths, clamped, _MIN_PATHS, _MAX_PATHS,
        )
    n_paths = clamped

    # ── GBM simulation ───────────────────────────────────────────────────────
    dt = 1.0 / _MINUTES_PER_YEAR  # time step: one minute in annualized units

    rng = np.random.default_rng(seed)
    Z = rng.standard_normal((n_paths, horizon_minutes))

    drift_term = (drift - 0.5 * volatility ** 2) * dt
    diffusion_term = volatility * math.sqrt(dt) * Z

    # Cumulative log returns → price paths
    log_returns = drift_term + diffusion_term           # shape: (n_paths, horizon_minutes)
    cumulative = np.cumsum(log_returns, axis=1)          # shape: (n_paths, horizon_minutes)
    price_paths = current_price * np.exp(cumulative)    # shape: (n_paths, horizon_minutes)

    # ── Path outcome determination ────────────────────────────────────────────
    target_price = current_price * (1.0 + take_profit_pct)
    stop_price = current_price * (1.0 - stop_loss_pct)

    hit_target_mask = np.any(price_paths >= target_price, axis=1)  # (n_paths,)
    hit_stop_mask = np.any(price_paths <= stop_price, axis=1)       # (n_paths,)

    # For paths that hit both: award to whichever was crossed first
    both_hit = hit_target_mask & hit_stop_mask

    if np.any(both_hit):
        # argmax returns first True index; returns 0 if never True (handled by mask check)
        target_first_idx = np.argmax(price_paths >= target_price, axis=1)
        stop_first_idx = np.argmax(price_paths <= stop_price, axis=1)

        target_wins_both = both_hit & (target_first_idx <= stop_first_idx)
        stop_wins_both = both_hit & (stop_first_idx < target_first_idx)
    else:
        target_wins_both = np.zeros(n_paths, dtype=bool)
        stop_wins_both = np.zeros(n_paths, dtype=bool)

    hit_target_only = hit_target_mask & ~hit_stop_mask
    hit_stop_only = hit_stop_mask & ~hit_target_mask

    final_target = int((hit_target_only | target_wins_both).sum())
    final_stop = int((hit_stop_only | stop_wins_both).sum())
    final_neither = n_paths - final_target - final_stop

    # ── Internal accounting validation ────────────────────────────────────────
    total = final_target + final_stop + final_neither
    if total != n_paths:
        raise MonteCarloError(
            f"Path accounting error: {final_target} + {final_stop} + "
            f"{final_neither} = {total} ≠ {n_paths}"
        )

    hit_target_rate = final_target / n_paths
    hit_stop_rate = final_stop / n_paths
    neither_rate = final_neither / n_paths

    rate_sum = hit_target_rate + hit_stop_rate + neither_rate
    if abs(rate_sum - 1.0) > 1e-9:
        raise MonteCarloError(
            f"Rate accounting error: rates sum to {rate_sum:.12f}, expected 1.0"
        )

    expected_pnl_pct = (hit_target_rate * take_profit_pct) - (hit_stop_rate * stop_loss_pct)

    result = SimulationResult(
        hit_target_rate=hit_target_rate,
        hit_stop_rate=hit_stop_rate,
        neither_rate=neither_rate,
        expected_pnl_pct=expected_pnl_pct,
        n_paths=n_paths,
    )

    logger.info(
        "Monte Carlo: hit_target=%.1f%% hit_stop=%.1f%% neither=%.1f%% "
        "expected_pnl=%.3f%% (n=%d)",
        hit_target_rate * 100, hit_stop_rate * 100, neither_rate * 100,
        expected_pnl_pct * 100, n_paths,
    )
    return result
