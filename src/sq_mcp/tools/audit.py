"""Audit tools — detect problems before they cost real money.

Three levels:

  * strategy_anomaly_check: heuristic audit of one .sqx (overfit signals,
    missing SL/PT, suspicious metric distributions).
  * project_anomaly_check: structural audit of one project (CFX validity,
    data coverage, naming pitfalls, snapshot hygiene).
  * workspace_audit: sweep every project + the most recent .sqx in each
    databank, return a ranked issue list.

All findings carry a structured `severity` so callers can decide what to
escalate vs. just log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_project_name,
)
from sq_mcp.engine import EngineError
from sq_mcp.parsers.sqx import derive_metrics, parse_sqx
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.projects import (
    _referenced_dates_from_cfx,
    _referenced_symbols_from_cfx,
    _scan_projects_fs,
    _validate_cfx_structure,
)

Severity = Literal["critical", "high", "medium", "low", "info"]


@dataclass
class Finding:
    code: str
    severity: Severity
    title: str
    message: str
    suggestion: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _severity_rank(s: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(s, 5)


# ---- strategy heuristics ----------------------------------------------------


def _audit_strategy_metrics(m: dict[str, Any]) -> list[Finding]:
    """Apply heuristic rules to a `derive_metrics` payload."""
    findings: list[Finding] = []

    trades = m.get("trades")
    if trades is not None and trades < 30:
        findings.append(
            Finding(
                code="TOO_FEW_TRADES",
                severity="high",
                title=f"only {trades} trades",
                message=(
                    "Fewer than 30 trades is statistically too thin to draw "
                    "reliable performance conclusions."
                ),
                suggestion=(
                    "Extend the backtest window, lower the timeframe, or relax "
                    "entry filters to get a denser sample."
                ),
            )
        )

    fitness_is = m.get("fitness_is")
    fitness_oos = m.get("fitness_oos")
    if (fitness_oos is None or fitness_oos == 0) and fitness_is is not None and fitness_is > 0:
        findings.append(
            Finding(
                code="NO_OOS_FITNESS",
                severity="high",
                title="OOS fitness missing or zero",
                message=(
                    "In-sample fitness is present but OOS is absent — the strategy "
                    "has never been evaluated on unseen data."
                ),
                suggestion="Run a Retest with an OOS sample before trusting this fitness.",
            )
        )

    oos_is_ratio = m.get("oos_is_ratio")
    # Only flag overfit when there IS an OOS value to compare. fitness_oos == 0
    # (and ratio == 0.0) typically means "no OOS sample was configured" — the
    # NO_OOS_FITNESS finding above already covers that case so we'd be
    # double-flagging.
    if (
        oos_is_ratio is not None
        and oos_is_ratio < 0.5
        and fitness_oos is not None
        and fitness_oos > 0
    ):
        findings.append(
            Finding(
                code="OVERFIT_OOS_DEGRADATION",
                severity="critical",
                title=f"OOS/IS ratio {oos_is_ratio:.2f}",
                message=(
                    "OOS fitness is less than half of IS fitness — strong curve-fit "
                    "signal. Performance is unlikely to hold out of sample."
                ),
                suggestion="Re-run Builder with stricter robustness checks and rebuild.",
            )
        )

    profit_to_dd = m.get("profit_to_dd_ratio")
    if profit_to_dd is not None and profit_to_dd > 20:
        findings.append(
            Finding(
                code="UNREALISTIC_PROFIT_TO_DD",
                severity="high",
                title=f"profit-to-DD ratio {profit_to_dd:.1f}",
                message=(
                    "Ratios above ~20 typically indicate overfitting, look-ahead "
                    "bias, or unrealistic backtest assumptions (zero slippage / "
                    "perfect fills)."
                ),
                suggestion=(
                    "Retest with realistic spread + slippage + commission. "
                    "Compare a coarse-tick vs fine-tick backtest."
                ),
            )
        )

    dd_pct = m.get("drawdown_pct")
    if dd_pct is not None and dd_pct > 50:
        findings.append(
            Finding(
                code="DRAWDOWN_OVER_HALF",
                severity="high",
                title=f"drawdown {dd_pct:.1f}% of capital",
                message=(
                    "Max drawdown exceeds 50% of initial capital — most live accounts "
                    "would be margin-called or psychologically abandoned long before."
                ),
                suggestion="Lower position size or filter out high-DD-period entries.",
            )
        )

    if (m.get("ambiguous_trades") or 0) > 0:
        findings.append(
            Finding(
                code="AMBIGUOUS_TRADES",
                severity="medium",
                title=f"{m.get('ambiguous_trades')} ambiguous trade(s)",
                message=(
                    "Engine flagged ambiguous trades — orders that could have been "
                    "filled multiple ways within a single bar."
                ),
                suggestion=(
                    "Re-run on higher BacktestPrecision (M1 → tick) and verify the "
                    "P/L is stable."
                ),
            )
        )

    if (m.get("strategy_problems") or 0) > 0:
        findings.append(
            Finding(
                code="STRATEGY_PROBLEMS",
                severity="high",
                title=f"{m.get('strategy_problems')} engine-flagged problem(s)",
                message="SQ engine reported StrategyProblems > 0 in the result.",
                suggestion="Open the .sqx in SQ X GUI to inspect the warnings.",
            )
        )

    years = m.get("history_years")
    if years is not None and years < 1:
        findings.append(
            Finding(
                code="VERY_SHORT_BACKTEST",
                severity="medium",
                title=f"backtest covers only {years:.2f} years",
                message=(
                    "Less than a year of history likely doesn't include both bullish "
                    "and bearish regimes, so robustness on unseen years is unproven."
                ),
                suggestion="Extend HistoryFrom to at least 3 years of data.",
            )
        )

    dd_abs = m.get("drawdown_abs")
    if dd_abs is not None and 0 < dd_abs < 0.01:
        findings.append(
            Finding(
                code="SUSPICIOUSLY_LOW_DRAWDOWN",
                severity="medium",
                title=f"drawdown_abs = {dd_abs}",
                message=(
                    "Near-zero drawdown is highly suspicious in a real-world backtest — "
                    "often the symptom of synthetic data or look-ahead leaking into entry."
                ),
                suggestion="Inspect the equity curve manually and rerun on realistic data.",
            )
        )

    trades_per_year = m.get("trades_per_year")
    if trades_per_year is not None and trades_per_year > 5000:
        findings.append(
            Finding(
                code="EXTREMELY_HIGH_TRADE_FREQ",
                severity="medium",
                title=f"{trades_per_year:.0f} trades/year",
                message=(
                    "Trading thousands of times per year per instrument is rarely "
                    "viable after broker spread + commission."
                ),
                suggestion=(
                    "Re-evaluate with realistic costs; the strategy may collapse to "
                    "break-even or loss in live conditions."
                ),
            )
        )

    return findings


# ---- project heuristics -----------------------------------------------------


def _audit_project_structure(
    project_dir: Path, eng_data_dir: Path, history_dir: Path
) -> list[Finding]:
    findings: list[Finding] = []
    name = project_dir.name
    cfx = project_dir / "project.cfx"

    # naming pitfalls (from CLAUDE.md: HTTP API can't reference names with spaces)
    if " " in name:
        findings.append(
            Finding(
                code="PROJECT_NAME_HAS_SPACES",
                severity="medium",
                title=f"name contains spaces: {name!r}",
                message=(
                    "sqcli's HTTP API parser splits on whitespace, so tools like "
                    "project_load_and_start and pipeline_* cannot reference this project."
                ),
                suggestion=(
                    "project_clone to an underscore-only name before running automation."
                ),
            )
        )
    if "(" in name and ")" in name:
        findings.append(
            Finding(
                code="PHANTOM_VARIANT_NAME",
                severity="high",
                title=f"name looks like JVM phantom variant: {name!r}",
                message=(
                    "Names like 'Foo(2)' typically result from rm -rf'ing a project "
                    "without first calling -project action=remove. The JVM keeps the "
                    "old instance and loadconfig creates a sibling variant."
                ),
                suggestion=(
                    "project_force_remove to clean both engine state and disk, then "
                    "recreate fresh."
                ),
            )
        )

    if not cfx.is_file():
        findings.append(
            Finding(
                code="PROJECT_CFX_MISSING",
                severity="critical",
                title="project.cfx not found",
                message="The project directory exists but contains no project.cfx.",
                suggestion="Either restore from a snapshot or delete the empty dir.",
            )
        )
        return findings  # bail — nothing else we can check without the .cfx

    # CFX structural validation
    cfx_check = _validate_cfx_structure(cfx)
    for issue in cfx_check.get("issues", []) or []:
        findings.append(
            Finding(
                code="CFX_STRUCTURE_ISSUE",
                severity="high",
                title="CFX structural issue",
                message=issue,
                suggestion="Open in SQ X GUI to repair or remove orphan files.",
            )
        )

    # Data coverage for referenced symbols
    referenced = _referenced_symbols_from_cfx(cfx)
    history_root = history_dir
    for sym in referenced:
        # Try both the full symbol name and the base form (strip _M1_dukas etc.)
        base_candidates = [sym, sym.split("_")[0]]
        if not any((history_root / c).is_dir() for c in base_candidates):
            findings.append(
                Finding(
                    code="MISSING_HISTORY_FOR_REFERENCED_SYMBOL",
                    severity="high",
                    title=f"no .dat history for {sym!r}",
                    message=(
                        f"Project references {sym!r} but no folder exists under "
                        f"{history_root}/. Builder/Retester runs would fail to start."
                    ),
                    suggestion="data_import the symbol before kicking off the project.",
                )
            )

    # Snapshot hygiene — projects edited recently without a snapshot
    snapshot_count = len(list(project_dir.glob("project.cfx.bak.*")))
    try:
        cfx_age_days = (time.time() - cfx.stat().st_mtime) / 86400.0
    except OSError:
        cfx_age_days = None
    if snapshot_count == 0 and cfx_age_days is not None and cfx_age_days < 14:
        findings.append(
            Finding(
                code="NO_RECENT_SNAPSHOT",
                severity="low",
                title="no .cfx.bak snapshots",
                message=(
                    f"This project's .cfx was last modified {cfx_age_days:.1f} days "
                    "ago but no snapshot exists. Manual edits would be unrecoverable."
                ),
                suggestion="project_snapshot before next CFX patch.",
            )
        )

    # Inverted date ranges in task XMLs
    dates = _referenced_dates_from_cfx(cfx)
    # heuristic: if any 'date_from' string sorts strictly later than every date_to, flag
    df_list = dates.get("date_from", []) or []
    dt_list = dates.get("date_to", []) or []
    if df_list and dt_list:
        # Detect obviously-inverted patterns like '2026.05.01' vs '2023.01.01'
        max_to = max(dt_list)
        min_from = min(df_list)
        # Only flag when comparing dotted-date strings (yyyy.MM.dd sorts lexicographically)
        if "." in max_to and "." in min_from and min_from > max_to:
            findings.append(
                Finding(
                    code="INVERTED_DATE_RANGE",
                    severity="critical",
                    title="dateFrom > dateTo in some task XML",
                    message=(
                        f"At least one task has dateFrom={min_from!r} > "
                        f"dateTo={max_to!r}. The engine will silently produce empty "
                        "results."
                    ),
                    suggestion="Use cfx_apply_patch to set a sane date_from / date_to.",
                )
            )

    return findings


# ---- args & registration ----------------------------------------------------


class StrategyAuditArgs(BaseModel):
    path: str = Field(..., description="Path to a .sqx file.")


class ProjectAuditArgs(BaseModel):
    project: str = Field(..., description="Project name.")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class WorkspaceAuditArgs(BaseModel):
    max_strategies_per_databank: int = Field(
        5,
        ge=0,
        le=100,
        description=(
            "How many of the most-recent .sqx files to scan PER databank. Set 0 to "
            "skip strategy-level audits entirely (faster). 5 is a sane default."
        ),
    )
    min_severity: Literal["critical", "high", "medium", "low", "info"] = Field(
        "medium",
        description=(
            "Drop findings below this severity. 'critical' = blockers only; "
            "'info' = everything (verbose)."
        ),
    )


class PreLiveChecklistArgs(BaseModel):
    path: str = Field(..., description="Path to the .sqx file to vet for live deployment.")
    require_oos: bool = Field(
        True,
        description=(
            "If True (default), demand non-zero OOS fitness. Skips this check when "
            "you're explicitly running an IS-only experiment."
        ),
    )
    require_min_trades: int = Field(
        100,
        ge=0,
        le=10_000,
        description="Minimum trades required to pass.",
    )
    max_drawdown_pct: float = Field(
        30.0,
        ge=0.0,
        le=100.0,
        description="Maximum acceptable drawdown_pct.",
    )
    min_profit_to_dd_ratio: float = Field(
        1.5,
        ge=0.0,
        description="Minimum profit/DD ratio.",
    )
    max_history_age_days: int = Field(
        90,
        ge=1,
        le=3650,
        description=(
            "Reject strategies whose HistoryTo is older than this many days — stale "
            "backtests don't account for the current market regime."
        ),
    )


class StrategyHistoryArgs(BaseModel):
    trades_hash: str = Field(
        ...,
        description="The Fingerprint trades_hash value to locate.",
        min_length=1,
        max_length=64,
    )
    project_filter: str | None = Field(
        None,
        description="If set, restrict the search to a single project.",
    )

    @field_validator("trades_hash")
    @classmethod
    def _v_hash(cls, v: str) -> str:
        # tradesHash is an integer string in SQ Build 143. Allow alnum just in case.
        if not v.replace("-", "").isalnum():
            raise ValueError("trades_hash must be alphanumeric (with optional dashes)")
        return v

    @field_validator("project_filter")
    @classmethod
    def _v_project(cls, v: str | None) -> str | None:
        return validate_project_name(v) if v else v


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Heuristic audit of a single .sqx strategy file. Flags overfit signals "
            "(OOS<<IS, unrealistically high profit/DD), thin samples (<30 trades), "
            "missing OOS data, ambiguous trades, engine-flagged problems, and "
            "suspicious metric distributions. Returns findings grouped by severity "
            "(critical / high / medium / low / info). Pure file read — no engine call."
        )
    )
    async def strategy_anomaly_check(args: StrategyAuditArgs, ctx: Context) -> dict:
        try:
            path = resolve_safe_path(args.path, must_exist=True)
            info = parse_sqx(path)
            m = derive_metrics(info)
            findings = _audit_strategy_metrics(m)
            counts = {s: 0 for s in ("critical", "high", "medium", "low", "info")}
            for f in findings:
                counts[f.severity] += 1
            return {
                "ok": True,
                "path": str(path),
                "metrics": m,
                "finding_count": len(findings),
                "severity_counts": counts,
                "findings": [f.as_dict() for f in
                             sorted(findings, key=lambda f: _severity_rank(f.severity))],
            }
        except (ValidationError, ValueError, FileNotFoundError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Structural & operational audit of one project. Checks CFX validity, "
            "data coverage for every referenced symbol, naming pitfalls (spaces, "
            "phantom variants), snapshot hygiene, inverted date ranges. Pure "
            "filesystem read — no engine call."
        )
    )
    async def project_anomaly_check(args: ProjectAuditArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            project_dir = eng.config.projects_dir / args.project
            if not project_dir.exists():
                return {
                    "ok": False,
                    "error": f"project directory not found: {project_dir}",
                }
            findings = _audit_project_structure(
                project_dir, eng.config.data_dir, eng.config.history_dir
            )
            counts = {s: 0 for s in ("critical", "high", "medium", "low", "info")}
            for f in findings:
                counts[f.severity] += 1
            return {
                "ok": True,
                "project": args.project,
                "project_dir": str(project_dir),
                "finding_count": len(findings),
                "severity_counts": counts,
                "findings": [f.as_dict() for f in
                             sorted(findings, key=lambda f: _severity_rank(f.severity))],
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Workspace-wide audit. Runs project_anomaly_check on every project AND "
            "strategy_anomaly_check on the N most recent .sqx files in each Results "
            "databank. Returns a ranked roll-up plus per-project / per-strategy "
            "details. Use this as a 'morning health report' before kicking off "
            "automation, or after a long automated run to see what came out broken."
        )
    )
    async def workspace_audit(args: WorkspaceAuditArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            projects_dir = eng.config.projects_dir
            fs_projects = _scan_projects_fs(projects_dir)
            min_rank = _severity_rank(args.min_severity)

            per_project: list[dict[str, Any]] = []
            top_severity_counter = {s: 0 for s in ("critical", "high", "medium", "low", "info")}
            for entry in fs_projects:
                proj_dir = projects_dir / entry["name"]
                proj_findings = _audit_project_structure(
                    proj_dir, eng.config.data_dir, eng.config.history_dir
                )
                # Strategy-level scan: take N most recent .sqx in databanks/Results
                strat_findings: list[dict[str, Any]] = []
                if args.max_strategies_per_databank > 0:
                    results_dir = proj_dir / "databanks" / "Results"
                    if results_dir.exists():
                        try:
                            sqx_paths = sorted(
                                results_dir.rglob("*.sqx"),
                                key=lambda p: p.stat().st_mtime,
                                reverse=True,
                            )[: args.max_strategies_per_databank]
                        except OSError:
                            sqx_paths = []
                        for sp in sqx_paths:
                            try:
                                info = parse_sqx(sp)
                                m = derive_metrics(info)
                                strat_findings_obj = _audit_strategy_metrics(m)
                            except (ValueError, OSError):
                                continue
                            for f in strat_findings_obj:
                                if _severity_rank(f.severity) > min_rank:
                                    continue
                                strat_findings.append(
                                    {
                                        "file": str(sp.relative_to(results_dir)),
                                        **f.as_dict(),
                                    }
                                )

                filtered_proj_findings = [
                    f for f in proj_findings if _severity_rank(f.severity) <= min_rank
                ]
                for f in filtered_proj_findings:
                    top_severity_counter[f.severity] += 1
                for sf in strat_findings:
                    top_severity_counter[sf["severity"]] += 1

                per_project.append(
                    {
                        "project": entry["name"],
                        "project_findings": [f.as_dict() for f in filtered_proj_findings],
                        "strategy_findings": strat_findings,
                        "finding_count": len(filtered_proj_findings) + len(strat_findings),
                    }
                )

            per_project.sort(key=lambda e: e["finding_count"], reverse=True)
            total_findings = sum(e["finding_count"] for e in per_project)
            return {
                "ok": True,
                "projects_scanned": len(fs_projects),
                "max_strategies_per_databank": args.max_strategies_per_databank,
                "min_severity": args.min_severity,
                "total_findings": total_findings,
                "severity_counts": top_severity_counter,
                "per_project": per_project,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Pre-live deployment checklist for a .sqx strategy. Verifies: OOS sample "
            "exists and is non-zero (configurable), trade count >= require_min_trades, "
            "drawdown_pct <= max_drawdown_pct, profit_to_dd_ratio >= min_profit_to_dd, "
            "HistoryTo is not older than max_history_age_days. Returns ok=True only if "
            "ALL conditions pass — strict by design. Use this as the very last gate "
            "before promoting a strategy to live trading."
        )
    )
    async def pre_live_checklist(args: PreLiveChecklistArgs, ctx: Context) -> dict:
        try:
            path = resolve_safe_path(args.path, must_exist=True)
            info = parse_sqx(path)
            m = derive_metrics(info)
            checks: list[dict[str, Any]] = []
            blockers: list[str] = []

            def _add(name: str, ok: bool, actual: Any, threshold: Any, msg: str) -> None:
                checks.append(
                    {"check": name, "ok": ok, "actual": actual, "threshold": threshold, "message": msg}
                )
                if not ok:
                    blockers.append(name)

            # OOS gate
            oos_fit = m.get("fitness_oos")
            if args.require_oos:
                _add(
                    "oos_present_nonzero",
                    bool(oos_fit and oos_fit > 0),
                    oos_fit,
                    "> 0",
                    "OOS sample required; fitness_oos must be > 0",
                )

            # Trade count
            trades = m.get("trades") or 0
            _add(
                "min_trades",
                trades >= args.require_min_trades,
                trades,
                args.require_min_trades,
                f"need at least {args.require_min_trades} trades",
            )

            # Drawdown ceiling
            dd_pct = m.get("drawdown_pct")
            _add(
                "max_drawdown_pct",
                dd_pct is not None and dd_pct <= args.max_drawdown_pct,
                dd_pct,
                args.max_drawdown_pct,
                f"drawdown_pct must be <= {args.max_drawdown_pct}",
            )

            # Profit-to-DD floor
            ratio = m.get("profit_to_dd_ratio")
            _add(
                "min_profit_to_dd_ratio",
                ratio is not None and ratio >= args.min_profit_to_dd_ratio,
                ratio,
                args.min_profit_to_dd_ratio,
                f"profit_to_dd_ratio must be >= {args.min_profit_to_dd_ratio}",
            )

            # History recency
            history_to_iso = m.get("history_to_iso")
            history_age_days = None
            if history_to_iso:
                from datetime import datetime, timezone
                try:
                    hto = datetime.fromisoformat(history_to_iso.rstrip("Z"))
                    history_age_days = (
                        datetime.now(timezone.utc).replace(tzinfo=None) - hto
                    ).days
                except ValueError:
                    pass
            _add(
                "history_recent",
                history_age_days is not None and history_age_days <= args.max_history_age_days,
                history_age_days,
                args.max_history_age_days,
                f"HistoryTo must be within {args.max_history_age_days} days of now",
            )

            # Engine-flagged problems
            ambiguous = m.get("ambiguous_trades") or 0
            problems = m.get("strategy_problems") or 0
            _add(
                "no_engine_flagged_problems",
                ambiguous == 0 and problems == 0,
                {"ambiguous": ambiguous, "problems": problems},
                {"ambiguous": 0, "problems": 0},
                "engine must report 0 ambiguous trades and 0 strategy problems",
            )

            # Combined anomaly view (informational — does not block on its own)
            anomalies = _audit_strategy_metrics(m)
            critical_anomalies = [a for a in anomalies if a.severity == "critical"]
            if critical_anomalies:
                blockers.append("anomaly_check_critical")
                checks.append(
                    {
                        "check": "anomaly_check_critical",
                        "ok": False,
                        "actual": [a.code for a in critical_anomalies],
                        "threshold": "no critical anomalies",
                        "message": "anomaly check flagged at least one critical issue",
                    }
                )

            return {
                "ok": not blockers,
                "ready_for_live": not blockers,
                "path": str(path),
                "metrics": m,
                "blocker_count": len(blockers),
                "blockers": blockers,
                "checks": checks,
                "anomalies": [a.as_dict() for a in anomalies],
                "history_age_days": history_age_days,
            }
        except (ValidationError, ValueError, FileNotFoundError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Locate every .sqx file in the workspace that shares a given Fingerprint "
            "trades_hash. Useful for tracking a strategy's promotion path (which "
            "projects' databanks does it live in?) and detecting accidental "
            "duplication. Set project_filter to narrow to a single project."
        )
    )
    async def strategy_history(args: StrategyHistoryArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            projects_dir = eng.config.projects_dir
            if not projects_dir.exists():
                return {"ok": False, "error": f"projects dir not found: {projects_dir}"}
            matches: list[dict[str, Any]] = []
            scanned = 0
            for proj_dir in sorted(projects_dir.iterdir()):
                if not proj_dir.is_dir():
                    continue
                if args.project_filter and proj_dir.name != args.project_filter:
                    continue
                db_root = proj_dir / "databanks"
                if not db_root.exists():
                    continue
                try:
                    sqx_paths = list(db_root.rglob("*.sqx"))
                except OSError:
                    continue
                for sp in sqx_paths:
                    scanned += 1
                    try:
                        info = parse_sqx(sp)
                    except (ValueError, OSError):
                        continue
                    if (
                        info.fingerprint
                        and info.fingerprint.trades_hash == args.trades_hash
                    ):
                        m = derive_metrics(info)
                        matches.append(
                            {
                                "path": str(sp),
                                "project": proj_dir.name,
                                "databank": (
                                    sp.relative_to(db_root).parts[0]
                                    if sp.is_relative_to(db_root)
                                    else None
                                ),
                                "metrics": m,
                            }
                        )
            return {
                "ok": True,
                "trades_hash": args.trades_hash,
                "scanned": scanned,
                "match_count": len(matches),
                "matches": matches,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)
