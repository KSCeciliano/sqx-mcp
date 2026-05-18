"""Workspace dashboard — one-shot health/state overview.

A single tool that returns a compact view of the workspace state,
suitable for a "morning briefing" or `/sq:dashboard` invocation. It
combines:

- Project count, status by category
- Number of strategies in each project's Results databank
- Active alerts (using the workspace defaults)
- Disk-data freshness (stale symbols)
- Last-modified projects (recently touched)
- Tags inventory (how many strategies per tag)
- Lineage tree size

Pure aggregation — no engine state is mutated. The dashboard payload is
also formattable as Markdown via ``dashboard_render_markdown``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.state import _read_state, _state_path

# ---- argument schemas ------------------------------------------------------


class DashboardArgs(BaseModel):
    include_stale_symbols: bool = True
    stale_days: int = Field(7, ge=1, le=3650)
    max_recent_projects: int = Field(10, ge=1, le=100)


class DashboardRenderArgs(BaseModel):
    payload: dict[str, Any]
    title: str = "Workspace dashboard"


# ---- helpers ---------------------------------------------------------------


def _project_inventory(projects_dir: Path) -> dict[str, Any]:
    if not projects_dir.is_dir():
        return {"projects": [], "count": 0}
    out: list[dict[str, Any]] = []
    for p in sorted(projects_dir.iterdir()):
        if not p.is_dir():
            continue
        cfx = p / "project.cfx"
        if not cfx.is_file():
            continue
        try:
            mtime = cfx.stat().st_mtime
        except OSError:
            mtime = 0
        # Results databank size
        results_dir = p / "databanks" / "Results"
        results_count = (
            sum(1 for _ in results_dir.glob("*.sqx")) if results_dir.is_dir() else 0
        )
        out.append({
            "name": p.name,
            "mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat() if mtime else None,
            "results_count": results_count,
        })
    out.sort(key=lambda r: r.get("mtime") or "", reverse=True)
    return {"projects": out, "count": len(out)}


def _tag_inventory(state: dict[str, Any]) -> dict[str, Any]:
    tags = state.get("data", {}).get("tags", {})
    by_tag: dict[str, int] = {}
    for _, t_list in tags.items():
        for t in t_list:
            by_tag[t] = by_tag.get(t, 0) + 1
    return {
        "n_strategies_tagged": len(tags),
        "n_distinct_tags": len(by_tag),
        "top_tags": sorted(
            ({"tag": t, "n_strategies": c} for t, c in by_tag.items()),
            key=lambda r: r["n_strategies"],
            reverse=True,
        )[:10],
    }


def _lineage_inventory(state: dict[str, Any]) -> dict[str, Any]:
    lineage = state.get("data", {}).get("lineage", {})
    roots = [n for n in lineage.values() if not n.get("parent_id")]
    return {
        "n_nodes": len(lineage),
        "n_root_strategies": len(roots),
    }


def _stale_data(history_dir: Path, stale_days: int) -> list[dict[str, Any]]:
    if not history_dir.is_dir():
        return []
    now = datetime.now(timezone.utc).timestamp()
    cutoff = stale_days * 86400
    stale = []
    for sym_dir in sorted(history_dir.iterdir()):
        if not sym_dir.is_dir():
            continue
        for dat in sym_dir.glob("*.dat"):
            try:
                age = now - dat.stat().st_mtime
            except OSError:
                continue
            if age > cutoff:
                stale.append({
                    "symbol": sym_dir.name,
                    "filename": dat.name,
                    "age_days": round(age / 86400, 2),
                })
    stale.sort(key=lambda r: r["age_days"], reverse=True)
    return stale[:20]


def _build_payload(
    eng,  # noqa: ANN001
    *,
    include_stale_symbols: bool,
    stale_days: int,
    max_recent_projects: int,
) -> dict[str, Any]:
    projects_dir = Path(eng.config.projects_dir)
    history_dir = Path(eng.config.data_dir) / "History"
    inv = _project_inventory(projects_dir)
    state = _read_state(_state_path(eng))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "projects": {
            "count": inv["count"],
            "total_results_strategies": sum(p["results_count"] for p in inv["projects"]),
            "recent": inv["projects"][:max_recent_projects],
        },
        "tags": _tag_inventory(state),
        "lineage": _lineage_inventory(state),
        "stale_data": (
            _stale_data(history_dir, stale_days) if include_stale_symbols else None
        ),
    }


def _render_markdown(payload: dict[str, Any], title: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"_Generated: {payload.get('generated_at', 'unknown')}_",
        "",
        "## Projects",
        "",
        f"- Total projects: **{payload.get('projects', {}).get('count', 0)}**",
        (
            f"- Strategies in Results databanks: "
            f"**{payload.get('projects', {}).get('total_results_strategies', 0)}**"
        ),
        "",
        "### Recently modified",
        "",
    ]
    for p in payload.get("projects", {}).get("recent", []):
        lines.append(f"- `{p.get('name')}` — results={p.get('results_count')}")
    lines += [
        "",
        "## Tags",
        "",
        f"- Strategies tagged: **{payload.get('tags', {}).get('n_strategies_tagged', 0)}**",
        f"- Distinct tags: **{payload.get('tags', {}).get('n_distinct_tags', 0)}**",
        "",
    ]
    if payload.get("tags", {}).get("top_tags"):
        lines.append("### Top tags")
        lines.append("")
        for t in payload["tags"]["top_tags"]:
            lines.append(f"- `{t['tag']}` — {t['n_strategies']} strategies")
        lines.append("")
    lines += [
        "## Lineage",
        "",
        f"- Total nodes: **{payload.get('lineage', {}).get('n_nodes', 0)}**",
        f"- Root strategies: **{payload.get('lineage', {}).get('n_root_strategies', 0)}**",
        "",
    ]
    if payload.get("stale_data"):
        lines.append("## Stale data")
        lines.append("")
        for r in payload["stale_data"][:10]:
            lines.append(f"- `{r['symbol']}/{r['filename']}` — {r['age_days']}d old")
        lines.append("")
    return "\n".join(lines)


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "One-shot workspace dashboard: project count, total strategies in "
            "Results databanks, recent project mtimes, tag inventory, lineage "
            "tree size, optionally stale .dat files. Returns a structured "
            "payload — pair with dashboard_render_markdown for a Markdown view."
        )
    )
    async def dashboard_overview(args: DashboardArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            return {
                "ok": True,
                "payload": _build_payload(
                    eng,
                    include_stale_symbols=args.include_stale_symbols,
                    stale_days=args.stale_days,
                    max_recent_projects=args.max_recent_projects,
                ),
            }
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Render a dashboard payload as Markdown. Pass the `payload` "
            "returned by `dashboard_overview`. Pure formatting."
        )
    )
    async def dashboard_render_markdown(args: DashboardRenderArgs) -> dict:
        return {"ok": True, "markdown": _render_markdown(args.payload, args.title)}
