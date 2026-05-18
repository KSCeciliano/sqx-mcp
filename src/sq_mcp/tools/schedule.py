"""Backtest cadence recommendations.

When should a strategy be re-tested or rebuilt? Some signals:

- The data underneath has been refreshed (Binance pulled new bars,
  broker rolled the contract). If the .dat mtime is more recent than
  the strategy's HistoryTo, the backtest is stale.
- The strategy has been live long enough to accumulate live trades. A
  monthly re-test against the latest data is healthier than waiting
  for catastrophic drift.
- Market regime has shifted (volatility doubled, trend ended). The
  user typically notices this in the news; this module just provides
  a heuristic "due for re-test" timer.

Tools:

- ``schedule_for_strategy`` — single-strategy recommendation: when was
  last rebuilt vs when the underlying data was last updated? What's
  the recommended next action (no-op, retest, rebuild)?
- ``schedule_for_workspace`` — apply schedule_for_strategy across
  every (project, strategy) pair the agent knows about; return a
  ranked queue.
- ``schedule_quarterly_calendar`` — produce a Q1/Q2/Q3/Q4 calendar of
  recommended actions for a user portfolio (used in annual planning).

Recommendations are heuristic — they don't run anything, just report
what the agent should consider doing next.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# ---- argument schemas ------------------------------------------------------


class ScheduleStrategyArgs(BaseModel):
    strategy_built_at: str = Field(..., min_length=10)  # ISO timestamp
    data_updated_at: str = Field(..., min_length=10)
    live_days_since_deploy: int | None = Field(None, ge=0)
    drift_verdict: str | None = None  # green / yellow / red from drift module


class ScheduleWorkspaceArgs(BaseModel):
    items: list[dict[str, Any]] = Field(..., min_length=1, max_length=200)


class QuarterlyCalendarArgs(BaseModel):
    portfolio_size: int = Field(..., ge=1, le=200)
    asset_class: str = "crypto"  # crypto | forex | futures | equities


# ---- helpers ---------------------------------------------------------------


def _parse_iso(s: str) -> datetime | None:
    try:
        # Permissive — accept yyyy-mm-dd or full ISO
        if len(s) == 10:
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _days_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 86400.0


def _schedule_recommendation(args: ScheduleStrategyArgs) -> dict[str, Any]:
    built = _parse_iso(args.strategy_built_at)
    updated = _parse_iso(args.data_updated_at)
    if built is None or updated is None:
        return {
            "ok": False,
            "error": "could not parse strategy_built_at or data_updated_at",
        }
    now = datetime.now(timezone.utc)
    days_since_build = _days_between(built, now)
    data_after_build_days = _days_between(built, updated)

    # Score each signal
    reasons: list[str] = []
    action = "no_action"

    if args.drift_verdict == "red":
        action = "rebuild"
        reasons.append("live drift detector returned red")
    elif args.drift_verdict == "yellow":
        if action == "no_action":
            action = "retest"
        reasons.append("live drift detector returned yellow")

    if data_after_build_days > 30:
        # Data has new bars since the build — at minimum, re-test
        if action == "no_action":
            action = "retest"
        reasons.append(
            f"data has {data_after_build_days:.0f} days of new bars since "
            f"strategy was built"
        )
    if data_after_build_days > 180:
        action = "rebuild"
        reasons.append(
            f"data updated {data_after_build_days:.0f} days after build — "
            "rebuild on fresh history"
        )

    if days_since_build > 365 and action == "no_action":
        action = "retest"
        reasons.append("strategy is >1 year old — annual retest recommended")

    if (
        args.live_days_since_deploy is not None
        and args.live_days_since_deploy > 90
        and action == "no_action"
    ):
        action = "retest"
        reasons.append("strategy has been live > 90 days — quarterly retest")

    if not reasons:
        reasons.append("no triggers fired — leave as is")

    return {
        "ok": True,
        "action": action,
        "days_since_build": round(days_since_build, 1),
        "data_lag_days": round(data_after_build_days, 1),
        "reasons": reasons,
        "interpretation": {
            "no_action": "strategy fresh enough; revisit in 30 days",
            "retest": "re-run a backtest on current data (cheap, ~minutes)",
            "rebuild": "full Builder re-run (expensive — confirm trial-license budget)",
        }[action],
    }


def _workspace_schedule(items: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for it in items:
        try:
            args = ScheduleStrategyArgs(**it)
        except (ValueError, TypeError) as exc:
            rows.append({"input": it, "ok": False, "error": str(exc)})
            continue
        rec = _schedule_recommendation(args)
        if rec["ok"]:
            rows.append({**it, **rec})
        else:
            rows.append({**it, **rec})
    # Sort: rebuild → retest → no_action
    rank = {"rebuild": 0, "retest": 1, "no_action": 2}
    rows.sort(key=lambda r: rank.get(r.get("action", "no_action"), 99))
    summary = {
        "rebuild_count": sum(1 for r in rows if r.get("action") == "rebuild"),
        "retest_count": sum(1 for r in rows if r.get("action") == "retest"),
        "no_action_count": sum(1 for r in rows if r.get("action") == "no_action"),
    }
    return {"summary": summary, "queue": rows}


def _quarterly_calendar(portfolio_size: int, asset_class: str) -> dict[str, Any]:
    """Produce a yearly cadence calendar for the portfolio.

    The recommendations are tier-specific: crypto needs more frequent retests
    because regime changes happen monthly; equities can wait a quarter or two.
    """
    asset_class = asset_class.lower()
    if asset_class == "crypto":
        cadence = {
            "Q1": ["Annual rebuild of every strategy", "Data freshness audit", "Drift report"],
            "Q2": ["Monthly retest", "Mid-year drift report", "Cull bottom 20% by drift"],
            "Q3": ["Quarterly retest", "Re-evaluate symbol set"],
            "Q4": ["Quarterly retest", "Annual portfolio review", "Year-end snapshot"],
        }
    elif asset_class == "forex":
        cadence = {
            "Q1": ["Annual rebuild on 5-year window", "Broker spread audit"],
            "Q2": ["Quarterly retest"],
            "Q3": ["Quarterly retest", "Walk-forward refresh"],
            "Q4": ["Quarterly retest", "Annual portfolio review"],
        }
    elif asset_class in {"futures", "equities"}:
        cadence = {
            "Q1": ["Annual rebuild", "Contract roll audit (futures)"],
            "Q2": ["Bi-quarterly retest"],
            "Q3": ["Walk-forward refresh"],
            "Q4": ["Quarterly retest", "Annual review"],
        }
    else:
        cadence = {
            "Q1": ["Annual rebuild"],
            "Q2": ["Mid-year retest"],
            "Q3": ["Quarterly retest"],
            "Q4": ["Annual review"],
        }
    note = (
        "small portfolio — consider running ALL strategies through every step"
        if portfolio_size <= 5
        else "large portfolio — batch by tag or symbol"
    )
    return {
        "portfolio_size": portfolio_size,
        "asset_class": asset_class,
        "cadence": cadence,
        "note": note,
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Recommend what to do with a single strategy: no_action / retest / "
            "rebuild. Inputs: when the strategy was built, when underlying data "
            "was last updated, optionally days-live and a drift verdict. Returns "
            "the recommended action + reasons. Pure heuristic."
        )
    )
    async def schedule_for_strategy(args: ScheduleStrategyArgs) -> dict:
        return _schedule_recommendation(args)

    @mcp.tool(
        description=(
            "Apply schedule_for_strategy across a list of strategy items the "
            "caller supplies (each item must have strategy_built_at, "
            "data_updated_at, etc.). Returns a ranked queue (rebuild first) and "
            "a counts summary."
        )
    )
    async def schedule_for_workspace(args: ScheduleWorkspaceArgs) -> dict:
        return {"ok": True, **_workspace_schedule(args.items)}

    @mcp.tool(
        description=(
            "Yearly cadence calendar for a portfolio: Q1/Q2/Q3/Q4 actions tuned "
            "for asset_class (crypto needs frequent retests, equities less so). "
            "Returns markdown-paste-ready list of actions per quarter."
        )
    )
    async def schedule_quarterly_calendar(args: QuarterlyCalendarArgs) -> dict:
        return {"ok": True, **_quarterly_calendar(args.portfolio_size, args.asset_class)}
