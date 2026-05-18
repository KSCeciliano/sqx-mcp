"""High-level workflow orchestrators.

The leaf tools are powerful but require careful composition. These
workflow tools chain the most common multi-step routines into single
calls so the agent doesn't have to wire them up each time:

- ``workflow_morning_briefing`` — dashboard + active alerts + schedule
  recommendations for the workspace. Returns a single Markdown report
  the user can read at the start of a session.
- ``workflow_promote_strategy`` — full promotion check (audit + brittle
  + stress + drift inputs) followed by the ship pipeline. One call
  takes a strategy from "candidate" to "promoted or blocked with
  reasons".
- ``workflow_data_health_check`` — full data integrity + freshness
  audit; returns the action list.
- ``workflow_compare_two_strategies`` — chains hypothesis tests +
  benchmark + brittle/stress side-by-side comparison.

All output is structured + Markdown-renderable. Pure orchestration —
no engine state is mutated unless the underlying tool does.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload

# Use leaf-level helpers, not the registered tools (avoid round-trip).
from sq_mcp.tools.alerts import (
    _evaluate_batch as _alert_eval_batch,
)
from sq_mcp.tools.alerts import (
    _format_summary as _alert_format,
)
from sq_mcp.tools.alerts import (
    _workspace_defaults as _alert_defaults,
)
from sq_mcp.tools.benchmark import _alpha_beta, _outperformance_streaks
from sq_mcp.tools.brittle import _brittle_score_from_trades
from sq_mcp.tools.dashboard import _build_payload
from sq_mcp.tools.dashboard import _render_markdown as _render_dashboard
from sq_mcp.tools.hypothesis import _paired_t_test
from sq_mcp.tools.promotion import PromotionInputs
from sq_mcp.tools.ship_pipeline import _run_pipeline
from sq_mcp.tools.state import _read_state, _state_path, _write_state_atomic
from sq_mcp.tools.stress_test import CombinedArgs
from sq_mcp.tools.stress_test import _combined as _stress_combined

# ---- argument schemas ------------------------------------------------------


class MorningBriefingArgs(BaseModel):
    stale_days: int = Field(7, ge=1, le=3650)
    max_recent_projects: int = Field(10, ge=1, le=100)
    payload_for_alerts: dict[str, Any] = Field(default_factory=dict)


class PromoteStrategyArgs(BaseModel):
    strategy_name: str = Field(..., min_length=1, max_length=256)
    strategy_key: str = Field(..., min_length=1, max_length=256)
    promotion_inputs: PromotionInputs
    tags_if_approved: list[str] = Field(default_factory=list, max_length=20)
    parent_lineage_id: str | None = None
    dry_run: bool = False


class CompareTwoArgs(BaseModel):
    name_a: str
    name_b: str
    trades_a: list[float] = Field(..., min_length=10, max_length=100_000)
    trades_b: list[float] = Field(..., min_length=10, max_length=100_000)
    returns_a: list[float] | None = None
    returns_b: list[float] | None = None
    alpha: float = Field(0.05, gt=0, lt=1)


# ---- helpers ---------------------------------------------------------------


def _morning_briefing(
    eng,  # noqa: ANN001
    *,
    stale_days: int,
    max_recent_projects: int,
    payload_for_alerts: dict[str, Any],
) -> dict[str, Any]:
    dashboard_payload = _build_payload(
        eng,
        include_stale_symbols=True,
        stale_days=stale_days,
        max_recent_projects=max_recent_projects,
    )
    dashboard_md = _render_dashboard(dashboard_payload, "Morning briefing")

    # Alerts against the user-provided payload (or empty dict)
    alert_result = _alert_eval_batch(_alert_defaults(), payload_for_alerts)
    alerts_md = _alert_format(alert_result["alerts"], "Active alerts")

    # Stale data → schedule recommendations (only if there's data)
    stale = dashboard_payload.get("stale_data") or []
    schedule_actions: list[str] = []
    if stale:
        for r in stale[:5]:
            schedule_actions.append(
                f"refresh `{r['symbol']}/{r['filename']}` ({r['age_days']}d stale)"
            )

    return {
        "dashboard": dashboard_payload,
        "dashboard_markdown": dashboard_md,
        "alerts": alert_result,
        "alerts_markdown": alerts_md,
        "schedule_actions": schedule_actions,
    }


def _promote_strategy(
    state: dict[str, Any],
    *,
    strategy_name: str,
    strategy_key: str,
    promotion_inputs: PromotionInputs,
    tags_if_approved: list[str],
    parent_lineage_id: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    return _run_pipeline(
        state,
        strategy_name=strategy_name,
        strategy_key=strategy_key,
        promotion_inputs=promotion_inputs,
        tags_if_approved=tags_if_approved,
        parent_lineage_id=parent_lineage_id,
        dry_run=dry_run,
    )


def _compare_two_strategies(args: CompareTwoArgs) -> dict[str, Any]:
    # Statistical
    paired = _paired_t_test(args.trades_a, args.trades_b, args.alpha)
    # Brittleness side-by-side
    bri_a = _brittle_score_from_trades(args.trades_a)
    bri_b = _brittle_score_from_trades(args.trades_b)
    # Stress
    try:
        stress_a = _stress_combined(CombinedArgs(trades=args.trades_a))
    except (ValueError, TypeError):
        stress_a = {"verdict": "error"}
    try:
        stress_b = _stress_combined(CombinedArgs(trades=args.trades_b))
    except (ValueError, TypeError):
        stress_b = {"verdict": "error"}

    # Optional benchmark-style alpha/beta if returns provided
    alpha_beta = None
    outperf = None
    if args.returns_a and args.returns_b:
        alpha_beta = _alpha_beta(args.returns_a, args.returns_b)
        outperf = _outperformance_streaks(args.returns_a, args.returns_b)

    # Verdict synthesis
    a_better_stats = (
        paired.get("mean_difference", 0) > 0 and paired.get("verdict") == "different"
    )
    return {
        "name_a": args.name_a,
        "name_b": args.name_b,
        "paired_t_test": paired,
        "brittle_a": bri_a,
        "brittle_b": bri_b,
        "stress_a": stress_a,
        "stress_b": stress_b,
        "alpha_beta": alpha_beta,
        "outperformance": outperf,
        "summary": (
            f"{args.name_a} > {args.name_b}"
            if a_better_stats
            else f"{args.name_b} > {args.name_a}"
            if paired.get("verdict") == "different"
            else "no significant difference"
        ),
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Morning briefing for the workspace: dashboard overview + active "
            "alerts + freshness-driven schedule recommendations, all in one "
            "call. Returns structured payload + Markdown for each section. "
            "Read-only."
        )
    )
    async def workflow_morning_briefing(args: MorningBriefingArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            return {
                "ok": True,
                **_morning_briefing(
                    eng,
                    stale_days=args.stale_days,
                    max_recent_projects=args.max_recent_projects,
                    payload_for_alerts=args.payload_for_alerts,
                ),
            }
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Full promote-to-live workflow: runs the ship pipeline (audit gates "
            "+ tag + lineage + alerts). Returns the manifest. Set dry_run=True "
            "to preview without persisting. WRITE — confirm before non-dry-run."
        )
    )
    async def workflow_promote_strategy(args: PromoteStrategyArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            state = _read_state(p)
            manifest = _promote_strategy(
                state,
                strategy_name=args.strategy_name,
                strategy_key=args.strategy_key,
                promotion_inputs=args.promotion_inputs,
                tags_if_approved=args.tags_if_approved,
                parent_lineage_id=args.parent_lineage_id,
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                _write_state_atomic(p, state)
            return {"ok": True, "manifest": manifest}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Compare two strategies side-by-side: paired t-test on per-trade "
            "PnL, brittleness verdict for each, stress test for each, "
            "optionally alpha/beta if return series provided. Returns a "
            "synthesized verdict. Read-only."
        )
    )
    async def workflow_compare_two_strategies(args: CompareTwoArgs) -> dict:
        return {"ok": True, **_compare_two_strategies(args)}
