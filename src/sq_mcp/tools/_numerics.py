"""Shared numeric helpers and adversarial-input guards.

Every tool that does math on a caller-supplied list of floats SHOULD route the
input through one of these helpers. They reject the silent failure modes
that have bitten us in the past:

- ``NaN`` and ``±Inf`` propagate through math without raising. Sorted lists
  containing NaN are *not* ordered. Comparisons against NaN return False, so
  a NaN trade can defeat every gate that expects a finite number.
- Empty / single-element series leak past schema validation when ``min_length``
  is set on the wrong field.
- Extreme magnitudes (1e308) cause overflow on multiplication; constant
  series produce zero variance and trip every Sharpe / Sortino / correlation.

Use ``validate_finite_floats`` at the helper boundary; use ``safe_div`` and
``safe_pct`` for ratios that should report None on undefined math.
"""

from __future__ import annotations

import math
from typing import Any


class NumericValidationError(ValueError):
    """Raised when a numeric input is non-finite (NaN/Inf) or otherwise unusable."""


def is_finite(x: float) -> bool:
    return isinstance(x, (int, float)) and not (math.isnan(x) or math.isinf(x))


def validate_finite_floats(
    xs: list[float], *, name: str = "values", min_length: int | None = None
) -> list[float]:
    """Reject NaN/Inf in a float list. Return the list unchanged on success.

    Use at the top of every pure-math helper that accepts a caller-supplied
    float series. Pydantic schemas with `min_length` already cover length;
    this is the value-level check Pydantic cannot do safely without a custom
    validator on every model.
    """
    if not isinstance(xs, list):
        raise NumericValidationError(f"{name} must be a list, got {type(xs).__name__}")
    if min_length is not None and len(xs) < min_length:
        raise NumericValidationError(
            f"{name} must have at least {min_length} elements; have {len(xs)}"
        )
    bad: list[tuple[int, Any]] = []
    for i, x in enumerate(xs):
        if not is_finite(x):
            bad.append((i, x))
            if len(bad) >= 5:
                break
    if bad:
        sample = ", ".join(f"[{i}]={v!r}" for i, v in bad)
        raise NumericValidationError(
            f"{name} contains non-finite values (NaN/Inf): {sample}"
        )
    return xs


def safe_div(num: float, denom: float, *, default: Any = None) -> Any:
    """Divide num by denom; return `default` if denom is zero or non-finite,
    or if the result itself would be NaN/Inf.
    """
    if not is_finite(num) or not is_finite(denom) or denom == 0:
        return default
    result = num / denom
    return result if is_finite(result) else default


def safe_pct(num: float, denom: float, *, default: Any = None) -> Any:
    """Like safe_div but multiplies the ratio by 100."""
    r = safe_div(num, denom, default=default)
    return r if r is None or r is default else r * 100.0


def clamp(x: float, lo: float, hi: float) -> float:
    """Bound x to [lo, hi]. Returns x unchanged if already in range."""
    if not is_finite(x):
        return lo
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def percentile(sorted_xs: list[float], p: float) -> float:
    """Linear-interpolation percentile from a pre-sorted list.

    Centralized here because tail_risk, montecarlo, stats_extra all needed
    the same routine and the original copies drifted slightly.
    """
    if not sorted_xs:
        return 0.0
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    p = clamp(p, 0.0, 100.0)
    idx = (len(sorted_xs) - 1) * (p / 100.0)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (idx - lo)


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stddev(xs: list[float], *, sample: bool = True) -> float:
    """Population (sample=False) or sample (sample=True) stddev. Returns 0
    for length-0 or length-1 input.
    """
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    denom = (n - 1) if sample else n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / denom)


__all__ = [
    "NumericValidationError",
    "clamp",
    "is_finite",
    "mean",
    "percentile",
    "safe_div",
    "safe_pct",
    "stddev",
    "validate_finite_floats",
]
