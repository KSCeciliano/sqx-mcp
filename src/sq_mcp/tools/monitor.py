"""MCP tools for the active build monitor."""

from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_databank_name, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.monitor import DEFAULT_RULES, MonitorManager
from sq_mcp.tools._common import get_engine, safe_error_payload

# Module-level manager — created lazily on first use.
_MANAGER: MonitorManager | None = None


def _manager(ctx: Context) -> MonitorManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = MonitorManager(get_engine(ctx))
    return _MANAGER


class MonitorStartArgs(BaseModel):
    project: str = Field(..., description="Project name to monitor (e.g. 'Builder').")
    databank: str = Field("Results", description="Databank to poll for new strategies.")
    interval_seconds: float = Field(
        60.0,
        ge=10.0,
        le=3600.0,
        description="Polling interval (10–3600 s). 60 s is a reasonable default; lower for short builds.",
    )
    auto_stop_on_critical: bool = Field(
        False,
        description=(
            "If True, the monitor will -project action=stop the project automatically when "
            "STALLED_GROWTH / LOW_MEDIAN_FITNESS / OOS_DEGRADATION rules fire. "
            "Use this for unattended runs."
        ),
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_databank(cls, v: str) -> str:
        return validate_databank_name(v)


class ProjectArg(BaseModel):
    project: str = Field(..., description="Project name.")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Start actively monitoring a project run. Polls databank count, new .sqx files, "
            "median fitness, OOS/IS ratio and project status; raises alerts and (optionally) "
            "stops the project if it's clearly wasting time. Returns immediately — query "
            "monitor_status to inspect."
        )
    )
    async def monitor_start(args: MonitorStartArgs, ctx: Context) -> dict:
        try:
            mgr = _manager(ctx)
            sess = await mgr.start(
                args.project,
                databank=args.databank,
                interval_seconds=args.interval_seconds,
                auto_stop_on_critical=args.auto_stop_on_critical,
            )
            await ctx.info(
                f"monitoring {args.project} every {args.interval_seconds:.0f}s "
                f"({'auto-stop on critical' if args.auto_stop_on_critical else 'alert-only'})"
            )
            return {"ok": True, "session": sess.as_dict()}
        except (EngineError, ValidationError, ValueError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Stop monitoring a project.")
    async def monitor_stop(args: ProjectArg, ctx: Context) -> dict:
        try:
            mgr = _manager(ctx)
            ok = await mgr.stop(args.project)
            return {"ok": ok, "project": args.project}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="List active monitoring sessions.")
    async def monitor_list(ctx: Context) -> dict:
        try:
            mgr = _manager(ctx)
            return {"ok": True, "sessions": [s.as_dict() for s in mgr.list()]}
        except EngineError as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Get current state of a monitor session (last snapshot, recent alerts).")
    async def monitor_status(args: ProjectArg, ctx: Context) -> dict:
        try:
            mgr = _manager(ctx)
            sess = mgr.get(args.project)
            if not sess:
                return {"ok": False, "error": f"no monitor session for project '{args.project}'"}
            return {
                "ok": True,
                "session": sess.as_dict(),
                "recent_snapshots": [s.__dict__ for s in sess.snapshots[-5:]],
                "recent_alerts": [a.as_dict() for a in sess.alerts[-10:]],
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Return the full alert history for a monitor session.")
    async def monitor_alerts(args: ProjectArg, ctx: Context) -> dict:
        try:
            mgr = _manager(ctx)
            sess = mgr.get(args.project)
            if not sess:
                return {"ok": False, "error": f"no monitor session for project '{args.project}'"}
            return {
                "ok": True,
                "project": args.project,
                "alerts": [a.as_dict() for a in sess.alerts],
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="List the default rules and what they detect — useful before starting a monitor.")
    async def monitor_default_rules(ctx: Context) -> dict:
        return {
            "ok": True,
            "rules": [
                {
                    "code": r.code,
                    "description": r.description,
                    "default_action": r.action,
                    "threshold": r.threshold,
                    "window_checks": r.window_checks,
                }
                for r in DEFAULT_RULES
            ],
        }
