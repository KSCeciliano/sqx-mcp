"""Unit tests for tail_risk helpers."""

from __future__ import annotations

import math
import random

from sq_mcp.tools.tail_risk import (
    _cvar,
    _gain_to_pain,
    _historical_var,
    _inv_norm_cdf,
    _parametric_var,
    _tail_ratio,
)


def test_historical_var_known_distribution() -> None:
    # Symmetric ±1 series; VaR(0.95) should be ~1.0
    returns = [(-1.0) ** i for i in range(100)]
    result = _historical_var(returns, 0.95)
    assert result["var"] >= 0.9


def test_historical_var_zero_returns() -> None:
    result = _historical_var([0.0] * 100, 0.95)
    assert result["var"] == 0.0


def test_cvar_more_extreme_than_var() -> None:
    rng = random.Random(42)
    returns = [rng.gauss(0.0, 1.0) for _ in range(1000)]
    var = _historical_var(returns, 0.95)["var"]
    cvar = _cvar(returns, 0.95)["cvar"]
    # CVaR should be >= VaR in magnitude (avg of tail ≤ tail boundary)
    assert cvar >= var * 0.9  # allow some sampling noise


def test_parametric_var_against_known_normal() -> None:
    rng = random.Random(0)
    returns = [rng.gauss(0.0, 1.0) for _ in range(5000)]
    result = _parametric_var(returns, 0.95)
    # z(0.05) ≈ -1.645, so VaR ≈ 1.645 for unit normal
    assert 1.4 < result["var"] < 1.9
    assert abs(result["mean"]) < 0.2
    assert 0.85 < result["std"] < 1.15


def test_inv_norm_cdf_known_quantiles() -> None:
    # CDF^-1(0.5) = 0
    assert abs(_inv_norm_cdf(0.5)) < 1e-3
    # CDF^-1(0.95) ≈ 1.645
    assert 1.6 < _inv_norm_cdf(0.95) < 1.7
    # CDF^-1(0.05) ≈ -1.645
    assert -1.7 < _inv_norm_cdf(0.05) < -1.6


def test_inv_norm_cdf_edges() -> None:
    assert math.isinf(_inv_norm_cdf(0.0)) and _inv_norm_cdf(0.0) < 0
    assert math.isinf(_inv_norm_cdf(1.0)) and _inv_norm_cdf(1.0) > 0


def test_tail_ratio_symmetric() -> None:
    rng = random.Random(0)
    returns = [rng.gauss(0.0, 1.0) for _ in range(2000)]
    result = _tail_ratio(returns)
    assert 0.7 < result["tail_ratio"] < 1.4  # roughly symmetric


def test_tail_ratio_skewed_upside() -> None:
    rng = random.Random(0)
    returns = [rng.gauss(0.0, 1.0) for _ in range(2000)]
    # Add fat upside tail
    returns.extend([5.0, 6.0, 7.0, 8.0])
    result = _tail_ratio(returns)
    # The fat upside tail should push tail ratio > 1
    assert result["tail_ratio"] > 1.0


def test_gain_to_pain_robust() -> None:
    # 5 of each, gain twice the size of loss
    returns = [1.0, 1.0, 1.0, 1.0, 1.0, -0.4, -0.4, -0.4, -0.4, -0.4]
    result = _gain_to_pain(returns)
    assert result["gain_to_pain"] > 2.0
    assert result["interpretation"] == "robust"


def test_gain_to_pain_no_losses() -> None:
    result = _gain_to_pain([1.0] * 20)
    assert result["gain_to_pain"] is None
