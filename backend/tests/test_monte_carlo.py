"""
test_monte_carlo.py — Unit tests for monte_carlo.py.

Uses seeded RNG for deterministic results. No external dependencies.
"""

from __future__ import annotations

import math

import pytest


# Shared test parameters for a "normal" simulation
_BASE_PARAMS = dict(
    current_price=500.0,
    volatility=0.20,    # 20% annualized
    drift=0.08,         # 8% annualized
    take_profit_pct=0.02,
    stop_loss_pct=0.01,
    n_paths=1_000,
    horizon_minutes=390,
    seed=42,
)


class TestSimulateOutputs:
    def test_rates_sum_to_one(self):
        from backend.core.monte_carlo import simulate
        result = simulate(**_BASE_PARAMS)
        total = result.hit_target_rate + result.hit_stop_rate + result.neither_rate
        assert abs(total - 1.0) < 1e-9

    def test_result_is_frozen_dataclass(self):
        from backend.core.monte_carlo import simulate, SimulationResult
        result = simulate(**_BASE_PARAMS)
        assert isinstance(result, SimulationResult)
        with pytest.raises((AttributeError, TypeError)):
            result.hit_target_rate = 0.5  # type: ignore

    def test_all_rates_in_unit_interval(self):
        from backend.core.monte_carlo import simulate
        result = simulate(**_BASE_PARAMS)
        for rate in (result.hit_target_rate, result.hit_stop_rate, result.neither_rate):
            assert 0.0 <= rate <= 1.0

    def test_n_paths_in_result(self):
        from backend.core.monte_carlo import simulate
        result = simulate(**_BASE_PARAMS)
        assert result.n_paths == 1_000

    def test_deterministic_with_seed(self):
        from backend.core.monte_carlo import simulate
        r1 = simulate(**_BASE_PARAMS)
        r2 = simulate(**_BASE_PARAMS)
        assert r1.hit_target_rate == r2.hit_target_rate
        assert r1.hit_stop_rate == r2.hit_stop_rate

    def test_different_seeds_differ(self):
        from backend.core.monte_carlo import simulate
        r1 = simulate(**{**_BASE_PARAMS, "seed": 1})
        r2 = simulate(**{**_BASE_PARAMS, "seed": 2})
        # Extremely unlikely to be exactly equal with different seeds
        assert r1.hit_target_rate != r2.hit_target_rate


class TestExpectedPnl:
    def test_positive_drift_tends_positive_pnl(self):
        """High positive drift should produce net positive expected P&L."""
        from backend.core.monte_carlo import simulate
        result = simulate(
            current_price=500.0,
            volatility=0.10,     # low vol
            drift=2.0,           # very high drift → should mostly hit target
            take_profit_pct=0.02,
            stop_loss_pct=0.01,
            n_paths=2_000,
            seed=42,
        )
        assert result.expected_pnl_pct > 0.0

    def test_negative_drift_tends_negative_pnl(self):
        """Strong negative drift should produce negative expected P&L."""
        from backend.core.monte_carlo import simulate
        result = simulate(
            current_price=500.0,
            volatility=0.10,
            drift=-2.0,          # very negative drift → should mostly hit stop
            take_profit_pct=0.02,
            stop_loss_pct=0.01,
            n_paths=2_000,
            seed=42,
        )
        assert result.expected_pnl_pct < 0.0


class TestPathClamping:
    def test_n_paths_clamped_to_max(self):
        from backend.core.monte_carlo import simulate, _MAX_PATHS
        result = simulate(**{**_BASE_PARAMS, "n_paths": 999_999})
        assert result.n_paths == _MAX_PATHS

    def test_n_paths_clamped_to_min(self):
        from backend.core.monte_carlo import simulate, _MIN_PATHS
        result = simulate(**{**_BASE_PARAMS, "n_paths": 1})
        assert result.n_paths == _MIN_PATHS


class TestInputValidation:
    def test_negative_price_raises(self):
        from backend.core.monte_carlo import simulate, MonteCarloError
        with pytest.raises(MonteCarloError, match="current_price"):
            simulate(**{**_BASE_PARAMS, "current_price": -100.0})

    def test_zero_price_raises(self):
        from backend.core.monte_carlo import simulate, MonteCarloError
        with pytest.raises(MonteCarloError, match="current_price"):
            simulate(**{**_BASE_PARAMS, "current_price": 0.0})

    def test_nan_price_raises(self):
        from backend.core.monte_carlo import simulate, MonteCarloError
        with pytest.raises(MonteCarloError, match="current_price"):
            simulate(**{**_BASE_PARAMS, "current_price": float("nan")})

    def test_inf_price_raises(self):
        from backend.core.monte_carlo import simulate, MonteCarloError
        with pytest.raises(MonteCarloError, match="current_price"):
            simulate(**{**_BASE_PARAMS, "current_price": float("inf")})

    def test_zero_volatility_raises(self):
        from backend.core.monte_carlo import simulate, MonteCarloError
        with pytest.raises(MonteCarloError, match="volatility"):
            simulate(**{**_BASE_PARAMS, "volatility": 0.0})

    def test_zero_take_profit_pct_raises(self):
        from backend.core.monte_carlo import simulate, MonteCarloError
        with pytest.raises(MonteCarloError, match="take_profit_pct"):
            simulate(**{**_BASE_PARAMS, "take_profit_pct": 0.0})

    def test_take_profit_pct_above_one_raises(self):
        from backend.core.monte_carlo import simulate, MonteCarloError
        with pytest.raises(MonteCarloError, match="take_profit_pct"):
            simulate(**{**_BASE_PARAMS, "take_profit_pct": 1.5})

    def test_zero_stop_loss_pct_raises(self):
        from backend.core.monte_carlo import simulate, MonteCarloError
        with pytest.raises(MonteCarloError, match="stop_loss_pct"):
            simulate(**{**_BASE_PARAMS, "stop_loss_pct": 0.0})

    def test_nan_drift_raises(self):
        from backend.core.monte_carlo import simulate, MonteCarloError
        with pytest.raises(MonteCarloError, match="drift"):
            simulate(**{**_BASE_PARAMS, "drift": float("nan")})


class TestEdgeCases:
    def test_extreme_tp_rarely_hit(self):
        """A 90% take-profit target should almost never be hit."""
        from backend.core.monte_carlo import simulate
        result = simulate(
            current_price=500.0,
            volatility=0.20,
            drift=0.08,
            take_profit_pct=0.90,  # 90% gain required
            stop_loss_pct=0.01,
            n_paths=1_000,
            seed=42,
        )
        assert result.hit_target_rate < 0.05

    def test_tight_stop_frequently_hit(self):
        """A very tight stop (0.01%) should be hit very frequently."""
        from backend.core.monte_carlo import simulate
        result = simulate(
            current_price=500.0,
            volatility=0.20,
            drift=0.0,
            take_profit_pct=0.50,
            stop_loss_pct=0.0001,  # 0.01% stop — will be hit almost immediately
            n_paths=1_000,
            seed=42,
        )
        assert result.hit_stop_rate > 0.80
