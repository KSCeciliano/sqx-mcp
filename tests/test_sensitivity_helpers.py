"""Unit tests for sensitivity helpers."""

from __future__ import annotations

import math

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.sensitivity import (
    _bullseye,
    _compare_two,
    _interp_metric,
    _local_slope,
    _plateau_score,
    _range_decay,
    _sort_points,
)

# ---- _sort_points / _interp_metric -----------------------------------------


def test_sort_points_ascending() -> None:
    pts = [{"parameter_value": 3, "metric": 1}, {"parameter_value": 1, "metric": 2}]
    out = _sort_points(pts)
    assert out[0]["parameter_value"] == 1


def test_interp_metric_exact_match() -> None:
    pts = [{"parameter_value": 1, "metric": 5}, {"parameter_value": 3, "metric": 9}]
    assert _interp_metric(pts, 1) == 5
    assert _interp_metric(pts, 3) == 9


def test_interp_metric_linear() -> None:
    pts = [{"parameter_value": 0, "metric": 0}, {"parameter_value": 10, "metric": 100}]
    assert _interp_metric(pts, 5) == 50.0


def test_interp_metric_clamps_at_boundaries() -> None:
    pts = [{"parameter_value": 1, "metric": 5}, {"parameter_value": 3, "metric": 9}]
    assert _interp_metric(pts, 0) == 5
    assert _interp_metric(pts, 100) == 9


# ---- _local_slope ----------------------------------------------------------


def test_local_slope_linear() -> None:
    # Metric grows by 1 per unit of parameter
    pts = [
        {"parameter_value": 0, "metric": 0},
        {"parameter_value": 5, "metric": 5},
        {"parameter_value": 10, "metric": 10},
    ]
    out = _local_slope(pts, baseline=5)
    assert out["slope"] == 1.0


def test_local_slope_flat_metric_zero_slope() -> None:
    pts = [
        {"parameter_value": 0, "metric": 7},
        {"parameter_value": 5, "metric": 7},
        {"parameter_value": 10, "metric": 7},
    ]
    out = _local_slope(pts, baseline=5)
    assert out["slope"] == 0.0


def test_local_slope_interpretation_low() -> None:
    pts = [
        {"parameter_value": 0, "metric": 100},
        {"parameter_value": 10, "metric": 100.001},
    ]
    out = _local_slope(pts, baseline=5)
    assert "low" in out["interpretation"]


def test_local_slope_interpretation_high() -> None:
    pts = [
        {"parameter_value": 0, "metric": 0},
        {"parameter_value": 1, "metric": 100},
    ]
    out = _local_slope(pts, baseline=0.5)
    assert "high" in out["interpretation"]


# ---- _plateau_score --------------------------------------------------------


def test_plateau_score_flat_curve_high_score() -> None:
    pts = [{"parameter_value": float(i), "metric": 10.0} for i in range(10)]
    out = _plateau_score(pts)
    assert out["score"] == 100.0
    assert out["verdict"] == "robust"


def test_plateau_score_volatile_curve_low_score() -> None:
    pts = [{"parameter_value": float(i), "metric": float(i * 10)} for i in range(10)]
    out = _plateau_score(pts)
    assert out["score"] < 50.0


def test_plateau_score_zero_mean() -> None:
    pts = [{"parameter_value": float(i), "metric": 0.0} for i in range(5)]
    out = _plateau_score(pts)
    assert out["score"] == 0
    assert "no signal" in out["note"]


# ---- _range_decay ----------------------------------------------------------


def test_range_decay_wide_plateau() -> None:
    # Metric flat at 10
    pts = [{"parameter_value": float(i), "metric": 10.0} for i in range(20)]
    out = _range_decay(pts, baseline=10, tolerance_pct=10.0)
    assert out["verdict"] == "wide_plateau"


def test_range_decay_narrow_plateau() -> None:
    # Metric peaks at 10 then drops sharply both sides
    pts = [
        {"parameter_value": 0, "metric": 5},
        {"parameter_value": 5, "metric": 7},
        {"parameter_value": 10, "metric": 100},  # baseline
        {"parameter_value": 15, "metric": 7},
        {"parameter_value": 20, "metric": 5},
    ]
    out = _range_decay(pts, baseline=10, tolerance_pct=10.0)
    assert out["verdict"] == "narrow_plateau"
    assert out["left_break_value"] is not None
    assert out["right_break_value"] is not None


