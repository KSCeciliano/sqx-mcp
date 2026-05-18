"""Unit tests for the shared _numerics helpers."""

from __future__ import annotations

import math

import pytest

from sq_mcp.tools._numerics import (
    NumericValidationError,
    clamp,
    is_finite,
    mean,
    percentile,
    safe_div,
    safe_pct,
    stddev,
    validate_finite_floats,
)


def test_is_finite_normal() -> None:
    assert is_finite(1.0)
    assert is_finite(0)
    assert is_finite(-1e10)


def test_is_finite_rejects_nan_inf() -> None:
    assert not is_finite(float("nan"))
    assert not is_finite(float("inf"))
    assert not is_finite(float("-inf"))


def test_is_finite_rejects_non_numbers() -> None:
    assert not is_finite("1.0")  # type: ignore[arg-type]
    assert not is_finite(None)  # type: ignore[arg-type]


def test_validate_finite_floats_passes_clean_list() -> None:
    xs = [1.0, 2.0, 3.0]
    assert validate_finite_floats(xs) is xs


def test_validate_finite_floats_rejects_nan() -> None:
    with pytest.raises(NumericValidationError, match="non-finite"):
        validate_finite_floats([1.0, float("nan"), 3.0])


def test_validate_finite_floats_rejects_inf() -> None:
    with pytest.raises(NumericValidationError, match="non-finite"):
        validate_finite_floats([1.0, float("inf"), 3.0])


def test_validate_finite_floats_min_length() -> None:
    with pytest.raises(NumericValidationError, match="at least 5"):
        validate_finite_floats([1.0, 2.0], min_length=5)


def test_validate_finite_floats_reports_index() -> None:
    with pytest.raises(NumericValidationError, match=r"\[1\]"):
        validate_finite_floats([1.0, float("nan"), 3.0])


def test_safe_div_normal() -> None:
    assert safe_div(10, 2) == 5.0


def test_safe_div_zero_denom() -> None:
    assert safe_div(10, 0) is None
    assert safe_div(10, 0, default=0.0) == 0.0


def test_safe_div_nan_input() -> None:
    assert safe_div(float("nan"), 1) is None
    assert safe_div(1, float("inf")) is None


def test_safe_pct_normal() -> None:
    assert safe_pct(1, 4) == 25.0


def test_safe_pct_zero_denom() -> None:
    assert safe_pct(5, 0) is None


def test_clamp_within_range() -> None:
    assert clamp(0.5, 0, 1) == 0.5


def test_clamp_below_range() -> None:
    assert clamp(-1, 0, 1) == 0


def test_clamp_above_range() -> None:
    assert clamp(2, 0, 1) == 1


def test_clamp_nan_returns_low() -> None:
    assert clamp(float("nan"), 0, 1) == 0


def test_percentile_known_values() -> None:
    xs = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
    assert percentile(xs, 0) == 1.0
    assert percentile(xs, 100) == 5.0
    assert percentile(xs, 50) == 3.0


def test_percentile_interpolates() -> None:
    xs = sorted([0.0, 10.0])
    assert percentile(xs, 50) == 5.0


def test_percentile_empty_returns_zero() -> None:
    assert percentile([], 50) == 0.0


def test_percentile_clamps_out_of_range_p() -> None:
    xs = sorted([1.0, 2.0, 3.0])
    # p > 100 should clamp to 100
    assert percentile(xs, 150) == 3.0
    # p < 0 should clamp to 0
    assert percentile(xs, -10) == 1.0


def test_mean_basic() -> None:
    assert mean([1.0, 2.0, 3.0]) == 2.0


def test_mean_empty() -> None:
    assert mean([]) == 0.0


def test_stddev_constant_zero() -> None:
    assert stddev([5.0, 5.0, 5.0]) == 0.0


def test_stddev_known() -> None:
    # Sample stddev of [1, 2, 3] = 1.0
    assert abs(stddev([1.0, 2.0, 3.0]) - 1.0) < 1e-9


def test_stddev_singleton_zero() -> None:
    assert stddev([1.0]) == 0.0


def test_stddev_population_vs_sample() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    pop = stddev(xs, sample=False)
    samp = stddev(xs, sample=True)
    assert samp > pop  # n-1 denominator is smaller → larger stddev
    assert abs(samp - math.sqrt(2.5)) < 1e-9
