"""Heuristic lint for project.cfx files.

Reads the first Build/Optimize task XML and runs a set of opinionated checks
("you probably want OOS hold-out", "PopulationSize=50 is small for a real
search", "MM is FixedSize with 0.1 lots, you're not stress-testing
risk-of-ruin"). Each finding has a severity and a suggested next call.

Tools:

- ``cfx_lint`` — return all findings + per-category counts.
- ``cfx_lint_summary`` — same but with just the totals (no individual findings).
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lxml import etree
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_project_name,
)
from sq_mcp._xml import safe_fromstring
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.cfx_config import _inspect_task_xml, _is_build_like_task
from sq_mcp.tools.projects import _is_task_xml

Severity = Literal["critical", "high", "medium", "low", "info"]


@dataclass
class LintFinding:
    code: str
    severity: Severity
    title: str
    message: str
    suggestion: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class _CfxAnalysis:
    inspect: dict[str, Any] = field(default_factory=dict)
    has_build_like: bool = False
    population_size: int | None = None
    max_generations: int | None = None
    is_ratio: int | None = None
    max_strategies: int | None = None


class CfxLintArgs(BaseModel):
    project: str | None = Field(
        None,
        description="Project name. Mutually exclusive with cfx_path.",
    )
    cfx_path: str | None = Field(
        None,
        description="Absolute path to a .cfx. Mutually exclusive with project.",
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str | None) -> str | None:
        return validate_project_name(v) if v else v


def _extract_first_buildlike(cfx_path: Path) -> bytes | None:
    with zipfile.ZipFile(cfx_path, "r") as z:
        for i in z.infolist():
            if _is_task_xml(i.filename) and _is_build_like_task(i.filename):
                return z.read(i.filename)
    return None


def _analyze_cfx(cfx_path: Path) -> _CfxAnalysis:
    out = _CfxAnalysis()
    xml_bytes = _extract_first_buildlike(cfx_path)
    if xml_bytes is None:
        return out
    out.has_build_like = True
    out.inspect = _inspect_task_xml(xml_bytes)
    try:
        root = safe_fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return out
    bm = root.find(".//BuildMode")
    if bm is not None:
        ps = bm.find("PopulationSize")
        out.population_size = int(ps.text) if ps is not None and ps.text else None
        mg = bm.find("MaxGenerations")
        out.max_generations = int(mg.text) if mg is not None and mg.text else None
        ev = bm.find("EvoInSamplePeriod")
        if ev is not None:
            r = ev.get("ratio")
            out.is_ratio = int(r) if r else None
    ms = root.find(".//Rankings/MaxStrategies")
    if ms is not None and ms.text:
        try:
            out.max_strategies = int(ms.text)
        except ValueError:
            pass
    return out


def _lint_cfx(analysis: _CfxAnalysis) -> list[LintFinding]:
    findings: list[LintFinding] = []
    if not analysis.has_build_like:
        findings.append(
            LintFinding(
                code="NO_BUILD_LIKE_TASK",
                severity="info",
                title="no Build/Optimize task XML",
                message=(
                    "This .cfx has no Build-/Optimize-Task*.xml. Lint targets "
                    "those; reset/walkforward configs aren't covered by this tool."
                ),
                suggestion=(
                    "Use cfx_inspect or cfx_archetype to see what's inside."
                ),
            )
        )
        return findings

    mm = analysis.inspect.get("money_management") or {}
    if not mm.get("active_method"):
        findings.append(
            LintFinding(
                code="NO_ACTIVE_MM",
                severity="high",
                title="no active MoneyManagement method",
                message=(
                    "No <Method use='true'> in the MoneyManagement block — the "
                    "Builder won't size trades and SQ may fail to start."
                ),
                suggestion="cfx_set_money_management method_type=FixedSize size=0.1",
            )
        )
    elif mm.get("active_method") == "FixedSize":
        params = mm.get("active_params") or {}
        size_str = params.get("Size", "")
        try:
            size = float(size_str)
        except (TypeError, ValueError):
            size = None
        if size is not None and size < 0.01:
            findings.append(
                LintFinding(
                    code="FIXED_SIZE_TOO_SMALL",
                    severity="low",
                    title=f"FixedSize position = {size}",
                    message=(
                        "Very small lot — won't be representative of live risk."
                    ),
                    suggestion="Use risk-based sizing (RiskFixedBalancePct / RiskFixedPctOfAccount).",
                )
            )

    if analysis.is_ratio == 100:
        findings.append(
            LintFinding(
                code="NO_OOS_HOLDOUT",
                severity="high",
                title="EvoInSamplePeriod ratio=100",
                message=(
                    "100% in-sample means no OOS holdout. Builder will only "
                    "score on data it has access to."
                ),
                suggestion="cfx_set_genetic_options in_sample_ratio_pct=50 (or 70).",
            )
        )

    if analysis.population_size and analysis.population_size < 50:
        findings.append(
            LintFinding(
                code="POPULATION_TOO_SMALL",
                severity="medium",
                title=f"PopulationSize = {analysis.population_size}",
                message=(
                    "Populations under ~50 collapse quickly into a local optimum "
                    "in genetic-evolution search."
                ),
                suggestion="cfx_set_genetic_options population_size=100",
            )
        )

    if analysis.max_generations and analysis.max_generations < 20:
        findings.append(
            LintFinding(
                code="GENERATIONS_TOO_FEW",
                severity="medium",
                title=f"MaxGenerations = {analysis.max_generations}",
                message=(
                    "Few generations rarely give the GA room to explore — "
                    "you'll cap out before the search converges."
                ),
                suggestion="cfx_set_genetic_options max_generations=100 (or higher).",
            )
        )

    if analysis.max_strategies and analysis.max_strategies > 5000:
        findings.append(
            LintFinding(
                code="MAX_STRATEGIES_HUGE",
                severity="low",
                title=f"MaxStrategies = {analysis.max_strategies}",
                message=(
                    "Very large cap. Be aware: databank files scale O(N), and "
                    "you'll spend more time pruning than building."
                ),
                suggestion="cfx_set_max_strategies 1000 (or whatever fits your retest pipeline).",
            )
        )

    setups = analysis.inspect.get("data_setups") or []
    if not setups:
        findings.append(
            LintFinding(
                code="NO_DATA_SETUP",
                severity="critical",
                title="no <Setup> element under <Data>",
                message="The Builder has nothing to backtest against.",
                suggestion="cfx_set_instrument (or cfx_apply_patch symbol=...) first.",
            )
        )
    else:
        for s in setups:
            dfrom = s.get("dateFrom") or ""
            dto = s.get("dateTo") or ""
            if dfrom and dto and dfrom > dto:
                findings.append(
                    LintFinding(
                        code="INVERTED_DATE_RANGE",
                        severity="critical",
                        title=f"Setup dateFrom > dateTo: {dfrom} > {dto}",
                        message="Backtests with inverted dates will fail or run on nothing.",
                        suggestion="cfx_set_data_range or cfx_apply_patch to fix.",
                    )
                )
            test_prec = s.get("testPrecision")
            if test_prec == "0":
                findings.append(
                    LintFinding(
                        code="TEST_PRECISION_ZERO",
                        severity="medium",
                        title="testPrecision='0' (bar open only)",
                        message=(
                            "Bar-open-only precision misses intrabar SL/PT hits — "
                            "results overstate strategy performance."
                        ),
                        suggestion="Re-run with testPrecision='1' (M1) or higher.",
                    )
                )

    return findings


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Heuristic lint of a .cfx Builder/Optimizer config — flags missing "
            "OOS holdout, undersized populations, inverted date ranges, fixed-size "
            "MM that hides risk-of-ruin, etc. Each finding includes a suggested "
            "next tool call. Read-only."
        )
    )
    async def cfx_lint(args: CfxLintArgs, ctx: Context) -> dict:
        try:
            if args.project and args.cfx_path:
                return {"ok": False, "error": "pass either project OR cfx_path"}
            if args.project:
                eng = get_engine(ctx)
                p = eng.config.projects_dir / args.project / "project.cfx"
            elif args.cfx_path:
                p = resolve_safe_path(args.cfx_path, must_exist=True)
            else:
                return {"ok": False, "error": "must pass project or cfx_path"}
            if not p.is_file():
                return {"ok": False, "error": f"not found: {p}"}

            analysis = _analyze_cfx(p)
            findings = _lint_cfx(analysis)
            counts: dict[str, int] = {}
            for f in findings:
                counts[f.severity] = counts.get(f.severity, 0) + 1
            return {
                "ok": True,
                "source": str(p),
                "findings_count": len(findings),
                "counts_by_severity": counts,
                "has_critical": any(f.severity == "critical" for f in findings),
                "findings": [f.as_dict() for f in findings],
                "analysis": {
                    "has_build_like_task": analysis.has_build_like,
                    "population_size": analysis.population_size,
                    "max_generations": analysis.max_generations,
                    "in_sample_ratio_pct": analysis.is_ratio,
                    "max_strategies": analysis.max_strategies,
                },
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "CfxLintArgs",
    "LintFinding",
    "_CfxAnalysis",
    "_analyze_cfx",
    "_lint_cfx",
    "register",
]
