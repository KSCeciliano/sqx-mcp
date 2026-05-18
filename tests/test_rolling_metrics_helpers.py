"""Unit tests for rolling_metrics helpers."""

from __future__ import annotations

import math
import random

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.rolling_metrics import (
    _rolling_beta_alpha,
    _rolling_correlation,
    _rolling_drawdown,
    _rolling_sharpe,
    _rolling_volatility,
    _rolling_win_rate,
)


def test_rolling_sharpe_length_matches_input() -> None:
    rng = random.Random(0)
    series = [rng.gauss(0.01, 0.02) for _ in range(100)]
    r = _rolling_sharpe(series, window=20, annualize=252)
    assert len(r["values"]) == 100
    # First window-1 entries are None
    assert all(v is None for v in r["values"][:19])
    assert r["values"][19] is not None


def test_rolling_sharpe_constant_returns_none() -> None:
    r = _rolling_sharpe([0.01] * 100, window=20, annualize=252)
    # All-constant → zero variance → None
    assert all(v is None for v in r["values"][19:])


def test_rolling_sharpe_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _rolling_sharpe([float("nan")] + [0.01] * 100, window=20, annualize=252)


def test_rolling_drawdown_zero_for_increasing_curve() -> None:
    curve = list(range(1, 101))  # strictly increasing
    r = _rolling_drawdown([float(x) for x in curve], window=20)
    valid = [v for v in r["values"] if v is not None]
    assert all(v == 0.0 for v in valid)


def test_rolling_drawdown_detects_local_drawdown() -> None:
    # Curve: rise, then drop, then recover
    curve = [100.0 + i for i in range(50)] + [149.0 - i for i in range(30)] + [120.0 + i for i in range(20)]
    r = _rolling_drawdown(curve, window=20)
    valid = [v for v in r["values"] if v is not None]
    assert max(valid) > 0.0  # some windows must show a drawdown


def test_rolling_volatility_grows_with_dispersion() -> None:
    rng = random.Random(0)
    quiet = [rng.gauss(0.0, 0.01) for _ in range(100)]
    loud = [rng.gauss(0.0, 0.1) for _ in range(100)]
    rq = _rolling_volatility(quiet, window=20, annualize=252)
    rl = _rolling_volatility(loud, window=20, annualize=252)
    assert rl["summary"]["mean"] > rq["summary"]["mean"] * 3


def test_rolling_correlation_perfect_correlation() -> None:
    rng = random.Random(0)
    a = [rng.gauss(0.0, 1.0) for _ in range(100)]
    b = [2.0 * x + 0.1 for x in a]  # perfect linear correlation
    r = _rolling_correlation(a, b, window=20)
    valid = [v for v in r["values"] if v is not None]
    assert all(abs(v - 1.0) < 1e-9 for v in valid)


def test_rolling_correlation_zero_for_independent() -> None:
    rng_a = random.Random(0)
    rng_b = random.Random(1)
    a = [rng_a.gauss(0.0, 1.0) for _ in range(500)]
    b = [rng_b.gauss(0.0, 1.0) for _ in range(500)]
    r = _rolling_correlation(a, b, window=50)
    assert r["summary"]["mean"] is not None
    assert abs(r["summary"]["mean"]) < 0.3


def test_rolling_beta_alpha_recovers_known_beta() -> None:
    """y = 2x + noise → beta should be ~2."""
    rng = random.Random(0)
    bench = [rng.gauss(0.0, 1.0) for _ in range(200)]
    strat = [2.0 * b + rng.gauss(0.0, 0.1) for b in bench]
    r = _rolling_beta_alpha(strat, bench, window=50)
    assert r["current_beta"] is not None
    assert abs(r["current_beta"] - 2.0) < 0.1


def test_rolling_win_rate_alternating() -> None:
    # Alternating win/loss
    pnls = [(-1) ** i for i in range(100)]
    r = _rolling_win_rate([float(p) for p in pnls], window=20)
    valid = [v for v in r["values"] if v is not None]
    # Win rate should be 0.5 on alternating signs
    assert all(abs(v - 0.5) < 1e-9 for v in valid)


def test_rolling_win_rate_all_wins() -> None:
    r = _rolling_win_rate([1.0] * 50, window=10)
    valid = [v for v in r["values"] if v is not None]
    assert all(v == 1.0 for v in valid)


def test_rolling_metrics_summary_has_current() -> None:
    r = _rolling_sharpe([math.sin(i) for i in range(100)], window=20, annualize=252)
    assert "current" in r["summary"]
