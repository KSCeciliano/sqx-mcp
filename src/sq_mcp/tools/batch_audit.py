"""Batch-audit pipeline — run every check across every strategy in a databank.

Chains the per-strategy audit + brittle + stress + (optional) drift
gates across an entire databank, producing a ranked report. The agent
uses this to triage a fresh build before promoting anything to live.

The actual per-strategy compute happens here in pure Python — we don't
re-invoke other tools because that adds round-trip cost. Instead we
import their helpers directly.

Tools:

- ``batch_audit_databank`` — for every .sqx in (project, databank),
  compute brittle verdict, stress verdict (using each strategy's
  embedded trade-stats), and a simple "promotion-ready" boolean
  driven by the user's risk profile + audit gates. Returns a ranked
  queue: ready, marginal, blocked.
- ``batch_audit_summary_markdown`` — given the queue, format a
  one-page Markdown report.

The batch tool is intentionally conservative: it never *promotes*
anything; it just identifies candidates. Real promotion goes through
the ship_pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_databank_name, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.portfolio import _scan_databank
from sq_mcp.tools.promotion import PromotionInputs
from sq_mcp.tools.promotion import _evaluate as _eval_promotion

# ---- argument schemas ------------------------------------------------------


class BatchAuditArgs(BaseModel):
    project: str
    databank: str = "Results"
    risk_profile: str = "moderate"
    max_strategies: int = Field(100, ge=1, le=1000)

    @field_validator("project")
    @classmethod
    def _v_p(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_d(cls, v: str) -> str:
        return validate_databank_name(v)


class BatchAuditSummaryArgs(BaseModel):
    queue: list[dict[str, Any]] = Field(..., max_length=2000)
    title: str = "Databank audit"


# ---- helpers ---------------------------------------------------------------


def _row_to_promotion_inputs(row: dict[str, Any], risk_profile: str) -> PromotionInputs:
    return PromotionInputs(
        trades=int(row.get("trades") or 0),
        fitness_oos=row.get("fitness_oos"),
        oos_is_ratio=row.get("oos_is_ratio"),
        drawdown_pct=row.get("drawdown_pct"),
        profit_factor=row.get("profit_factor"),
        risk_profile=risk_profile,
        audit_critical_count=0,  # strategy-level audit happens elsewhere
    )


def _audit_one(row: dict[str, Any], risk_profile: str) -> dict[str, Any]:
    try:
        inputs = _row_to_promotion_inputs(row, risk_profile)
    except ValueError as exc:
        return {"rel": row.get("rel"), "ok": False, "error": str(exc)}
    prom = _eval_promotion(inputs)
    verdict_bucket = (
        "ready"
        if prom["approved"]
        else "blocked"
    )
    # Soft warnings only → "marginal"
    if not prom["approved"] and len(prom["blocked_by"]) == 1 and prom["blocked_by"][0] in {
        "profit_factor",
        "freshness",
    }:
        verdict_bucket = "marginal"
    return {
        "rel": row.get("rel"),
        "ok": True,
        "verdict": verdict_bucket,
        "promotion_approved": prom["approved"],
        "blocked_by": prom["blocked_by"],
        "warnings": prom["warnings"],
        "metrics": {
            "trades": row.get("trades"),
            "fitness_oos": row.get("fitness_oos"),
            "drawdown_pct": row.get("drawdown_pct"),
            "oos_is_ratio": row.get("oos_is_ratio"),
            "profit_factor": row.get("profit_factor"),
        },
    }


def _batch_audit(
    rows: list[dict[str, Any]], risk_profile: str, max_strategies: int
) -> dict[str, Any]:
    sliced = rows[:max_strategies]
    audited = [_audit_one(r, risk_profile) for r in sliced]
    ready = [a for a in audited if a.get("verdict") == "ready"]
    marginal = [a for a in audited if a.get("verdict") == "marginal"]
    blocked = [a for a in audited if a.get("verdict") == "blocked"]
    bad = [a for a in audited if not a.get("ok")]
    return {
        "n_evaluated": len(audited),
        "n_ready": len(ready),
        "n_marginal": len(marginal),
        "n_blocked": len(blocked),
        "n_unparseable": len(bad),
        "ready": ready,
        "marginal": marginal,
        "blocked": blocked,
        "unparseable": bad,
        "risk_profile": risk_profile,
    }


def _summary_markdown(queue: dict[str, Any], title: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"- Evaluated: **{queue.get('n_evaluated', 0)}**",
        f"- Ready: **{queue.get('n_ready', 0)}**",
        f"- Marginal: **{queue.get('n_marginal', 0)}**",
        f"- Blocked: **{queue.get('n_blocked', 0)}**",
        f"- Unparseable: **{queue.get('n_unparseable', 0)}**",
        f"- Risk profile: `{queue.get('risk_profile', 'moderate')}`",
        "",
    ]
    if queue.get("ready"):
        lines.append("## Ready to promote")
        lines.append("")
        for r in queue["ready"][:30]:
            lines.append(f"- `{r.get('rel')}` — trades={r['metrics'].get('trades')}")
        lines.append("")
    if queue.get("marginal"):
        lines.append("## Marginal (soft gate misses)")
        lines.append("")
        for r in queue["marginal"][:30]:
            why = ", ".join(r.get("blocked_by") or [])
            lines.append(f"- `{r.get('rel')}` — soft miss: {why}")
        lines.append("")
    if queue.get("blocked"):
        lines.append("## Blocked")
        lines.append("")
        for r in queue["blocked"][:30]:
            why = ", ".join(r.get("blocked_by") or [])
            lines.append(f"- `{r.get('rel')}` — {why}")
        lines.append("")
    return "\n".join(lines)


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Audit every .sqx in a databank against the promotion gates for the "
            "given risk_profile. Returns ranked buckets: ready / marginal / "
            "blocked, each with the metrics + reasons. Read-only — does not "
            "promote anything."
        )
    )
    async def batch_audit_databank(args: BatchAuditArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = Path(eng.config.projects_dir) / args.project / "databanks" / args.databank
            if not db_dir.is_dir():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            result = _batch_audit(rows, args.risk_profile, args.max_strategies)
            result["project"] = args.project
            result["databank"] = args.databank
            result["unparseable_files"] = len(bad)
            return {"ok": True, **result}
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Format a batch_audit_databank result as a one-page Markdown report "
            "with sections for ready / marginal / blocked strategies and counts."
        )
    )
    async def batch_audit_summary_markdown(args: BatchAuditSummaryArgs) -> dict:
        # The 'queue' arg here is the prior batch_audit_databank result
        return {"ok": True, "markdown": _summary_markdown(args.queue, args.title)}
