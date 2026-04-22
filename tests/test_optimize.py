"""Tests for the pure MPT math in `allocator.optimize`.

Exercises the deterministic path (seeded RNG) so assertions can check
specific numerical properties: bounds enforcement, sum-to-1 constraints,
cloud shape, and ordering between named portfolios.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from allocator.optimize import build_frontier


def _synthetic_prices(seed: int = 42, periods: int = 500) -> pd.DataFrame:
    """Three synthetic assets: low-return/low-vol, medium, high-return/high-vol.

    Log-normal random walks; seed pinned so test assertions stay stable.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=periods, freq="D")
    return pd.DataFrame(
        {
            "LOW": 100 * np.exp(np.cumsum(rng.normal(0.0001, 0.005, periods))),
            "MED": 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, periods))),
            "HIGH": 100 * np.exp(np.cumsum(rng.normal(0.0006, 0.02, periods))),
        },
        index=dates,
    )


def test_build_frontier_max_sharpe_weights_sum_to_one():
    prices = _synthetic_prices()
    result = build_frontier(prices, risk_free_rate=0.04, n_random=500, rng=np.random.default_rng(1))
    total = sum(result.max_sharpe.weights.values())
    assert total == pytest.approx(1.0, abs=1e-6)


def test_build_frontier_min_vol_weights_sum_to_one():
    prices = _synthetic_prices()
    result = build_frontier(prices, risk_free_rate=0.04, n_random=500, rng=np.random.default_rng(1))
    total = sum(result.min_volatility.weights.values())
    assert total == pytest.approx(1.0, abs=1e-6)


def test_build_frontier_respects_max_weight_bound():
    """A hard cap on HIGH must be honored by SLSQP."""
    prices = _synthetic_prices()
    result = build_frontier(
        prices,
        risk_free_rate=0.04,
        n_random=500,
        max_weights={"HIGH": 0.30},
        rng=np.random.default_rng(1),
    )
    assert result.max_sharpe.weights["HIGH"] <= 0.30 + 1e-6
    assert result.min_volatility.weights["HIGH"] <= 0.30 + 1e-6


def test_build_frontier_respects_min_weight_floor():
    prices = _synthetic_prices()
    result = build_frontier(
        prices,
        risk_free_rate=0.04,
        n_random=500,
        min_weights={"LOW": 0.10},
        rng=np.random.default_rng(1),
    )
    assert result.max_sharpe.weights["LOW"] >= 0.10 - 1e-6
    assert result.min_volatility.weights["LOW"] >= 0.10 - 1e-6


def test_build_frontier_min_vol_has_lower_or_equal_vol_than_max_sharpe():
    """Min-vol portfolio's vol never exceeds the max-Sharpe portfolio's vol — MPT definition."""
    prices = _synthetic_prices()
    result = build_frontier(prices, risk_free_rate=0.04, n_random=500, rng=np.random.default_rng(1))
    assert result.min_volatility.volatility <= result.max_sharpe.volatility + 1e-6


def test_build_frontier_current_and_target_stats_computed_when_supplied():
    prices = _synthetic_prices()
    result = build_frontier(
        prices,
        risk_free_rate=0.04,
        n_random=200,
        current_weights={"LOW": 0.5, "MED": 0.5, "HIGH": 0.0},
        target_weights={"LOW": 0.33, "MED": 0.33, "HIGH": 0.34},
        rng=np.random.default_rng(1),
    )
    assert result.current is not None
    assert result.target is not None
    assert sum(result.current.weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert sum(result.target.weights.values()) == pytest.approx(1.0, abs=1e-6)


def test_build_frontier_cloud_shape_matches_request():
    prices = _synthetic_prices()
    result = build_frontier(prices, risk_free_rate=0.04, n_random=777, rng=np.random.default_rng(1))
    assert result.cloud_returns.shape == (777,)
    assert result.cloud_volatility.shape == (777,)
    assert result.cloud_sharpe.shape == (777,)
    assert result.cloud_weights.shape == (777, 3)


def test_build_frontier_rejects_bad_bounds():
    prices = _synthetic_prices()
    with pytest.raises(ValueError, match="min_weight > max_weight"):
        build_frontier(
            prices,
            risk_free_rate=0.04,
            n_random=100,
            min_weights={"LOW": 0.8},
            max_weights={"LOW": 0.1},
        )


def test_build_frontier_rejects_infeasible_min_weights():
    """Asking for min-weights that sum > 1 is always infeasible."""
    prices = _synthetic_prices()
    with pytest.raises(ValueError, match="min_weights sum"):
        build_frontier(
            prices,
            risk_free_rate=0.04,
            n_random=100,
            min_weights={"LOW": 0.5, "MED": 0.5, "HIGH": 0.5},
        )


def test_build_frontier_rejects_single_asset():
    prices = _synthetic_prices().iloc[:, :1]
    with pytest.raises(ValueError, match="at least two symbols"):
        build_frontier(prices, risk_free_rate=0.04, n_random=100)


def test_build_frontier_rejects_too_little_history():
    prices = _synthetic_prices(periods=10)
    with pytest.raises(ValueError, match="need ≥30"):
        build_frontier(prices, risk_free_rate=0.04, n_random=100)
