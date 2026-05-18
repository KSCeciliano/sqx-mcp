"""End-to-end pipeline tools that compose smaller building blocks.

These tools are deliberately chained: each one calls helpers that other tools
already expose. The aim is to give the agent a single tool call that answers
the practical question "which strategies in this databank are actually ready
to ship?" without having to manually fan out across portfolio_rank,
portfolio_select_diverse, strategy_audit, etc.

Tools registered here:

- ``strategy_export_pipeline`` — pick N diversified top strategies from a
  databank, audit each one's risk findings, attach a traffic-light verdict,
  and return source .sqx paths ready to hand to ``mt5_deploy_ea`` (or the
  user's own export step).

- ``strategy_ready_for_deploy`` — same audit pass for a single .sqx file
  outside any project context.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_databank_name,
    validate_project_name,
)
from sq_mcp.engine import EngineError
from sq_mcp.parsers.sqx import derive_metrics, parse_sqx
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.audit import _audit_strategy_metrics, _severity_rank
from sq_mcp.tools.portfolio import (
    _filter_min_trades,
    _rank_rows,
    _scan_databank,
    _select_diverse,
)

# ---- argument schemas ------------------------------------------------------


class StrategyExportPipelineArgs(BaseModel):
    project: str
    databank: str = "Results"
    n: int = Field(default=5, ge=1, le=50)
    rank_mode: str = Field(default="defensive")
    diversify_by: str | None = Field(
        default="trades_hash",
        description=(
            "Diversification bucket key. None = pure top-N ranking. Allowed: "
            "'trades_hash' (different trade sequences), 'symbol_tf' "
            "(symbol/timeframe combos), 'fingerprint_exact' (SQ full hash)."
        ),
    )
    min_trades: int | None = 30
    block_on: list[Literal["critical", "high", "medium", "low", "info"]] = Field(
        default_factory=lambda: ["critical", "high"],
        description=(
            "Severity levels that turn a strategy red (block_deploy=True). "
            "Defaults flag any 'high' or 'critical' audit finding."
        ),
    )


class StrategyReadyForDeployArgs(BaseModel):
    sqx_path: str
    block_on: list[Literal["critical", "high", "medium", "low", "info"]] = Field(
        default_factory=lambda: ["critical", "high"]
    )


# ---- helpers ---------------------------------------------------------------


def _verdict_from_findings(
    findings: list[Any], block_on: list[str]
) -> dict[str, Any]:
    """Roll up Finding dataclasses into a traffic-light verdict."""
    sev_counts: dict[str, int] = {}
    block = False
    worst = "info"
    for f in findings:
        sev = f.severity if hasattr(f, "severity") else f["severity"]
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
        if sev in block_on:
            block = True
        if _severity_rank(sev) < _severity_rank(worst):
            worst = sev

    if block:
        light = "red"
    elif sev_counts:
        light = "yellow"
    else:
        light = "green"

    return {
        "traffic_light": light,
        "block_deploy": block,
        "worst_severity": worst if findings else "none",
        "severity_counts": sev_counts,
        "finding_codes": [
            f.code if hasattr(f, "code") else f["code"] for f in findings
        ],
    }


def _finding_to_dict(f: Any) -> dict[str, Any]:
    if hasattr(f, "as_dict"):
        return f.as_dict()
    return dict(f)


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "End-to-end ship-readiness pipeline: rank → diversify → audit. Picks "
            "N strategies from a databank using the same diversity rules as "
            "portfolio_select_diverse, runs the strategy audit on each one, and "
            "returns a traffic-light verdict (green/yellow/red) per pick plus the "
            "absolute .sqx path you can hand to mt5_deploy_ea or strategy_export."
        )
    )
    async def strategy_export_pipeline(
        args: StrategyExportPipelineArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            project = validate_project_name(args.project)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}

            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            if args.diversify_by:
                picks = _select_diverse(
                    rows,
                    n=args.n,
                    rank_mode=args.rank_mode,
                    diversify_by=args.diversify_by,
                )
            else:
                picks = _rank_rows(rows, args.rank_mode)[: args.n]

            ranked: list[dict[str, Any]] = []
            green = yellow = red = 0
            for r in picks:
                findings = _audit_strategy_metrics(r)
                verdict = _verdict_from_findings(findings, list(args.block_on))
                ranked.append(
                    {
                        "rel": r["rel"],
                        "file": r.get("file"),
                        "strategy_name": r.get("strategy_name"),
                        "symbol": r.get("symbol"),
                        "timeframe": r.get("timeframe"),
                        "trades": r.get("trades"),
                        "fitness_oos": r.get("fitness_oos"),
                        "drawdown_pct": r.get("drawdown_pct"),
                        "profit_to_dd_ratio": r.get("profit_to_dd_ratio"),
                        "trades_hash": r.get("trades_hash"),
                        "verdict": verdict,
                        "findings": [_finding_to_dict(f) for f in findings],
                    }
                )
                if verdict["traffic_light"] == "green":
                    green += 1
                elif verdict["traffic_light"] == "yellow":
                    yellow += 1
                else:
                    red += 1

            return {
                "ok": True,
                "project": project,
                "databank": databank,
                "candidates_total": len(rows),
                "unparseable_count": len(bad),
                "picked_count": len(ranked),
                "rank_mode": args.rank_mode,
                "diversify_by": args.diversify_by,
                "min_trades": args.min_trades,
                "block_on": list(args.block_on),
                "totals": {"green": green, "yellow": yellow, "red": red},
                "deploy_ready": [r for r in ranked if r["verdict"]["traffic_light"] == "green"],
                "needs_review": [r for r in ranked if r["verdict"]["traffic_light"] == "yellow"],
                "blocked": [r for r in ranked if r["verdict"]["traffic_light"] == "red"],
                "all_picks": ranked,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Run the strategy audit against a single .sqx file (outside any project) "
            "and return a green/yellow/red verdict plus the full list of findings. "
            "Useful right before handing the file to mt5_deploy_ea, or for spot-"
            "checking exported strategies that live outside the SQ workspace."
        )
    )
    async def strategy_ready_for_deploy(
        args: StrategyReadyForDeployArgs, ctx: Context  # noqa: ARG001  (ctx kept for symmetry)
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            info = parse_sqx(p)
            m = derive_metrics(info)
            findings = _audit_strategy_metrics(m)
            verdict = _verdict_from_findings(findings, list(args.block_on))
            return {
                "ok": True,
                "sqx_path": str(p),
                "strategy_name": m.get("strategy_name"),
                "metrics": {
                    "trades": m.get("trades"),
                    "fitness_oos": m.get("fitness_oos"),
                    "drawdown_pct": m.get("drawdown_pct"),
                    "profit_to_dd_ratio": m.get("profit_to_dd_ratio"),
                    "oos_is_ratio": m.get("oos_is_ratio"),
                    "trades_per_year": m.get("trades_per_year"),
                    "history_years": m.get("history_years"),
                },
                "verdict": verdict,
                "findings": [_finding_to_dict(f) for f in findings],
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "StrategyExportPipelineArgs",
    "StrategyReadyForDeployArgs",
    "_finding_to_dict",
    "_verdict_from_findings",
    "register",
]
