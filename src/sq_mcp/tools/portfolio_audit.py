"""Portfolio-level audit — heuristic checks across a whole databank.

The existing ``audit.strategy_anomaly_check`` covers one strategy at a time.
This module aggregates over the databank to surface issues that only become
visible across many strategies at once:

- Most strategies show low OOS/IS ratio → systemic overfitting in the run.
- All survivors share the same trade_hash → no diversity (one edge).
- All on the same symbol/TF → no cross-asset robustness.
- Tiny profitable fraction → the Builder didn't find much edge.
- HHI > 0.5 on trade_hash → effectively one strategy with permutations.

Tool:

- ``portfolio_audit`` — combine portfolio_summary + portfolio_concentration
  results with strategy-level rule rollups; emit a unified finding list.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, field_validator

from sq_mcp._validation import ValidationError, validate_databank_name, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.audit import Finding, _audit_strategy_metrics, _severity_rank
from sq_mcp.tools.portfolio import _filter_min_trades, _scan_databank, _summary_for_rows
from sq_mcp.tools.portfolio_risk import _bucket_counts, _hhi


class PortfolioAuditArgs(BaseModel):
    project: str
    databank: str = "Results"
    min_trades: int | None = 30

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class DatabankAuditSummaryArgs(BaseModel):
    project: str
    databank: str = "Results"
    min_trades: int | None = None

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


def _audit_portfolio(rows: list[dict[str, Any]]) -> list[Finding]:
    """Portfolio-wide checks over a list of derive_metrics-shaped rows."""
    findings: list[Finding] = []
    if not rows:
        findings.append(
            Finding(
                code="EMPTY_DATABANK",
                severity="info",
                title="databank has no strategies",
                message="Nothing to audit — the databank is empty.",
                suggestion=(
                    "Run a Builder / Optimizer first, then databank_force_sync."
                ),
            )
        )
        return findings

    summary = _summary_for_rows(rows)
    n = summary["strategies"]

    profitable_pct = (
        (summary.get("profitable_count") or 0) / n if n else 0.0
    )
    if profitable_pct < 0.20:
        findings.append(
            Finding(
                code="LOW_PROFITABLE_RATE",
                severity="high",
                title=f"only {profitable_pct:.0%} of strategies are profitable",
                message=(
                    "Less than one in five strategies in this databank is profitable. "
                    "Builder is probably finding noise."
                ),
                suggestion=(
                    "Tighten Builder Conditions, increase IS sample, or change "
                    "fitness criterion (cfx_set_fitness_criterion)."
                ),
            )
        )

    overfit_count = summary.get("overfit_oos_under_0p5") or 0
    if n > 0 and overfit_count / n > 0.30:
        findings.append(
            Finding(
                code="SYSTEMIC_OVERFIT",
                severity="high",
                title=f"{overfit_count}/{n} strategies have OOS/IS < 0.5",
                message=(
                    "More than 30% of survivors show heavy OOS degradation — "
                    "systemic overfitting across the run."
                ),
                suggestion=(
                    "Reduce population/generations cap, add WhatIf robustness "
                    "checks, or widen the IS window. cfx_set_genetic_options."
                ),
            )
        )

    dup_rate = summary.get("duplicate_rate_trades_hash") or 0
    if dup_rate > 0.30:
        findings.append(
            Finding(
                code="HIGH_DUPLICATE_RATE",
                severity="medium",
                title=f"{dup_rate:.0%} duplicate trades_hash rate",
                message=(
                    "A large share of strategies share the same trade sequence — "
                    "they're permutations of the same edge."
                ),
                suggestion="portfolio_dedupe to remove duplicates before retest.",
            )
        )

    hhi = _hhi(_bucket_counts(rows, "trades_hash"))
    hhi_norm = hhi.get("hhi_normalized")
    if hhi_norm is not None and hhi_norm > 0.5:
        findings.append(
            Finding(
                code="HIGH_TRADES_HASH_CONCENTRATION",
                severity="medium",
                title=f"normalized HHI = {hhi_norm:.2f} on trades_hash",
                message=(
                    "Trade-sequence diversity is poor — most strategies cluster "
                    "into a small number of buckets."
                ),
                suggestion=(
                    "portfolio_select_diverse to pick across buckets, or rerun "
                    "Builder with broader BuildingBlocks (cfx_toggle_building_blocks)."
                ),
            )
        )

    by_symbol = summary.get("by_symbol") or {}
    by_tf = summary.get("by_timeframe") or {}
    if len(by_symbol) == 1 and n > 1:
        findings.append(
            Finding(
                code="SINGLE_SYMBOL_ONLY",
                severity="low",
                title=f"all {n} strategies on symbol {next(iter(by_symbol))!r}",
                message=(
                    "No cross-asset diversity. If the asset breaks, the whole "
                    "portfolio breaks at once."
                ),
                suggestion=(
                    "Consider cloning the project to a second symbol "
                    "(project_clone + cfx_set_instrument) and merging databanks."
                ),
            )
        )
    if len(by_tf) == 1 and n > 1:
        findings.append(
            Finding(
                code="SINGLE_TIMEFRAME_ONLY",
                severity="low",
                title=f"all {n} strategies on timeframe {next(iter(by_tf))!r}",
                message=(
                    "Single-TF portfolios miss the diversification benefit of "
                    "multi-horizon edges."
                ),
                suggestion=(
                    "Re-run a Builder on a different TF; merge databanks."
                ),
            )
        )

    if n < 10:
        findings.append(
            Finding(
                code="TINY_DATABANK",
                severity="info",
                title=f"only {n} strategies in this databank",
                message=(
                    "Small databanks make portfolio statistics noisy. Per-strategy "
                    "metrics still apply, but A/B testing across runs is unreliable."
                ),
                suggestion="cfx_set_max_strategies to raise the cap, then re-run Builder.",
            )
        )

    return findings


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Portfolio-level audit — runs portfolio_summary + concentration "
            "checks and emits a Finding[] list with severity codes (the same "
            "format strategy_audit uses). Flags systemic overfitting, low "
            "profitable rate, high concentration, single-symbol-only setups, etc. "
            "Use right after a Builder finishes to triage the run."
        )
    )
    async def portfolio_audit(args: PortfolioAuditArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / args.project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)

            portfolio_findings = _audit_portfolio(rows)
            # Roll up per-strategy findings for context
            strategy_findings_count: dict[str, int] = {}
            for r in rows:
                for f in _audit_strategy_metrics(r):
                    strategy_findings_count[f.code] = (
                        strategy_findings_count.get(f.code, 0) + 1
                    )

            severity_counts: dict[str, int] = {}
            for f in portfolio_findings:
                severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

            worst = "none"
            if portfolio_findings:
                worst = min(
                    portfolio_findings, key=lambda f: _severity_rank(f.severity)
                ).severity

            return {
                "ok": True,
                "project": args.project,
                "databank": databank,
                "strategies_audited": len(rows),
                "unparseable_count": len(bad),
                "portfolio_findings_count": len(portfolio_findings),
                "portfolio_severity_counts": severity_counts,
                "worst_severity": worst,
                "portfolio_findings": [f.as_dict() for f in portfolio_findings],
                "strategy_findings_aggregate": dict(
                    sorted(
                        strategy_findings_count.items(),
                        key=lambda kv: -kv[1],
                    )
                ),
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Aggregate strategy-level audit findings across every strategy in a "
            "databank: counts by severity, top-N most common finding codes, "
            "which strategies have the most findings. Use to triage at the "
            "databank level before drilling into individual strategies. Read-only."
        )
    )
    async def databank_audit_summary(
        args: DatabankAuditSummaryArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            from sq_mcp._validation import validate_databank_name
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / args.project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            severity_counts: dict[str, int] = {}
            code_counts: dict[str, int] = {}
            per_strategy: list[dict[str, Any]] = []
            for r in rows:
                findings = _audit_strategy_metrics(r)
                if not findings:
                    continue
                strat_sev_counts: dict[str, int] = {}
                for f in findings:
                    severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1
                    code_counts[f.code] = code_counts.get(f.code, 0) + 1
                    strat_sev_counts[f.severity] = strat_sev_counts.get(f.severity, 0) + 1
                per_strategy.append(
                    {
                        "rel": r.get("rel"),
                        "finding_count": len(findings),
                        "severity_counts": strat_sev_counts,
                        "codes": [f.code for f in findings],
                    }
                )
            top_codes = sorted(code_counts.items(), key=lambda kv: -kv[1])[:10]
            # Sort per_strategy by total finding count (worst-first)
            per_strategy.sort(key=lambda x: -x["finding_count"])
            return {
                "ok": True,
                "project": args.project,
                "databank": databank,
                "strategies_audited": len(rows),
                "strategies_with_findings": len(per_strategy),
                "unparseable_count": len(bad),
                "severity_counts": severity_counts,
                "top_finding_codes": [
                    {"code": c, "count": n} for c, n in top_codes
                ],
                "worst_strategies": per_strategy[:20],
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "DatabankAuditSummaryArgs",
    "PortfolioAuditArgs",
    "_audit_portfolio",
    "register",
]
