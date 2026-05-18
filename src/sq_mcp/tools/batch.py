"""Multi-project orchestration helpers — operate across the whole workspace.

These tools cover the operational gap between "do one thing to one project"
and "manage a workspace of N projects". They reuse the single-project
primitives in ``projects.py`` and add cross-project planning + status grids.

Tools:

- ``projects_batch_status`` — pull a status row for every project on disk in
  one call (uses each project's best status source: engine if available,
  filesystem fallback otherwise).
- ``projects_batch_start`` — start a list of projects with a hard cap on
  how many can run at once (the engine doesn't enforce this; we do).
  Returns which projects were kicked off and which were deferred.
- ``projects_batch_stop`` — stop a list of projects in one round-trip.
- ``projects_inventory`` — workspace-wide inventory: every project, its
  cfx size, mtime, and databank counts, joined into a single table.
- ``projects_databank_export_all`` — for each project, list every databank
  and (optionally) the row count per databank.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.projects import _project_status_fs, _scan_projects_fs

# ---- argument schemas ------------------------------------------------------


class ProjectsBatchStartArgs(BaseModel):
    projects: list[str] = Field(
        ..., min_length=1, max_length=50,
        description="Project names to start (in order).",
    )
    max_concurrent: int = Field(
        1, ge=1, le=10,
        description=(
            "Maximum number to actually kick off in this call. The rest are "
            "listed as 'deferred' so the caller can retry after some finish."
        ),
    )
    nowait: bool = Field(
        True,
        description="Hand 'nowait' to project_start so the call returns immediately.",
    )

    @field_validator("projects")
    @classmethod
    def _v_projects(cls, v: list[str]) -> list[str]:
        return [validate_project_name(name) for name in v]


class ProjectsBatchStopArgs(BaseModel):
    projects: list[str] = Field(..., min_length=1, max_length=50)

    @field_validator("projects")
    @classmethod
    def _v_projects(cls, v: list[str]) -> list[str]:
        return [validate_project_name(name) for name in v]


class ProjectsInventoryArgs(BaseModel):
    include_databank_counts: bool = Field(
        True,
        description="Walk each project's databanks/ to count subfolders + files.",
    )


# ---- helpers ---------------------------------------------------------------


def _count_databanks_for_project(project_dir: Path) -> dict[str, Any]:
    """Cheap per-project databank inventory (no parsing)."""
    db_root = project_dir / "databanks"
    if not db_root.is_dir():
        return {"databanks": 0, "sqx_files_total": 0, "by_databank": []}
    by_db: list[dict[str, Any]] = []
    total_sqx = 0
    for d in sorted(db_root.iterdir()):
        if not d.is_dir():
            continue
        sqx_count = sum(1 for _ in d.rglob("*.sqx"))
        total_sqx += sqx_count
        by_db.append({"name": d.name, "sqx_count": sqx_count})
    return {
        "databanks": len(by_db),
        "sqx_files_total": total_sqx,
        "by_databank": by_db,
    }


def _classify_status_text(text: str) -> str:
    """Reduce engine status response to a single coarse state.

    Order matters: 'not running' must be checked before bare 'running' so the
    substring match doesn't misclassify it as a running project.
    """
    t = (text or "").lower()
    if "not running" in t or "stopped" in t or "finished" in t:
        return "stopped"
    if "paused" in t:
        return "paused"
    if "running" in t or "started" in t:
        return "running"
    return "unknown"


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Status row per project on disk in one shot. For each project: "
            "tries 'sqcli -project action=status' (best when engine is up), "
            "falls back to a filesystem-only status when the engine is silent. "
            "Returns: name, cfx_present, databanks_present, coarse_state, "
            "engine_raw (truncated)."
        )
    )
    async def projects_batch_status(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            scanned = _scan_projects_fs(eng.config.projects_dir)
            rows: list[dict[str, Any]] = []
            for p in scanned:
                name = p["name"]
                engine_status: str | None = None
                engine_raw: str | None = None
                fs_extra: dict[str, Any] = {}
                try:
                    txt = await eng.call(
                        f"-project action=status name={name}", timeout=20.0
                    )
                    engine_raw = txt
                    engine_status = _classify_status_text(txt)
                except EngineError as exc:
                    fs_extra = _project_status_fs(eng, name) | {
                        "engine_error": str(exc)
                    }
                    engine_status = "unreachable"
                rows.append(
                    {
                        "name": name,
                        "cfx_size": p.get("cfx_size"),
                        "cfx_mtime": p.get("cfx_mtime"),
                        "engine_status": engine_status,
                        "engine_raw": (engine_raw or "").strip()[:200] or None,
                        **fs_extra,
                    }
                )
            return {
                "ok": True,
                "projects_scanned": len(scanned),
                "running_count": sum(1 for r in rows if r["engine_status"] == "running"),
                "stopped_count": sum(1 for r in rows if r["engine_status"] == "stopped"),
                "unknown_count": sum(1 for r in rows if r["engine_status"] in (None, "unknown")),
                "unreachable_count": sum(1 for r in rows if r["engine_status"] == "unreachable"),
                "rows": rows,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Start up to max_concurrent projects from a list, in order. The "
            "engine itself doesn't enforce a global concurrency limit, so this "
            "tool stops issuing 'start' calls after max_concurrent. The remainder "
            "are returned as 'deferred' for the caller to retry. Honors nowait=True "
            "so it doesn't block on long Builder runs."
        )
    )
    async def projects_batch_start(
        args: ProjectsBatchStartArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            started: list[dict[str, Any]] = []
            deferred: list[str] = []
            for name in args.projects:
                if len(started) >= args.max_concurrent:
                    deferred.append(name)
                    continue
                cmd = f"-project action=start name={name}"
                if args.nowait:
                    cmd += " nowait=true"
                try:
                    txt = await eng.call(cmd, timeout=30.0)
                    started.append(
                        {"name": name, "engine_raw": (txt or "").strip()[:200]}
                    )
                except EngineError as exc:
                    started.append({"name": name, "error": str(exc)})
            return {
                "ok": True,
                "max_concurrent": args.max_concurrent,
                "started_count": len(started),
                "deferred_count": len(deferred),
                "started": started,
                "deferred": deferred,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Stop a list of projects in one call. Best-effort: each stop call "
            "is independent; one failure does not stop the rest. Use after "
            "projects_batch_status flagged what's running."
        )
    )
    async def projects_batch_stop(
        args: ProjectsBatchStopArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            results: list[dict[str, Any]] = []
            for name in args.projects:
                try:
                    txt = await eng.call(
                        f"-project action=stop name={name}", timeout=20.0
                    )
                    results.append(
                        {"name": name, "engine_raw": (txt or "").strip()[:200]}
                    )
                except EngineError as exc:
                    results.append({"name": name, "error": str(exc)})
            return {
                "ok": True,
                "stopped_count": sum(1 for r in results if "error" not in r),
                "results": results,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Workspace-wide inventory: every project on disk, its cfx size + "
            "mtime, and (optionally) the number of databanks plus per-databank "
            ".sqx counts. Read-only, no engine calls."
        )
    )
    async def projects_inventory(
        args: ProjectsInventoryArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            scanned = _scan_projects_fs(eng.config.projects_dir)
            rows: list[dict[str, Any]] = []
            for p in scanned:
                project_dir = eng.config.projects_dir / p["name"]
                row = {
                    "name": p["name"],
                    "cfx_size": p["cfx_size"],
                    "cfx_mtime": p["cfx_mtime"],
                }
                if args.include_databank_counts:
                    row.update(_count_databanks_for_project(project_dir))
                rows.append(row)
            return {
                "ok": True,
                "projects_dir": str(eng.config.projects_dir),
                "project_count": len(rows),
                "total_sqx": (
                    sum(r.get("sqx_files_total", 0) for r in rows)
                    if args.include_databank_counts
                    else None
                ),
                "rows": rows,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Workspace overview: counts of projects, databanks, and .sqx files. "
            "Includes a 'most_recently_modified' list (top 10 by cfx mtime). "
            "Cheap one-call dashboard. Read-only."
        )
    )
    async def workspace_overview(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            scanned = _scan_projects_fs(eng.config.projects_dir)
            rows = []
            for p in scanned:
                pd = eng.config.projects_dir / p["name"]
                inv = _count_databanks_for_project(pd)
                rows.append(
                    {
                        "name": p["name"],
                        "cfx_size": p["cfx_size"],
                        "cfx_mtime": p["cfx_mtime"],
                        "databanks": inv["databanks"],
                        "sqx_files_total": inv["sqx_files_total"],
                    }
                )
            rows.sort(key=lambda r: r["cfx_mtime"] or "", reverse=True)
            return {
                "ok": True,
                "now": datetime.now(tz=timezone.utc).isoformat(),
                "projects_dir": str(eng.config.projects_dir),
                "project_count": len(rows),
                "total_databanks": sum(r["databanks"] for r in rows),
                "total_sqx": sum(r["sqx_files_total"] for r in rows),
                "most_recently_modified": rows[:10],
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "ProjectsBatchStartArgs",
    "ProjectsBatchStopArgs",
    "ProjectsInventoryArgs",
    "_classify_status_text",
    "_count_databanks_for_project",
    "register",
]
