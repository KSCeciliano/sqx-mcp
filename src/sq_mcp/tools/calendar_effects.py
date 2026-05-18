"""Calendar effects — when a strategy makes (or loses) its money.

Given a list of trades with timestamps and PnLs, this module breaks
performance down by:

- Hour of day (0-23)
- Day of week (Monday=0 .. Sunday=6)
- Month (1-12)
- Quarter (1-4)
- Day-vs-night (configurable hour ranges)

These slices reveal hidden patterns: a "balanced" strategy may turn
out to make 100% of its money on Tuesdays, or only during US session
hours. Knowing this lets you trim execution to the productive
windows.

Tools:

- ``calendar_by_hour`` — PnL aggregates per hour of day.
- ``calendar_by_dayofweek`` — PnL aggregates per weekday.
- ``calendar_by_month`` — PnL aggregates per calendar month.
- ``calendar_by_quarter`` — PnL aggregates per calendar quarter.
- ``calendar_session_split`` — split trades into named sessions
  (e.g. asia/europe/us) given hour-of-day cutoffs.

Each tool returns per-bucket: trades, wins, losses, sum, mean, best,
worst. Pure math / parsing — read-only.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

# ---- argument schemas ------------------------------------------------------


class TradeWithTime(BaseModel):
    when: str  # ISO timestamp
    pnl: float

    @field_validator("when")
    @classmethod
    def _v_when(cls, v: str) -> str:
        # We'll parse at use time; just sanity-check non-empty
        if not v.strip():
            raise ValueError("when must be non-empty ISO timestamp")
        return v

    @field_validator("pnl")
    @classmethod
    def _v_pnl(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError("pnl must be finite (no NaN / Inf)")
        return v


class CalendarArgs(BaseModel):
    trades: list[TradeWithTime] = Field(..., min_length=5, max_length=100_000)


class SessionDef(BaseModel):
    name: str
    start_hour: int = Field(..., ge=0, le=23)
    end_hour: int = Field(..., ge=0, le=23)  # exclusive


class SessionSplitArgs(BaseModel):
    trades: list[TradeWithTime] = Field(..., min_length=5, max_length=100_000)
    sessions: list[SessionDef] = Field(..., min_length=1, max_length=10)


# ---- helpers ---------------------------------------------------------------


def _parse_when(s: str) -> datetime | None:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _empty_bucket() -> dict[str, Any]:
    return {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "sum_pnl": 0.0,
        "best": None,
        "worst": None,
    }


def _accumulate(bucket: dict[str, Any], pnl: float) -> None:
    bucket["trades"] += 1
    bucket["sum_pnl"] += pnl
    if pnl > 0:
        bucket["wins"] += 1
    elif pnl < 0:
        bucket["losses"] += 1
    if bucket["best"] is None or pnl > bucket["best"]:
        bucket["best"] = pnl
    if bucket["worst"] is None or pnl < bucket["worst"]:
        bucket["worst"] = pnl


def _finalize(bucket: dict[str, Any]) -> dict[str, Any]:
    out = dict(bucket)
    if out["trades"] > 0:
        out["mean_pnl"] = round(out["sum_pnl"] / out["trades"], 6)
        out["win_rate"] = round(out["wins"] / out["trades"], 4)
    else:
        out["mean_pnl"] = None
        out["win_rate"] = None
    out["sum_pnl"] = round(out["sum_pnl"], 6)
    if out["best"] is not None:
        out["best"] = round(out["best"], 6)
    if out["worst"] is not None:
        out["worst"] = round(out["worst"], 6)
    return out


def _group_by(
    trades: list[dict[str, Any]], key_fn
) -> dict[Any, dict[str, Any]]:
    buckets: dict[Any, dict[str, Any]] = {}
    skipped = 0
    for t in trades:
        dt = _parse_when(t["when"])
        if dt is None:
            skipped += 1
            continue
        k = key_fn(dt)
        bucket = buckets.setdefault(k, _empty_bucket())
        _accumulate(bucket, t["pnl"])
    out = {k: _finalize(v) for k, v in buckets.items()}
    out["_skipped"] = skipped  # type: ignore[assignment]
    return out


def _hour_table(trades: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = _group_by(trades, lambda dt: dt.hour)
    skipped = grouped.pop("_skipped", 0)
    # Fill out 0-23 with empty buckets
    out: dict[int, dict[str, Any]] = {}
    for h in range(24):
        if h in grouped:
            out[h] = grouped[h]
        else:
            out[h] = _finalize(_empty_bucket())
    # Best hour by sum_pnl
    sorted_hrs = sorted(
        out.items(), key=lambda kv: kv[1]["sum_pnl"], reverse=True
    )
    best_hr = sorted_hrs[0][0] if sorted_hrs[0][1]["trades"] > 0 else None
    worst_hr = sorted_hrs[-1][0] if sorted_hrs[-1][1]["trades"] > 0 else None
    return {
        "by_hour": out,
        "best_hour": best_hr,
        "worst_hour": worst_hr,
        "skipped_unparseable": skipped,
    }


def _dayofweek_table(trades: list[dict[str, Any]]) -> dict[str, Any]:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    grouped = _group_by(trades, lambda dt: dt.weekday())
    skipped = grouped.pop("_skipped", 0)
    out: dict[str, dict[str, Any]] = {}
    for i, name in enumerate(names):
        out[name] = grouped.get(i, _finalize(_empty_bucket()))
    sorted_days = sorted(out.items(), key=lambda kv: kv[1]["sum_pnl"], reverse=True)
    best = sorted_days[0][0] if sorted_days[0][1]["trades"] > 0 else None
    worst = sorted_days[-1][0] if sorted_days[-1][1]["trades"] > 0 else None
    return {
        "by_day_of_week": out,
        "best_day": best,
        "worst_day": worst,
        "skipped_unparseable": skipped,
    }


def _month_table(trades: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = _group_by(trades, lambda dt: dt.month)
    skipped = grouped.pop("_skipped", 0)
    out: dict[int, dict[str, Any]] = {}
    for m in range(1, 13):
        out[m] = grouped.get(m, _finalize(_empty_bucket()))
    sorted_m = sorted(out.items(), key=lambda kv: kv[1]["sum_pnl"], reverse=True)
    best = sorted_m[0][0] if sorted_m[0][1]["trades"] > 0 else None
    worst = sorted_m[-1][0] if sorted_m[-1][1]["trades"] > 0 else None
    return {
        "by_month": out,
        "best_month": best,
        "worst_month": worst,
        "skipped_unparseable": skipped,
    }


def _quarter_table(trades: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = _group_by(trades, lambda dt: (dt.month - 1) // 3 + 1)
    skipped = grouped.pop("_skipped", 0)
    out: dict[int, dict[str, Any]] = {}
    for q in range(1, 5):
        out[q] = grouped.get(q, _finalize(_empty_bucket()))
    sorted_q = sorted(out.items(), key=lambda kv: kv[1]["sum_pnl"], reverse=True)
    best = sorted_q[0][0] if sorted_q[0][1]["trades"] > 0 else None
    worst = sorted_q[-1][0] if sorted_q[-1][1]["trades"] > 0 else None
    return {
        "by_quarter": out,
        "best_quarter": best,
        "worst_quarter": worst,
        "skipped_unparseable": skipped,
    }


def _hour_in_session(hour: int, start: int, end: int) -> bool:
    """Handles sessions that wrap midnight (start > end)."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Wraps midnight
    return hour >= start or hour < end


