"""Drawdown analysis: duration, recovery time, distribution.

Max drawdown alone hides the *shape* of underwater periods. A 20%
drawdown that recovers in 3 weeks is very different from a 20% drawdown
that takes 18 months to recover. These tools surface those details.

Tools:

- ``drawdown_periods`` — segment the equity curve into drawdown episodes;
  each episode reports start/end indices, depth, duration, recovery time.
- ``drawdown_time_underwater`` — % of the curve spent below a previous
  peak (any drawdown, regardless of depth).
- ``drawdown_recovery_curve`` — recovery-time distribution; how long
  drawdowns of various depths take to recover, on average.
- ``drawdown_pain_decomposition`` — total pain (area under the
  underwater curve) and its share by depth bucket.

Pure Python.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)


class EquityCurveArgs(BaseModel):
    equity_curve: list[float] = Field(..., min_length=10, max_length=500_000)


class DrawdownDecomposeArgs(EquityCurveArgs):
    depth_buckets_pct: list[float] = Field(
        default_factory=lambda: [5.0, 10.0, 20.0, 50.0],
        min_length=1,
        max_length=20,
    )


def _drawdown_periods(curve: list[float]) -> dict[str, Any]:
    validate_finite_floats(curve, name="equity_curve")
    if len(curve) < 2:
        return {"periods": [], "n": 0}
    periods: list[dict[str, Any]] = []
    peak = curve[0]
    peak_idx = 0
    in_drawdown = False
    trough = curve[0]
    trough_idx = 0
    dd_start_idx = 0
    for i in range(1, len(curve)):
        v = curve[i]
        if v >= peak:
            if in_drawdown:
                depth = (peak - trough) / peak * 100.0 if peak > 0 else 0.0
                periods.append({
                    "start_index": dd_start_idx,
                    "peak_index": peak_idx,
                    "trough_index": trough_idx,
                    "recovery_index": i,
                    "depth_pct": round(depth, 6),
                    "duration_bars": i - dd_start_idx,
                    "drop_bars": trough_idx - dd_start_idx,
                    "recovery_bars": i - trough_idx,
                })
                in_drawdown = False
            peak = v
            peak_idx = i
            trough = v
            trough_idx = i
        else:
            if not in_drawdown:
                in_drawdown = True
                dd_start_idx = peak_idx
            if v < trough:
                trough = v
                trough_idx = i
    if in_drawdown:
        depth = (peak - trough) / peak * 100.0 if peak > 0 else 0.0
        periods.append({
            "start_index": dd_start_idx,
            "peak_index": peak_idx,
            "trough_index": trough_idx,
            "recovery_index": None,
            "depth_pct": round(depth, 6),
            "duration_bars": len(curve) - dd_start_idx,
            "drop_bars": trough_idx - dd_start_idx,
            "recovery_bars": None,
            "note": "ongoing — never recovered to peak",
        })
    if not periods:
        return {"periods": [], "n": 0}
    deepest = max(periods, key=lambda p: p["depth_pct"])
    longest = max(periods, key=lambda p: p["duration_bars"])
    return {
        "periods": periods,
        "n": len(periods),
        "deepest": deepest,
        "longest": longest,
        "mean_depth_pct": round(sum(p["depth_pct"] for p in periods) / len(periods), 6),
        "mean_duration_bars": round(sum(p["duration_bars"] for p in periods) / len(periods), 4),
    }


def _time_underwater(curve: list[float]) -> dict[str, Any]:
    validate_finite_floats(curve, name="equity_curve")
    n = len(curve)
    peak = curve[0]
    under = 0
    for v in curve:
        if v > peak:
            peak = v
        if v < peak:
            under += 1
    return {
        "n_bars": n,
        "n_underwater_bars": under,
        "underwater_share_pct": round(100.0 * under / n, 4),
        "interpretation": (
            "rarely_underwater" if under / n < 0.2
            else "moderately_underwater" if under / n < 0.5
            else "often_underwater" if under / n < 0.8
            else "almost_always_underwater"
        ),
    }


def _recovery_distribution(curve: list[float]) -> dict[str, Any]:
    r = _drawdown_periods(curve)
    periods = r.get("periods", [])
    closed = [p for p in periods if p.get("recovery_bars") is not None]
    if not closed:
        return {"n": 0, "note": "no recovered drawdown periods"}
    durations = [p["duration_bars"] for p in closed]
    recoveries = [p["recovery_bars"] for p in closed]
    return {
        "n_recovered": len(closed),
        "n_total": len(periods),
        "mean_duration_bars": round(sum(durations) / len(durations), 4),
        "max_duration_bars": max(durations),
        "mean_recovery_bars": round(sum(recoveries) / len(recoveries), 4),
        "max_recovery_bars": max(recoveries),
        "ongoing_drawdown": len(periods) - len(closed),
    }


def _pain_decomposition(
    curve: list[float], buckets_pct: list[float]
) -> dict[str, Any]:
    validate_finite_floats(curve, name="equity_curve")
    buckets = sorted(buckets_pct)
    peak = curve[0]
    total_pain = 0.0
    by_bucket = dict.fromkeys(buckets, 0.0)
    by_bucket_plus = {f">={b}%": 0.0 for b in buckets}
    bars_at_depth = dict.fromkeys(buckets, 0)
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd_pct = (peak - v) / peak * 100.0
            total_pain += dd_pct
            # Allocate to the smallest bucket whose threshold the dd exceeds
            for b in reversed(buckets):
                if dd_pct >= b:
                    by_bucket[b] += dd_pct
                    bars_at_depth[b] += 1
                    by_bucket_plus[f">={b}%"] += dd_pct
                    break
    return {
        "total_pain": round(total_pain, 6),
        "n_bars": len(curve),
        "mean_pain_per_bar": round(total_pain / len(curve), 6),
        "buckets_pct": buckets,
        "pain_by_bucket": {f">={b}%": round(by_bucket_plus[f">={b}%"], 6) for b in buckets},
        "bars_in_bucket": bars_at_depth,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Segment an equity curve into drawdown episodes. Each episode "
            "reports start / peak / trough / recovery indices, depth %, "
            "duration, drop bars, recovery bars. Also reports the deepest "
            "and longest drawdown across the curve."
        )
    )
    async def drawdown_periods(args: EquityCurveArgs) -> dict:
        try:
            return {"ok": True, **_drawdown_periods(args.equity_curve)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Time underwater: fraction of bars below the running peak "
            "(regardless of depth). Verdict: rarely / moderately / often / "
            "almost_always_underwater."
        )
    )
    async def drawdown_time_underwater(args: EquityCurveArgs) -> dict:
        try:
            return {"ok": True, **_time_underwater(args.equity_curve)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Recovery-time distribution across drawdown episodes. Reports "
            "mean/max duration and recovery bars + count of ongoing "
            "(unrecovered) drawdowns."
        )
    )
    async def drawdown_recovery_curve(args: EquityCurveArgs) -> dict:
        try:
            return {"ok": True, **_recovery_distribution(args.equity_curve)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Decompose total pain (area under the underwater curve) by "
            "depth buckets. Tells you which DD severity contributed most "
            "to the cumulative pain — a strategy that spent 10% of time at "
            "−5% accumulates more pain than one that spent 0.1% at −50%."
        )
    )
    async def drawdown_pain_decomposition(args: DrawdownDecomposeArgs) -> dict:
        try:
            return {
                "ok": True,
                **_pain_decomposition(args.equity_curve, args.depth_buckets_pct),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "DrawdownDecomposeArgs",
    "EquityCurveArgs",
    "_drawdown_periods",
    "_pain_decomposition",
    "_recovery_distribution",
    "_time_underwater",
    "register",
]
