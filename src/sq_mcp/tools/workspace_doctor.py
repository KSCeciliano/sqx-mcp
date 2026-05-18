"""Comprehensive workspace audit — one tool that runs all the smaller checks.

This is a roll-up of:

- ``environment_health_check`` (SQ install, sqcli, engine port)
- ``broker_data_integrity`` (registry vs disk)
- ``data_age_check`` (stale .dat files)
- ``cfx_lint`` for every project
- ``portfolio_audit`` for every project's Results databank (when present)
- ``workspace_fingerprint`` for a quick state hash

The output is one big nested dict + a summary block listing every blocker.
Use this when starting work in a workspace you haven't touched recently.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.backup import _gather_project_manifest
from sq_mcp.tools.cfx_lint import _analyze_cfx, _lint_cfx
from sq_mcp.tools.integrity import (
    _compare_indexes,
    _index_disk_files,
    _index_registry_rows,
)
from sq_mcp.tools.meta import _probe_tcp
from sq_mcp.tools.portfolio import _filter_min_trades, _scan_databank
from sq_mcp.tools.portfolio_audit import _audit_portfolio
from sq_mcp.tools.projects import _scan_projects_fs
from sq_mcp.tools.symbols import _enrich_data_row, _open_data_registry


class WorkspaceDoctorArgs(BaseModel):
    include_cfx_lint: bool = Field(True, description="Run cfx_lint per project.")
    include_portfolio_audit: bool = Field(
        True, description="Run portfolio_audit on each project's Results databank.",
    )
    max_age_days: int = Field(
        14,
        ge=1,
        le=365,
        description="data_age_check threshold (.dat files older than this are 'stale').",
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "One-call deep audit of the whole workspace: environment health, "
            "broker data integrity, stale data files, per-project cfx_lint, "
            "per-project portfolio_audit, and a workspace fingerprint. "
            "Returns blockers list + raw per-check results."
        )
    )
    async def workspace_doctor(args: WorkspaceDoctorArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfg = eng.config

            # ---- Env health ------------------------------------------------
            env: dict[str, Any] = {
                "sqx_home_exists": cfg.sqx_home.exists(),
                "sqcli_binary_exists": cfg.sqcli.exists(),
                "data_dir_exists": cfg.data_dir.exists(),
                "projects_dir_exists": cfg.projects_dir.exists(),
                "history_dir_exists": cfg.history_dir.exists(),
            }
            parsed = urlparse(cfg.http_url)
            env["engine_http_reachable"] = _probe_tcp(
                parsed.hostname or "localhost", parsed.port or cfg.http_port
            )

            # ---- Integrity -------------------------------------------------
            con = _open_data_registry(cfg.data_dir)
            registry_rows: list[dict[str, Any]] = []
            if con is not None:
                try:
                    cur = con.execute("SELECT * FROM DATA")
                    registry_rows = [_enrich_data_row(dict(r)) for r in cur.fetchall()]
                finally:
                    con.close()
            disk_idx = _index_disk_files(cfg.history_dir)
            reg_idx = _index_registry_rows(registry_rows)
            integrity = _compare_indexes(disk_idx, reg_idx)
            integrity_summary = {
                "matched": len(integrity["matched_pairs"]),
                "disk_only": len(integrity["disk_only_pairs"]),
                "registry_only": len(integrity["registry_only_pairs"]),
            }

            # ---- Stale data ------------------------------------------------
            stale: list[dict[str, Any]] = []
            if cfg.history_dir.exists():
                cutoff = datetime.now(tz=timezone.utc).timestamp() - args.max_age_days * 86400
                for p in cfg.history_dir.rglob("*.dat"):
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    if st.st_mtime < cutoff:
                        stale.append(
                            {
                                "path": str(p),
                                "age_days": round(
                                    (datetime.now(tz=timezone.utc).timestamp() - st.st_mtime) / 86400, 2
                                ),
                            }
                        )

            # ---- Per-project: cfx_lint + portfolio_audit -------------------
            projects = _scan_projects_fs(cfg.projects_dir)
            per_project: list[dict[str, Any]] = []
            blockers: list[str] = []
            if not env["engine_http_reachable"]:
                blockers.append("engine HTTP port unreachable")
            if not env["sqcli_binary_exists"]:
                blockers.append("sqcli binary missing")
            for p in projects:
                pd = cfg.projects_dir / p["name"]
                entry: dict[str, Any] = {"name": p["name"]}
                cfx_path = pd / "project.cfx"
                if not cfx_path.is_file():
                    entry["cfx"] = {"error": "missing"}
                    per_project.append(entry)
                    continue
                if args.include_cfx_lint:
                    analysis = _analyze_cfx(cfx_path)
                    findings = _lint_cfx(analysis)
                    entry["cfx_lint"] = {
                        "finding_count": len(findings),
                        "critical_count": sum(1 for f in findings if f.severity == "critical"),
                        "high_count": sum(1 for f in findings if f.severity == "high"),
                    }
                    for f in findings:
                        if f.severity == "critical":
                            blockers.append(f"[{p['name']}] cfx_lint: {f.code}")
                if args.include_portfolio_audit:
                    results_dir = pd / "databanks" / "Results"
                    if results_dir.is_dir():
                        rows, _ = _scan_databank(results_dir)
                        rows = _filter_min_trades(rows, 30)
                        port_findings = _audit_portfolio(rows)
                        entry["portfolio_audit"] = {
                            "row_count": len(rows),
                            "finding_count": len(port_findings),
                            "high_count": sum(
                                1 for f in port_findings if f.severity == "high"
                            ),
                        }
                per_project.append(entry)

            # ---- Fingerprint ----------------------------------------------
            h = hashlib.sha256()
            for p in projects:
                pd = cfg.projects_dir / p["name"]
                inv = _gather_project_manifest(pd)
                h.update(
                    f"{p['name']}:{(inv['cfx'] or {}).get('sha256','')}".encode()
                )
            fingerprint = h.hexdigest()[:16] if projects else "empty"

            return {
                "ok": True,
                "now": datetime.now(tz=timezone.utc).isoformat(),
                "sqx_home": str(cfg.sqx_home),
                "environment": env,
                "integrity_summary": integrity_summary,
                "stale_dat_count": len(stale),
                "stale_dat_sample": stale[:20],
                "project_count": len(projects),
                "per_project": per_project,
                "fingerprint": fingerprint,
                "blockers": blockers,
                "blocker_count": len(blockers),
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = ["WorkspaceDoctorArgs", "register"]

# Suppress unused warning for Path import (used by type hints implicitly via callers)
_ = Path