def _session_split(
    trades: list[dict[str, Any]], sessions: list[dict[str, Any]]
) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {s["name"]: _empty_bucket() for s in sessions}
    buckets["uncategorized"] = _empty_bucket()
    skipped = 0
    for t in trades:
        dt = _parse_when(t["when"])
        if dt is None:
            skipped += 1
            continue
        h = dt.hour
        matched = False
        for s in sessions:
            if _hour_in_session(h, s["start_hour"], s["end_hour"]):
                _accumulate(buckets[s["name"]], t["pnl"])
                matched = True
                break
        if not matched:
            _accumulate(buckets["uncategorized"], t["pnl"])

    out = {name: _finalize(b) for name, b in buckets.items()}
    sorted_s = sorted(out.items(), key=lambda kv: kv[1]["sum_pnl"], reverse=True)
    best = sorted_s[0][0] if sorted_s[0][1]["trades"] > 0 else None
    return {
        "by_session": out,
        "best_session": best,
        "skipped_unparseable": skipped,
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Aggregate trade PnLs by hour of day (0-23). Returns trades / wins "
            "/ losses / sum / mean / best / worst per hour, plus best+worst "
            "overall. Useful to find session-specific edge."
        )
    )
    async def calendar_by_hour(args: CalendarArgs) -> dict:
        return {"ok": True, **_hour_table([t.model_dump() for t in args.trades])}

    @mcp.tool(
        description=(
            "Aggregate trade PnLs by day of week (Mon-Sun). Useful to detect "
            "calendar bias (Monday momentum, Friday squaring up)."
        )
    )
    async def calendar_by_dayofweek(args: CalendarArgs) -> dict:
        return {
            "ok": True,
            **_dayofweek_table([t.model_dump() for t in args.trades]),
        }

    @mcp.tool(
        description=(
            "Aggregate trade PnLs by calendar month (1-12). Useful for "
            "seasonality / 'sell in May' style effects."
        )
    )
    async def calendar_by_month(args: CalendarArgs) -> dict:
        return {"ok": True, **_month_table([t.model_dump() for t in args.trades])}

    @mcp.tool(
        description=(
            "Aggregate trade PnLs by quarter (Q1-Q4). Useful for quarterly "
            "rebalancing analysis."
        )
    )
    async def calendar_by_quarter(args: CalendarArgs) -> dict:
        return {
            "ok": True,
            **_quarter_table([t.model_dump() for t in args.trades]),
        }

    @mcp.tool(
        description=(
            "Split trades into named sessions (asia/europe/us, or custom). Each "
            "session is (name, start_hour, end_hour). Sessions wrapping midnight "
            "are supported. Trades outside any session land in 'uncategorized'."
        )
    )
    async def calendar_session_split(args: SessionSplitArgs) -> dict:
        return {
            "ok": True,
            **_session_split(
                [t.model_dump() for t in args.trades],
                [s.model_dump() for s in args.sessions],
            ),
        }
