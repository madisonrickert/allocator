"""Modern Portfolio Theory / Monte-Carlo asset-allocation math.

Pure numerics — no I/O. Takes a DataFrame of daily adjusted closes plus a
handful of scalar knobs, returns an `OptimizationResult` with three named
portfolios (max-Sharpe, min-volatility, equal-weight) and the full random
point cloud for plotting. The CLI is responsible for sourcing the prices
and rendering the result; this module is reusable from a notebook.

Method:

1. Compute daily simple (percentage) returns per symbol and drop rows with
   missing data. Arithmetic returns are used rather than log returns because
   portfolio return is only a linear combination of weights for arithmetic
   returns — log-return portfolios drift low by sigma^2/2, which would turn
   "expected return" into a confusing misnomer in the output.
2. Annualize the mean-return vector and covariance matrix by multiplying by
   `trading_days_per_year` — a single factor, so a mixed stock/crypto
   portfolio is an approximation (see `build_frontier` docstring).
3. Monte-Carlo cloud: draw `n_random` random weight vectors, scale into
   [min_weight, max_weight] per symbol, normalize each row to sum to 1.
   Computed fully vectorized; ~10k portfolios run in milliseconds.
4. Precise optima via `scipy.optimize.minimize` (SLSQP): max-Sharpe and
   min-volatility, each subject to `sum(w) == 1` and the per-symbol bounds.
   Initial guess is equal-weight.

Post-normalization note: scaling weights into per-symbol bounds *before*
normalization can push a column outside its bounds after dividing by the row
sum. The random cloud treats this as a sampling imperfection (it's a cloud,
not a frontier); the SLSQP optima enforce bounds as hard constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from scipy.optimize import minimize

if TYPE_CHECKING:
    from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class PortfolioStats:
    """Annualized expected return, volatility, and Sharpe ratio for one weighting."""

    expected_return: float
    volatility: float
    sharpe: float
    weights: dict[str, float]
    """symbol → fractional weight, in the same order as the input DataFrame."""


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """Everything `allocator optimize` needs to render a report."""

    symbols: tuple[str, ...]
    risk_free_rate: float
    trading_days_per_year: int
    max_sharpe: PortfolioStats
    min_volatility: PortfolioStats
    equal_weight: PortfolioStats
    current: PortfolioStats | None
    """The user's current allocation, if one was supplied."""

    target: PortfolioStats | None
    """The portfolio's target allocation from `targets.yaml`, if supplied."""

    cloud_returns: NDArray[np.float64]
    cloud_volatility: NDArray[np.float64]
    cloud_sharpe: NDArray[np.float64]
    cloud_weights: NDArray[np.float64]
    """Shape (N, len(symbols)) — one row per simulated portfolio."""


