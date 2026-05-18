"""Unit tests for overfit_diag helpers."""

from __future__ import annotations

from sq_mcp.tools.overfit_diag import (
    _dsr,
    _expected_max_sharpe,
    _haircut_sharpe,
    _min_track_record,
    _norm_cdf,
    _pbo,
)


def test_norm_cdf_known_values() -> None:
    assert abs(_norm_cdf(0.0) - 0.5) < 1e-6
    assert _norm_cdf(1.96) > 0.97
    assert _norm_cdf(-1.96) < 0.03


def test_expected_max_sharpe_grows() -> None:
    e2 = _expected_max_sharpe(2)
    e100 = _expected_max_sharpe(100)
    e10000 = _expected_max_sharpe(10000)
    assert e2 < e100 < e10000


def test_dsr_higher_sharpe_higher_probability() -> None:
    low = _dsr(0.5, n_trials=10, n_observations=252, skewness=0.0, kurtosis=3.0, benchmark_sharpe=0.0)
    high = _dsr(2.0, n_trials=10, n_observations=252, skewness=0.0, kurtosis=3.0, benchmark_sharpe=0.0)
    assert high["dsr_probability"] > low["dsr_probability"]


def test_dsr_more_trials_lower_probability() -> None:
    few = _dsr(1.5, n_trials=5, n_observations=252, skewness=0.0, kurtosis=3.0, benchmark_sharpe=0.0)
    many = _dsr(1.5, n_trials=5000, n_observations=252, skewness=0.0, kurtosis=3.0, benchmark_sharpe=0.0)
    assert many["dsr_probability"] < few["dsr_probability"]


def test_haircut_sharpe_penalizes_many_trials() -> None:
    one = _haircut_sharpe(2.0, n_trials=1, n_observations=252)
    many = _haircut_sharpe(2.0, n_trials=1000, n_observations=252)
    assert many["sharpe_haircut"] < one["sharpe_haircut"]
    assert many["haircut_amount"] > one["haircut_amount"]


def test_pbo_consistent_strategies() -> None:
    # Top IS is also top OOS — no overfitting
    pairs = [
        {"is_sharpe": 2.0, "oos_sharpe": 1.8},
        {"is_sharpe": 1.5, "oos_sharpe": 1.4},
        {"is_sharpe": 1.0, "oos_sharpe": 0.9},
        {"is_sharpe": 0.5, "oos_sharpe": 0.4},
    ]
    result = _pbo(pairs)
    assert result["is_overfit"] is False
    assert result["interpretation"] == "consistent"


def test_pbo_overfit_strategies() -> None:
    # Top IS is below OOS median — overfit
    pairs = [
        {"is_sharpe": 3.0, "oos_sharpe": -0.5},  # top IS, terrible OOS
        {"is_sharpe": 1.0, "oos_sharpe": 1.2},
        {"is_sharpe": 0.8, "oos_sharpe": 1.0},
        {"is_sharpe": 0.6, "oos_sharpe": 0.8},
    ]
    result = _pbo(pairs)
    assert result["is_overfit"] is True


def test_min_track_record_under_benchmark() -> None:
    # observed equal benchmark — cannot claim
    result = _min_track_record(0.5, benchmark_sharpe=0.5, skewness=0.0, kurtosis=3.0, confidence=0.95)
    assert result["min_track_record_length"] is None


def test_min_track_record_positive_diff() -> None:
    result = _min_track_record(1.0, benchmark_sharpe=0.0, skewness=0.0, kurtosis=3.0, confidence=0.95)
    assert result["min_track_record_length"] is not None
    assert result["min_track_record_length"] > 0