def test_range_decay_asymmetric() -> None:
    # Metric flat on one side, drops on the other
    pts = [
        {"parameter_value": 0, "metric": 5},  # breaks below
        {"parameter_value": 5, "metric": 100},
        {"parameter_value": 10, "metric": 100},  # baseline
        {"parameter_value": 15, "metric": 99},
        {"parameter_value": 20, "metric": 99},
    ]
    out = _range_decay(pts, baseline=10, tolerance_pct=10.0)
    assert out["verdict"] == "asymmetric_decay"


def test_range_decay_zero_baseline_metric() -> None:
    pts = [{"parameter_value": float(i), "metric": 0.0} for i in range(5)]
    out = _range_decay(pts, baseline=2, tolerance_pct=10.0)
    assert out["verdict"] == "no_baseline"


# ---- _compare_two ----------------------------------------------------------


def test_compare_two_more_sensitive() -> None:
    # A flat → robust, B volatile → fragile
    a = [{"parameter_value": float(i), "metric": 10.0} for i in range(5)]
    b = [{"parameter_value": float(i), "metric": float(i * 100)} for i in range(5)]
    out = _compare_two(a, b, "A", "B")
    assert out["most_sensitive"] == "B"


def test_compare_two_tie() -> None:
    a = [{"parameter_value": float(i), "metric": 10.0} for i in range(5)]
    b = [{"parameter_value": float(i), "metric": 10.0} for i in range(5)]
    out = _compare_two(a, b, "A", "B")
    assert out["most_sensitive"] == "tie"


# ---- _bullseye -------------------------------------------------------------


def test_bullseye_wide_plateau() -> None:
    pts = [
        {"parameter_value": 1, "metric": 100},
        {"parameter_value": 2, "metric": 100},
        {"parameter_value": 3, "metric": 100},
        {"parameter_value": 4, "metric": 50},
    ]
    out = _bullseye(pts, tolerance_pct=10.0)
    assert out["best_metric"] == 100.0
    assert out["n_within_tolerance"] == 3
    assert out["verdict"] == "wide_safety_plateau"


def test_bullseye_single_optimum() -> None:
    pts = [
        {"parameter_value": 1, "metric": 10},
        {"parameter_value": 2, "metric": 100},  # narrow peak
        {"parameter_value": 3, "metric": 10},
    ]
    out = _bullseye(pts, tolerance_pct=5.0)
    assert out["n_within_tolerance"] == 1
    assert out["verdict"] == "single_point_optimum"


# ---- adversarial NaN / Inf inputs ------------------------------------------


def test_local_slope_rejects_nan_metric() -> None:
    pts = [
        {"parameter_value": 0, "metric": 0.0},
        {"parameter_value": 1, "metric": float("nan")},
    ]
    with pytest.raises(NumericValidationError):
        _local_slope(pts, baseline=0.5)


def test_local_slope_rejects_inf_baseline() -> None:
    pts = [
        {"parameter_value": 0, "metric": 0.0},
        {"parameter_value": 1, "metric": 1.0},
    ]
    with pytest.raises(NumericValidationError):
        _local_slope(pts, baseline=math.inf)


def test_local_slope_identical_left_right_parameters() -> None:
    # All neighbors collapse to same parameter_value → dx == 0 → safe note
    pts = [
        {"parameter_value": 5, "metric": 1.0},
        {"parameter_value": 5, "metric": 2.0},
    ]
    out = _local_slope(pts, baseline=5)
    assert out["slope"] is None
    assert "identical parameter values" in out["note"]


def test_plateau_score_rejects_nan_metric() -> None:
    pts = [{"parameter_value": float(i), "metric": float("nan")} for i in range(5)]
    with pytest.raises(NumericValidationError):
        _plateau_score(pts)


def test_range_decay_rejects_inf_tolerance() -> None:
    pts = [{"parameter_value": float(i), "metric": 10.0} for i in range(5)]
    with pytest.raises(NumericValidationError):
        _range_decay(pts, baseline=2, tolerance_pct=math.inf)


def test_compare_two_rejects_nan_in_b() -> None:
    a = [{"parameter_value": float(i), "metric": 10.0} for i in range(5)]
    b = [{"parameter_value": 1, "metric": float("nan")}]
    with pytest.raises(NumericValidationError):
        _compare_two(a, b, "A", "B")


def test_bullseye_rejects_nan_in_points() -> None:
    pts = [
        {"parameter_value": 1, "metric": 100},
        {"parameter_value": 2, "metric": float("inf")},
    ]
    with pytest.raises(NumericValidationError):
        _bullseye(pts, tolerance_pct=10.0)