def build_frontier(
    prices: pd.DataFrame,
    *,
    risk_free_rate: float,
    trading_days_per_year: int = 252,
    n_random: int = 10_000,
    min_weights: dict[str, float] | None = None,
    max_weights: dict[str, float] | None = None,
    current_weights: dict[str, float] | None = None,
    target_weights: dict[str, float] | None = None,
    rng: np.random.Generator | None = None,
) -> OptimizationResult:
    """Run the MC cloud and compute named optima for *prices*.

    *prices* has one column per symbol, one row per trading day. Columns are
    used in order — caller controls symbol order by slicing the DataFrame.
    Pass `trading_days_per_year=365` for pure-crypto portfolios, 252 for
    equities, and accept the approximation if you're mixing.
    """
    if prices.shape[1] < 2:
        raise ValueError("optimization needs at least two symbols")
    if prices.shape[0] < 30:
        raise ValueError(
            f"only {prices.shape[0]} rows of price history — need ≥30 to estimate covariance"
        )

    symbols = tuple(str(c) for c in prices.columns)
    simple_returns = prices.pct_change().dropna()
    if simple_returns.empty:
        raise ValueError("returns are empty after dropping NaNs; prices may be misaligned")

    mean_returns = simple_returns.mean().to_numpy() * trading_days_per_year
    cov_matrix = simple_returns.cov().to_numpy() * trading_days_per_year

    mn = _as_bound_array(min_weights, symbols, default=0.0)
    mx = _as_bound_array(max_weights, symbols, default=1.0)
    if np.any(mn > mx):
        raise ValueError("some symbol has min_weight > max_weight")
    if mn.sum() > 1.0 + 1e-9:
        raise ValueError(f"min_weights sum to {mn.sum():.4f}, cannot satisfy sum(w)==1")

    # Monte-Carlo cloud.
    rng = rng or np.random.default_rng()
    raw = rng.random((n_random, len(symbols)))
    scaled = mn + (mx - mn) * raw
    row_sums = scaled.sum(axis=1, keepdims=True)
    cloud_weights = scaled / row_sums
    cloud_returns = cloud_weights @ mean_returns
    cloud_vol = np.sqrt(np.einsum("ij,jk,ik->i", cloud_weights, cov_matrix, cloud_weights))
    cloud_sharpe = np.where(cloud_vol > 0, (cloud_returns - risk_free_rate) / cloud_vol, 0.0)

    # Precise optima via SLSQP. Bounds + sum-to-1 equality constraint.
    bounds = tuple(zip(mn.tolist(), mx.tolist(), strict=True))
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    x0 = _feasible_initial_guess(mn, mx)

    def neg_sharpe(w: NDArray[np.float64]) -> float:
        ret, vol = _portfolio_return_vol(w, mean_returns, cov_matrix)
        return -(ret - risk_free_rate) / vol if vol > 0 else 0.0

    def portfolio_vol(w: NDArray[np.float64]) -> float:
        _, vol = _portfolio_return_vol(w, mean_returns, cov_matrix)
        return vol

    max_sharpe_res = minimize(
        neg_sharpe, x0, method="SLSQP", bounds=bounds, constraints=constraints
    )
    min_vol_res = minimize(
        portfolio_vol, x0, method="SLSQP", bounds=bounds, constraints=constraints
    )

    max_sharpe = _stats_from_weights(
        max_sharpe_res.x, symbols, mean_returns, cov_matrix, risk_free_rate
    )
    min_vol = _stats_from_weights(min_vol_res.x, symbols, mean_returns, cov_matrix, risk_free_rate)
    equal_w = np.full(len(symbols), 1.0 / len(symbols))
    equal_stats = _stats_from_weights(equal_w, symbols, mean_returns, cov_matrix, risk_free_rate)

    current_stats = _maybe_weights_stats(
        current_weights, symbols, mean_returns, cov_matrix, risk_free_rate
    )
    target_stats = _maybe_weights_stats(
        target_weights, symbols, mean_returns, cov_matrix, risk_free_rate
    )

    return OptimizationResult(
        symbols=symbols,
        risk_free_rate=risk_free_rate,
        trading_days_per_year=trading_days_per_year,
        max_sharpe=max_sharpe,
        min_volatility=min_vol,
        equal_weight=equal_stats,
        current=current_stats,
        target=target_stats,
        cloud_returns=cloud_returns,
        cloud_volatility=cloud_vol,
        cloud_sharpe=cloud_sharpe,
        cloud_weights=cloud_weights,
    )


# ────────────────────────── helpers ──────────────────────────
def _portfolio_return_vol(
    weights: NDArray[np.float64],
    mean_returns: NDArray[np.float64],
    cov_matrix: NDArray[np.float64],
) -> tuple[float, float]:
    ret = float(np.dot(weights, mean_returns))
    vol = float(np.sqrt(np.dot(weights, cov_matrix @ weights)))
    return ret, vol


def _stats_from_weights(
    weights: NDArray[np.float64],
    symbols: tuple[str, ...],
    mean_returns: NDArray[np.float64],
    cov_matrix: NDArray[np.float64],
    risk_free_rate: float,
) -> PortfolioStats:
    ret, vol = _portfolio_return_vol(weights, mean_returns, cov_matrix)
    sharpe = (ret - risk_free_rate) / vol if vol > 0 else 0.0
    return PortfolioStats(
        expected_return=ret,
        volatility=vol,
        sharpe=sharpe,
        weights=dict(zip(symbols, (float(w) for w in weights), strict=True)),
    )


def _maybe_weights_stats(
    weights: dict[str, float] | None,
    symbols: tuple[str, ...],
    mean_returns: NDArray[np.float64],
    cov_matrix: NDArray[np.float64],
    risk_free_rate: float,
) -> PortfolioStats | None:
    if weights is None:
        return None
    arr = np.array([weights.get(s, 0.0) for s in symbols], dtype=float)
    total = arr.sum()
    if total <= 0:
        return None
    arr = arr / total  # renormalize so sub-portfolios still plot correctly
    return _stats_from_weights(arr, symbols, mean_returns, cov_matrix, risk_free_rate)


def _as_bound_array(
    bounds: dict[str, float] | None,
    symbols: tuple[str, ...],
    *,
    default: float,
) -> NDArray[np.float64]:
    if bounds is None:
        return np.full(len(symbols), default)
    return np.array([bounds.get(s, default) for s in symbols], dtype=float)


def _feasible_initial_guess(
    min_weights: NDArray[np.float64], max_weights: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Equal-weight shifted into the bound box. Good enough for SLSQP's starting point."""
    n = len(min_weights)
    x0 = np.full(n, 1.0 / n)
    x0 = np.clip(x0, min_weights, max_weights)
    # Rescale to sum to 1 while staying inside bounds — not guaranteed in pathological cases,
    # but SLSQP will repair from here.
    total = x0.sum()
    if total > 0:
        x0 = x0 / total
    return x0
