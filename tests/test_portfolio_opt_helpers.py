"""Unit tests for portfolio_opt helpers."""

from __future__ import annotations

import random

from sq_mcp.tools.portfolio_opt import (
    _covariance_matrix,
    _diversification_ratio,
    _equal_weight,
    _inverse_vol,
    _min_variance,
    _portfolio_variance,
    _risk_parity,
)


def _rng_returns(seed: int, n: int) -> list[float]:
    r = random.Random(seed)
    return [r.gauss(0.0, 1.0) for _ in range(n)]


def test_equal_weight_sums_to_one() -> None:
    returns = {
        "A": _rng_returns(1, 100),
        "B": _rng_returns(2, 100),
        "C": _rng_returns(3, 100),
    }
    result = _equal_weight(returns)
    total = sum(result["weights"].values())
    assert abs(total - 1.0) < 1e-9


def test_equal_weight_singleton() -> None:
    result = _equal_weight({"A": _rng_returns(0, 50)})
    assert result["weights"]["A"] == 1.0


def test_inverse_vol_higher_weight_to_lower_vol() -> None:
    rng = random.Random(0)
    returns = {
        "low_vol": [rng.gauss(0.0, 0.1) for _ in range(100)],
        "high_vol": [rng.gauss(0.0, 10.0) for _ in range(100)],
    }
    result = _inverse_vol(returns)
    assert result["weights"]["low_vol"] > result["weights"]["high_vol"]
    total = sum(result["weights"].values())
    assert abs(total - 1.0) < 1e-9


def test_covariance_matrix_diagonal_is_variance() -> None:
    series = [_rng_returns(i, 100) for i in range(3)]
    returns = {f"S{i}": s for i, s in enumerate(series)}
    names, cov = _covariance_matrix(returns)
    assert len(names) == 3
    for i in range(3):
        assert cov[i][i] > 0


def test_risk_parity_sums_to_one() -> None:
    returns = {
        "A": _rng_returns(1, 100),
        "B": _rng_returns(2, 100),
        "C": _rng_returns(3, 100),
    }
    result = _risk_parity(returns, max_iter=200, tol=1e-6)
    total = sum(result["weights"].values())
    assert abs(total - 1.0) < 1e-6


def test_min_variance_sums_to_one() -> None:
    returns = {
        "A": _rng_returns(1, 100),
        "B": _rng_returns(2, 100),
    }
    result = _min_variance(returns, max_iter=200, tol=1e-6)
    total = sum(result["weights"].values())
    assert abs(total - 1.0) < 1e-6


def test_min_variance_prefers_low_vol() -> None:
    rng = random.Random(0)
    returns = {
        "low": [rng.gauss(0.0, 0.1) for _ in range(200)],
        "high": [rng.gauss(0.0, 5.0) for _ in range(200)],
    }
    result = _min_variance(returns, max_iter=300, tol=1e-7)
    assert result["weights"]["low"] > result["weights"]["high"]


def test_diversification_ratio_uncorrelated() -> None:
    returns = {
        "A": _rng_returns(1, 200),
        "B": _rng_returns(2, 200),
        "C": _rng_returns(3, 200),
    }
    weights = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    result = _diversification_ratio(weights, returns)
    # Uncorrelated assets → diversification > 1
    assert result["diversification_ratio"] > 1.2


def test_portfolio_variance_zero_weights_zero() -> None:
    cov = [[1.0, 0.5], [0.5, 1.0]]
    var = _portfolio_variance([0.0, 0.0], cov)
    assert var == 0.0
