"""Parameter sensitivity helpers.

Quantify how sensitive a strategy's metric is to a parameter perturbation.
A robust strategy's metrics change gradually as a parameter moves; a
fragile strategy "falls off a cliff" once you nudge a parameter slightly.

The actual backtest re-runs happen elsewhere (SQ X's Optimizer is the
right tool). These helpers process the resulting data: a metric value
at each parameter point, plus a "baseline" value.

Tools:

- ``sensitivity_local_slope`` — given parameter values + corresponding
  metrics, estimate the local slope (first derivative) of metric vs
  parameter around a baseline value. Large |slope| ⇒ sensitive.
- ``sensitivity_plateau_score`` — score how flat (= robust) the metric
  curve is across the tested range. 100 = perfectly flat; 0 = wild
  swings.
- ``sensitivity_range_decay`` — measure how the metric degrades as you
  move away from the baseline (asymmetric).
- ``sensitivity_compare_two_params`` — given two parameter sweeps,
  identify which parameter the strategy is more sensitive to.
- ``sensitivity_bullseye`` — find the best parameter value in a sweep
  (most positive metric) and the values within ``tolerance_pct`` of
  that best — the "plateau of safety".
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._common import safe_error_payload
from sq_mcp.tools._numerics import NumericValidationError, is_finite

# ---- argument schemas ------------------------------------------------------


class SensitivityPoint(BaseModel):
    parameter_value: float
    metric: float


class SensitivityLocalSlopeArgs(BaseModel):
    points: list[SensitivityPoint] = Field(..., min_length=3, max_length=200)
    baseline_value: float


class SensitivityPlateauArgs(BaseModel):
    points: list[SensitivityPoint] = Field(..., min_length=3, max_length=200)


class SensitivityRangeDecayArgs(BaseModel):
    points: list[SensitivityPoint] = Field(..., min_length=5, max_length=200)
    baseline_value: float
    tolerance_pct: float = Field(10.0, gt=0, le=100)


class SensitivityCompareArgs(BaseModel):
    param_a: list[SensitivityPoint] = Field(..., min_length=3, max_length=200)
    param_b: list[SensitivityPoint] = Field(..., min_length=3, max_length=200)
    param_a_name: str = "A"
    param_b_name: str = "B"


class SensitivityBullseyeArgs(BaseModel):
    points: list[SensitivityPoint] = Field(..., min_length=3, max_length=200)
    tolerance_pct: float = Field(10.0, gt=0, le=100)


# ---- helpers ---------------------------------------------------------------


def _validate_points(pts: list[dict[str, Any]], *, name: str = "points") -> list[dict[str, Any]]:
    """Reject NaN/Inf parameter_value or metric in a SensitivityPoint list."""
    if not pts:
        raise NumericValidationError(f"{name} must be non-empty")
    bad: list[str] = []
    for i, p in enumerate(pts):
        pv = p.get("parameter_value")
        mt = p.get("metric")
        if not is_finite(pv):
            bad.append(f"{name}[{i}].parameter_value={pv!r}")
        if not is_finite(mt):
            bad.append(f"{name}[{i}].metric={mt!r}")
        if len(bad) >= 5:
            break
    if bad:
        raise NumericValidationError(
            f"non-finite (NaN/Inf) values: {', '.join(bad)}"
        )
    return pts


def _validate_scalar(x: float, name: str) -> float:
    if not is_finite(x):
        raise NumericValidationError(f"{name}={x!r} is not finite")
    return x


def _sort_points(pts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(pts, key=lambda p: p["parameter_value"])


def _interp_metric(pts_sorted: list[dict[str, Any]], at: float) -> float | None:
    """Linear interpolation of metric at parameter_value=at."""
    if not pts_sorted:
        return None
    if at <= pts_sorted[0]["parameter_value"]:
        return pts_sorted[0]["metric"]
    if at >= pts_sorted[-1]["parameter_value"]:
        return pts_sorted[-1]["metric"]
    for i in range(len(pts_sorted) - 1):
        x0 = pts_sorted[i]["parameter_value"]
        x1 = pts_sorted[i + 1]["parameter_value"]
        if x0 <= at <= x1:
            y0 = pts_sorted[i]["metric"]
            y1 = pts_sorted[i + 1]["metric"]
            if x1 == x0:
                return y0
            t = (at - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return None


def _local_slope(pts: list[dict[str, Any]], baseline: float) -> dict[str, Any]:
    _validate_points(pts)
    _validate_scalar(baseline, "baseline_value")
    pts_sorted = _sort_points(pts)
    # Find left and right neighbors of baseline
    left = None
    right = None
    for p in pts_sorted:
        if p["parameter_value"] < baseline:
            left = p
        elif p["parameter_value"] > baseline and right is None:
            right = p
    if left is None or right is None:
        # Edge: pick two closest neighbors
        if len(pts_sorted) >= 2:
            left, right = pts_sorted[0], pts_sorted[-1]
        else:
            return {"slope": None, "note": "need at least 2 distinct parameter values"}
    dx = right["parameter_value"] - left["parameter_value"]
    if dx == 0:
        return {"slope": None, "note": "left and right neighbors have identical parameter values"}
    slope = (right["metric"] - left["metric"]) / dx
    baseline_metric = _interp_metric(pts_sorted, baseline)
    return {
        "slope": round(slope, 6),
        "left_value": left["parameter_value"],
        "right_value": right["parameter_value"],
        "baseline_metric_estimate": round(baseline_metric, 6) if baseline_metric is not None else None,
        "interpretation": (
            "low sensitivity"
            if abs(slope) < 0.01
            else "moderate sensitivity"
            if abs(slope) < 1.0
            else "high sensitivity"
        ),
    }


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _plateau_score(pts: list[dict[str, Any]]) -> dict[str, Any]:
    _validate_points(pts)
    metrics = [p["metric"] for p in pts]
    if not metrics:
        return {"score": 0}
    mean = sum(metrics) / len(metrics)
    std = _std(metrics)
    if abs(mean) < 1e-9:
        return {"score": 0, "note": "metric mean is zero — no signal"}
    cv = std / abs(mean)  # coefficient of variation
    # CV near 0 = flat → score 100; CV >= 1 → score 0
    score = max(0.0, min(100.0, 100.0 * (1.0 - cv)))
    verdict = (
        "robust" if score >= 75 else "borderline" if score >= 50 else "fragile"
    )
    return {
        "score": round(score, 1),
        "coefficient_of_variation": round(cv, 4),
        "mean_metric": round(mean, 6),
        "std_metric": round(std, 6),
        "verdict": verdict,
    }


def _range_decay(
    pts: list[dict[str, Any]], baseline: float, tolerance_pct: float
) -> dict[str, Any]:
    _validate_points(pts)
    _validate_scalar(baseline, "baseline_value")
    _validate_scalar(tolerance_pct, "tolerance_pct")
    pts_sorted = _sort_points(pts)
    baseline_metric = _interp_metric(pts_sorted, baseline)
    if baseline_metric is None or baseline_metric == 0:
        return {"verdict": "no_baseline", "note": "could not establish baseline metric"}
    threshold = baseline_metric * (1.0 - tolerance_pct / 100.0)
    # Walk left from baseline: find first param_value where metric < threshold
    left_break = None
    for p in reversed([q for q in pts_sorted if q["parameter_value"] < baseline]):
        if p["metric"] < threshold:
            left_break = p["parameter_value"]
            break
    # Walk right
    right_break = None
    for p in [q for q in pts_sorted if q["parameter_value"] > baseline]:
        if p["metric"] < threshold:
            right_break = p["parameter_value"]
            break
    return {
        "baseline_metric": round(baseline_metric, 6),
        "threshold_metric": round(threshold, 6),
        "left_break_value": left_break,
        "right_break_value": right_break,
        "verdict": (
            "wide_plateau"
            if left_break is None and right_break is None
            else "narrow_plateau"
            if left_break is not None and right_break is not None
            else "asymmetric_decay"
        ),
    }


def _compare_two(
    a: list[dict[str, Any]], b: list[dict[str, Any]], a_name: str, b_name: str
) -> dict[str, Any]:
    _validate_points(a, name="param_a")
    _validate_points(b, name="param_b")
    a_score = _plateau_score(a)
    b_score = _plateau_score(b)
    most_sensitive = (
        a_name
        if a_score["score"] < b_score["score"]
        else b_name
        if b_score["score"] < a_score["score"]
        else "tie"
    )
    return {
        "most_sensitive": most_sensitive,
        a_name: a_score,
        b_name: b_score,
    }


def _bullseye(
    pts: list[dict[str, Any]], tolerance_pct: float
) -> dict[str, Any]:
    if not pts:
        return {"verdict": "no_points"}
    _validate_points(pts)
    _validate_scalar(tolerance_pct, "tolerance_pct")
    best = max(pts, key=lambda p: p["metric"])
    threshold = best["metric"] * (1.0 - tolerance_pct / 100.0)
    within = [p for p in pts if p["metric"] >= threshold]
    within.sort(key=lambda p: p["parameter_value"])
    return {
        "best_parameter_value": best["parameter_value"],
        "best_metric": round(best["metric"], 6),
        "tolerance_pct": tolerance_pct,
        "n_within_tolerance": len(within),
        "parameter_value_range_within_tolerance": (
            (within[0]["parameter_value"], within[-1]["parameter_value"])
            if within
            else None
        ),
        "verdict": (
            "wide_safety_plateau"
            if len(within) >= 3
            else "narrow_safety_plateau"
            if len(within) >= 2
            else "single_point_optimum"
        ),
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Estimate local slope of metric vs parameter around a baseline "
            "value. Uses the nearest neighbors on each side. Large |slope| "
            "indicates high sensitivity. Pure math."
        )
    )
    async def sensitivity_local_slope(args: SensitivityLocalSlopeArgs) -> dict:
        try:
            return {
                "ok": True,
                **_local_slope([p.model_dump() for p in args.points], args.baseline_value),
            }
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Plateau (robustness) score from a parameter sweep. 0-100, higher "
            "= flatter metric curve (less sensitive to parameter changes). "
            "Uses coefficient of variation."
        )
    )
    async def sensitivity_plateau_score(args: SensitivityPlateauArgs) -> dict:
        try:
            return {"ok": True, **_plateau_score([p.model_dump() for p in args.points])}
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Find the parameter values on each side of the baseline at which "
            "the metric falls below baseline × (1 - tolerance_pct). Tells you "
            "how far you can drift from baseline before performance degrades."
        )
    )
    async def sensitivity_range_decay(args: SensitivityRangeDecayArgs) -> dict:
        try:
            return {
                "ok": True,
                **_range_decay(
                    [p.model_dump() for p in args.points],
                    args.baseline_value,
                    args.tolerance_pct,
                ),
            }
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compare sensitivity of two parameters. Returns plateau scores "
            "and which parameter the strategy is more sensitive to."
        )
    )
    async def sensitivity_compare_two_params(args: SensitivityCompareArgs) -> dict:
        try:
            return {
                "ok": True,
                **_compare_two(
                    [p.model_dump() for p in args.param_a],
                    [p.model_dump() for p in args.param_b],
                    args.param_a_name,
                    args.param_b_name,
                ),
            }
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Find best parameter value (highest metric) in a sweep, plus the "
            "range of parameter values within tolerance_pct of that best. "
            "Identifies a 'safety plateau' you can pick from."
        )
    )
    async def sensitivity_bullseye(args: SensitivityBullseyeArgs) -> dict:
        try:
            return {
                "ok": True,
                **_bullseye([p.model_dump() for p in args.points], args.tolerance_pct),
            }
        except NumericValidationError as exc:
            return safe_error_payload(exc)
