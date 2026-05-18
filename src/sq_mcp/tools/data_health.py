"""Data health checks and freshness gates.

Companion to symbol_intel — focuses on whether historical data is
usable RIGHT NOW for trading decisions: gaps, freshness, monotonicity,
bar count expectations.

Tools:

- ``data_health_gap_report`` — given a list of timestamps (epoch seconds),
  report the count and size of gaps relative to an expected bar interval.
- ``data_health_freshness_gate`` — pass/fail freshness gate: data must
  be no older than max_age_minutes.
- ``data_health_monotonicity`` — verify timestamps are strictly
  increasing; report anomalies.
- ``data_health_bar_count_check`` — expected vs actual bar count for a
  date window.
- ``workflow_data_health_check`` — end-to-end: combine the gates and
  report a single overall verdict.

Pure Python helpers; engine call only for the workflow that needs
symbol data on disk.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field


class GapReportArgs(BaseModel):
    timestamps_epoch: list[int] = Field(..., min_length=2, max_length=2_000_000)
    expected_interval_seconds: int = Field(..., ge=1, le=86_400 * 7)


class FreshnessGateArgs(BaseModel):
    last_bar_epoch: int = Field(..., ge=0)
    now_epoch: int = Field(..., ge=0)
    max_age_minutes: int = Field(60, ge=1, le=10_080)


class MonotonicityArgs(BaseModel):
    timestamps_epoch: list[int] = Field(..., min_length=2, max_length=2_000_000)


class BarCountCheckArgs(BaseModel):
    start_epoch: int = Field(..., ge=0)
    end_epoch: int = Field(..., ge=0)
    bar_interval_seconds: int = Field(..., ge=1, le=86_400 * 7)
    actual_count: int = Field(..., ge=0)
    market_hours_pct: float = Field(1.0, gt=0.0, le=1.0)


class WorkflowDataHealthArgs(BaseModel):
    symbol: str
    timeframe: str
    last_bar_epoch: int = Field(..., ge=0)
    now_epoch: int = Field(..., ge=0)
    n_bars: int = Field(..., ge=0)
    expected_interval_seconds: int = Field(..., ge=1, le=86_400 * 7)
    max_age_minutes: int = Field(60, ge=1, le=10_080)
    min_bars: int = Field(0, ge=0, le=10_000_000)
    n_gaps: int = Field(0, ge=0)


def _gap_report(timestamps: list[int], expected: int) -> dict[str, Any]:
    sorted_ts = sorted(timestamps)
    gaps: list[dict[str, int]] = []
    total_missing = 0
    for i in range(1, len(sorted_ts)):
        delta = sorted_ts[i] - sorted_ts[i - 1]
        if delta > expected * 1.5:
            missing = max(0, (delta // expected) - 1)
            total_missing += missing
            gaps.append({
                "after_epoch": sorted_ts[i - 1],
                "next_epoch": sorted_ts[i],
                "gap_seconds": delta,
                "missing_bars": missing,
            })
    return {
        "expected_interval_seconds": expected,
        "n_bars": len(sorted_ts),
        "n_gaps": len(gaps),
        "total_missing_bars": total_missing,
        "gap_pct_of_expected": round(
            100.0 * total_missing / (total_missing + len(sorted_ts) - 1), 4
        ) if len(sorted_ts) > 1 else 0.0,
        "first_epoch": sorted_ts[0],
        "last_epoch": sorted_ts[-1],
        "largest_gaps": sorted(gaps, key=lambda g: g["gap_seconds"], reverse=True)[:10],
    }


def _freshness_gate(
    last_bar_epoch: int, now_epoch: int, max_age_minutes: int
) -> dict[str, Any]:
    age_seconds = max(0, now_epoch - last_bar_epoch)
    age_minutes = age_seconds / 60.0
    passed = age_minutes <= max_age_minutes
    return {
        "passed": passed,
        "age_minutes": round(age_minutes, 2),
        "max_age_minutes": max_age_minutes,
        "last_bar_epoch": last_bar_epoch,
        "now_epoch": now_epoch,
        "verdict": "fresh" if passed else "stale",
    }


def _monotonicity(timestamps: list[int]) -> dict[str, Any]:
    anomalies: list[dict[str, int]] = []
    for i in range(1, len(timestamps)):
        if timestamps[i] <= timestamps[i - 1]:
            anomalies.append({
                "index": i,
                "prev_epoch": timestamps[i - 1],
                "this_epoch": timestamps[i],
            })
    return {
        "monotonic": len(anomalies) == 0,
        "n_anomalies": len(anomalies),
        "first_anomalies": anomalies[:10],
    }


def _bar_count_check(
    start_epoch: int,
    end_epoch: int,
    bar_interval_seconds: int,
    actual_count: int,
    market_hours_pct: float,
) -> dict[str, Any]:
    duration = max(0, end_epoch - start_epoch)
    expected_full = duration / bar_interval_seconds
    expected_market = expected_full * market_hours_pct
    coverage = (actual_count / expected_market * 100.0) if expected_market > 0 else 0.0
    return {
        "actual_bars": actual_count,
        "expected_bars": int(expected_market),
        "coverage_pct": round(coverage, 4),
        "duration_seconds": duration,
        "market_hours_pct": market_hours_pct,
        "verdict": (
            "complete" if coverage >= 99.0
            else "good" if coverage >= 95.0
            else "patchy" if coverage >= 80.0
            else "sparse"
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Detect gaps in a timestamp series relative to an expected bar "
            "interval. Reports gap count, missing bar count, and the 10 "
            "largest gaps. Caller supplies timestamps as epoch seconds and the "
            "expected interval in seconds (e.g. 60 for M1, 3600 for H1). "
            "Read-only."
        )
    )
    async def data_health_gap_report(args: GapReportArgs) -> dict:
        return {
            "ok": True,
            **_gap_report(args.timestamps_epoch, args.expected_interval_seconds),
        }

    @mcp.tool(
        description=(
            "Freshness gate: pass/fail based on whether the most recent bar is "
            "newer than max_age_minutes. Use as a pre-trade check or before "
            "running a fresh-data backtest."
        )
    )
    async def data_health_freshness_gate(args: FreshnessGateArgs) -> dict:
        return {
            "ok": True,
            **_freshness_gate(
                args.last_bar_epoch, args.now_epoch, args.max_age_minutes
            ),
        }

    @mcp.tool(
        description=(
            "Verify timestamps are strictly increasing — flags duplicates and "
            "out-of-order entries. Returns the index of the first 10 anomalies."
        )
    )
    async def data_health_monotonicity(args: MonotonicityArgs) -> dict:
        return {"ok": True, **_monotonicity(args.timestamps_epoch)}

    @mcp.tool(
        description=(
            "Compare actual bar count to expected over a date window. Accounts "
            "for market hours (e.g. forex ~24/5 → market_hours_pct=0.714). "
            "Returns coverage_pct + verdict (complete/good/patchy/sparse)."
        )
    )
    async def data_health_bar_count_check(args: BarCountCheckArgs) -> dict:
        return {
            "ok": True,
            **_bar_count_check(
                args.start_epoch,
                args.end_epoch,
                args.bar_interval_seconds,
                args.actual_count,
                args.market_hours_pct,
            ),
        }

    @mcp.tool(
        description=(
            "End-to-end data health workflow: apply freshness + bar-count + "
            "gap-count gates to a symbol+timeframe's caller-provided data "
            "snapshot. Returns a single overall verdict (healthy / stale / "
            "patchy / missing). Read-only — does NOT trigger a data refresh."
        )
    )
    async def workflow_data_health_check(args: WorkflowDataHealthArgs) -> dict:
        gates: dict[str, Any] = {}
        fresh = _freshness_gate(args.last_bar_epoch, args.now_epoch, args.max_age_minutes)
        gates["freshness"] = fresh
        bars_ok = args.n_bars >= args.min_bars if args.min_bars > 0 else True
        gates["bars"] = {"passed": bars_ok, "n_bars": args.n_bars, "min_bars": args.min_bars}
        gaps_ok = args.n_gaps == 0
        gates["gaps"] = {"passed": gaps_ok, "n_gaps": args.n_gaps}
        if not fresh["passed"]:
            verdict = "stale"
        elif not bars_ok:
            verdict = "missing"
        elif not gaps_ok:
            verdict = "patchy"
        else:
            verdict = "healthy"
        return {
            "ok": True,
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "verdict": verdict,
            "gates": gates,
        }


__all__ = [
    "BarCountCheckArgs",
    "FreshnessGateArgs",
    "GapReportArgs",
    "MonotonicityArgs",
    "WorkflowDataHealthArgs",
    "_bar_count_check",
    "_freshness_gate",
    "_gap_report",
    "_monotonicity",
    "register",
]
