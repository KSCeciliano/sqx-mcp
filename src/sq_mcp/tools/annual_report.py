"""Annual / period markdown report builder.

Aggregates workspace-level state (projects, databanks, drift signals,
recent activity, top strategies) into a single Markdown document for
stakeholders or your own end-of-year review.

The report is *static* — it composes information from arguments the
caller provides rather than re-running every other tool. This keeps
the report module decoupled and testable (no engine roundtrip).

Tools:

- ``annual_report_compose`` — compose a full markdown report from
  user-supplied sections: portfolio summary, top strategies, drift
  results, deployment activity, charts (ASCII).
- ``annual_report_section_top_strategies`` — produce the "top
  strategies" subsection given a sorted list of strategy dicts.
- ``annual_report_section_drift_summary`` — produce the "drift
  summary" subsection given a list of drift outcomes.
- ``annual_report_section_recent_activity`` — produce the "recent
  activity" subsection given a list of activity entries.

All output is markdown text — the caller writes the file. Pure
formatting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# ---- argument schemas ------------------------------------------------------


class TopStrategy(BaseModel):
    rank: int
    name: str
    fitness: float | None = None
    drawdown_pct: float | None = None
    trades: int | None = None
    profit_factor: float | None = None
    tags: list[str] = Field(default_factory=list)


class DriftOutcome(BaseModel):
    strategy_name: str
    verdict: str  # green / yellow / red / insufficient_sample
    note: str | None = None


class ActivityEntry(BaseModel):
    when: str
    what: str  # short description
    details: str | None = None


class TopStrategiesArgs(BaseModel):
    strategies: list[TopStrategy] = Field(..., min_length=1, max_length=100)


class DriftSummaryArgs(BaseModel):
    outcomes: list[DriftOutcome] = Field(..., min_length=1, max_length=500)


class RecentActivityArgs(BaseModel):
    entries: list[ActivityEntry] = Field(..., min_length=1, max_length=500)


class ReportComposeArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    period_start: str  # yyyy-mm-dd
    period_end: str
    portfolio_summary: dict[str, Any] | None = None
    top_strategies: list[TopStrategy] = Field(default_factory=list)
    drift_outcomes: list[DriftOutcome] = Field(default_factory=list)
    recent_activity: list[ActivityEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---- helpers ---------------------------------------------------------------


def _format_top_strategies(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No strategies provided._\n"
    lines = ["| Rank | Name | Fitness | DD% | Trades | PF | Tags |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        tags = ", ".join(r.get("tags") or [])
        lines.append(
            f"| {r['rank']} | {r['name']} | {_fmt(r.get('fitness'))} | "
            f"{_fmt(r.get('drawdown_pct'))} | {_fmt(r.get('trades'))} | "
            f"{_fmt(r.get('profit_factor'))} | {tags} |"
        )
    return "\n".join(lines) + "\n"


def _format_drift_summary(outcomes: list[dict[str, Any]]) -> str:
    if not outcomes:
        return "_No drift data provided._\n"
    counts: dict[str, int] = {}
    for o in outcomes:
        v = o.get("verdict", "unknown")
        counts[v] = counts.get(v, 0) + 1
    summary_line = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    lines = [f"**Drift counts:** {summary_line}", ""]
    # Highlight red strategies
    reds = [o for o in outcomes if o.get("verdict") == "red"]
    yellows = [o for o in outcomes if o.get("verdict") == "yellow"]
    if reds:
        lines.append("### Strategies flagged red")
        lines.append("")
        for o in reds:
            note = f" — {o['note']}" if o.get("note") else ""
            lines.append(f"- **{o['strategy_name']}**{note}")
        lines.append("")
    if yellows:
        lines.append("### Strategies flagged yellow")
        lines.append("")
        for o in yellows:
            note = f" — {o['note']}" if o.get("note") else ""
            lines.append(f"- **{o['strategy_name']}**{note}")
        lines.append("")
    return "\n".join(lines)


def _format_recent_activity(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "_No activity recorded._\n"
    sorted_entries = sorted(entries, key=lambda e: e.get("when", ""), reverse=True)
    lines = ["| When | What | Details |", "|---|---|---|"]
    for e in sorted_entries:
        details = e.get("details") or ""
        # Escape pipe characters in details
        details = details.replace("|", "\\|")
        lines.append(f"| {e['when']} | {e['what']} | {details} |")
    return "\n".join(lines) + "\n"


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _compose_report(args: ReportComposeArgs) -> str:
    now = datetime.now(timezone.utc).isoformat()
    parts: list[str] = [
        f"# {args.title}",
        "",
        f"_Period: {args.period_start} → {args.period_end}_  ",
        f"_Generated: {now}_",
        "",
    ]
    if args.portfolio_summary:
        parts.append("## Portfolio summary")
        parts.append("")
        for k, v in sorted(args.portfolio_summary.items()):
            parts.append(f"- **{k}**: {_fmt(v)}")
        parts.append("")

    parts.append("## Top strategies")
    parts.append("")
    parts.append(
        _format_top_strategies([s.model_dump() for s in args.top_strategies])
    )

    parts.append("## Drift summary")
    parts.append("")
    parts.append(
        _format_drift_summary([o.model_dump() for o in args.drift_outcomes])
    )

    parts.append("## Recent activity")
    parts.append("")
    parts.append(
        _format_recent_activity([e.model_dump() for e in args.recent_activity])
    )

    if args.notes:
        parts.append("## Notes")
        parts.append("")
        for n in args.notes:
            parts.append(f"- {n}")
        parts.append("")

    parts.append("---")
    parts.append("_Generated by sq-mcp annual_report_compose._")
    return "\n".join(parts)


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Compose a full markdown annual/period report from caller-supplied "
            "sections: portfolio summary, top strategies, drift outcomes, "
            "recent activity, free-form notes. Returns the markdown text. The "
            "caller writes it to a file."
        )
    )
    async def annual_report_compose(args: ReportComposeArgs) -> dict:
        md = _compose_report(args)
        return {"ok": True, "markdown": md, "bytes": len(md.encode("utf-8"))}

    @mcp.tool(
        description=(
            "Format a 'top strategies' table from a list of strategy dicts "
            "(rank, name, fitness, drawdown_pct, trades, profit_factor, tags). "
            "Returns markdown table only."
        )
    )
    async def annual_report_section_top_strategies(args: TopStrategiesArgs) -> dict:
        return {
            "ok": True,
            "markdown": _format_top_strategies([s.model_dump() for s in args.strategies]),
        }

    @mcp.tool(
        description=(
            "Format a 'drift summary' section: counts by verdict, highlighted "
            "red/yellow strategy lists. Returns markdown only."
        )
    )
    async def annual_report_section_drift_summary(args: DriftSummaryArgs) -> dict:
        return {
            "ok": True,
            "markdown": _format_drift_summary([o.model_dump() for o in args.outcomes]),
        }

    @mcp.tool(
        description=(
            "Format a 'recent activity' table (when / what / details). Sorted "
            "newest-first. Returns markdown table only."
        )
    )
    async def annual_report_section_recent_activity(args: RecentActivityArgs) -> dict:
        return {
            "ok": True,
            "markdown": _format_recent_activity([e.model_dump() for e in args.entries]),
        }
