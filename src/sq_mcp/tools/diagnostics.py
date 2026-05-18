"""Cross-cutting health & cleanup tools.

  * health_check: roll-up of engine + license + data + projects + registry +
    recent error tail. One-call situational awareness.
  * workspace_cleanup: bulk-remove projects matching a glob pattern, with
    dry-run safety and engine-aware ordering (stop + engine-remove BEFORE rm).
"""

from __future__ import annotations

import fnmatch
import shutil
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.projects import (
    _parse_license_info,
    _scan_projects_fs,
)


class WorkspaceCleanupArgs(BaseModel):
    pattern: str = Field(
        ...,
        description=(
            "Glob pattern matched against project directory names "
            "(e.g. 'TEST_*', '*_AUTO_*', 'Builder_v?'). Use '*' to wipe everything "
            "(don't, unless that's really the intent). Case-insensitive."
        ),
    )
    keep: list[str] = Field(
        default_factory=list,
        description=(
            "Project names to NEVER delete even if matched. Sensible defaults: the "
            "official Builder/Optimizer/Retester template projects."
        ),
    )
    dry_run: bool = Field(
        True,
        description=(
            "If True (default), only report what WOULD be removed. Flip to False to "
            "actually delete. Required to be True on first call for safety."
        ),
    )
    delete_directory: bool = Field(
        True,
        description="rm -rf the project dir after engine-side removal succeeds.",
    )
    confirm_token: str | None = Field(
        None,
        description=(
            "When dry_run=False, pass the token returned by the dry-run call to confirm "
            "you actually intended this. Prevents accidental wipes."
        ),
    )

    @field_validator("pattern")
    @classmethod
    def _v_pattern(cls, v: str) -> str:
        if not v:
            raise ValueError("pattern must be non-empty")
        if len(v) > 200:
            raise ValueError("pattern too long (max 200 chars)")
        if any(c in v for c in ("\n", "\r", "\x00")):
            raise ValueError("pattern contains forbidden characters")
        # Block path separators — pattern matches directory NAMES, not paths.
        if "/" in v or "\\" in v:
            raise ValueError(
                "pattern must match directory names only — no '/' or '\\\\' allowed"
            )
        if ".." in v:
            raise ValueError("pattern must not contain '..'")
        return v

    @field_validator("keep")
    @classmethod
    def _v_keep(cls, v: list[str]) -> list[str]:
        if len(v) > 100:
            raise ValueError("keep list too long (max 100 entries)")
        return [validate_project_name(n) for n in v]


def _dry_run_token(pattern: str, targets: list[str]) -> str:
    """Stable hash-style token from pattern + targets. Lets the caller confirm intent.

    SHA-256 truncated to 10 hex chars. Stable across Python runs (unlike hash()),
    crucial because the caller passes the token back in a separate call.
    """
    import hashlib
    raw = f"{pattern}|" + ",".join(sorted(targets))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "One-call health rollup: engine running/attached/pid, license info + "
            "days_remaining, data registry presence, project count on disk, recent "
            "fatal-error tail. Useful as the very first step of an autonomous run so the "
            "agent knows what's available before deciding what to do."
        )
    )
    async def health_check(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            checks: dict[str, Any] = {}
            blockers: list[str] = []
            warnings: list[str] = []

            # engine state
            checks["engine"] = {
                "running": eng.is_running,
                "attached": eng.attached,
                "pid": eng.pid,
                "http_url": eng.config.http_url,
                "sqcli": str(eng.config.sqcli),
                "fatal_error": eng._state.fatal_error,
            }
            if not eng.is_running:
                blockers.append("engine not running")

            # license — only meaningful when engine is up
            if eng.is_running:
                try:
                    raw = await eng.call("-license action=info", timeout=10.0)
                    lic = _parse_license_info(raw)
                    checks["license"] = lic
                    if lic.get("expires_iso"):
                        from datetime import datetime, timezone
                        try:
                            exp = datetime.fromisoformat(lic["expires_iso"])
                            days = (exp - datetime.now(timezone.utc).replace(tzinfo=None)).days
                            checks["license"]["days_remaining"] = days
                            if days < 0:
                                blockers.append(
                                    f"license expired on {lic['expires']} ({-days} days ago)"
                                )
                            elif days <= 7:
                                warnings.append(
                                    f"license expires in {days} days ({lic['expires']})"
                                )
                        except ValueError:
                            pass
                except EngineError as exc:
                    checks["license"] = {"error": str(exc)}
                    warnings.append(f"license check failed: {exc}")

            # data
            history_dir = eng.config.history_dir
            data_db = eng.config.data_dir / "data.db"
            checks["data"] = {
                "history_dir": str(history_dir),
                "history_dir_exists": history_dir.exists(),
                "history_symbols_count": (
                    sum(1 for p in history_dir.iterdir() if p.is_dir())
                    if history_dir.exists() else 0
                ),
                "data_db_path": str(data_db),
                "data_db_exists": data_db.is_file(),
                "data_db_size": data_db.stat().st_size if data_db.is_file() else 0,
            }
            if not history_dir.exists():
                warnings.append(f"history dir missing: {history_dir}")
            if not data_db.is_file():
                warnings.append(f"data.db missing: {data_db}")

            # projects
            projects_dir = eng.config.projects_dir
            fs_projects = _scan_projects_fs(projects_dir)
            checks["projects"] = {
                "projects_dir": str(projects_dir),
                "exists": projects_dir.exists(),
                "count": len(fs_projects),
                "names": [p["name"] for p in fs_projects],
            }

            # MT5 (lightweight check — does NOT require Wine)
            try:
                from sq_mcp.tools.mt5 import _resolve_mt5_install_root
                mt5_root = _resolve_mt5_install_root()
                checks["mt5"] = {
                    "install_root": str(mt5_root) if mt5_root else None,
                    "found": mt5_root is not None,
                }
            except Exception:  # noqa: BLE001  -- best effort
                checks["mt5"] = {"error": "MT5 probe failed"}

            # log tail (last 20 non-noise lines)
            tail = eng.recent_log
            error_lines = [
                ln for ln in tail
                if any(k in ln.lower() for k in ("error", "exception", "failed", "exit app"))
            ]
            checks["log_tail"] = {
                "total_buffered": len(tail),
                "last_20": tail[-20:],
                "recent_errors": error_lines[-10:],
            }
            if error_lines:
                warnings.append(f"{len(error_lines)} recent log line(s) contain error markers")

            return {
                "ok": not blockers,
                "ready": not blockers,
                "blocker_count": len(blockers),
                "warning_count": len(warnings),
                "blockers": blockers,
                "warnings": warnings,
                "checks": checks,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Bulk-remove projects matching a glob pattern (e.g. 'TEST_*', '*_AUTO_*'). "
            "Defaults to dry_run=True for safety. Returns a token in dry-run mode that "
            "must be passed back as confirm_token to actually delete. Uses force-remove "
            "ordering: -project action=stop, then action=remove (clears JVM-resident "
            "phantom (2) variants), then rm -rf. The `keep` list prevents accidental "
            "deletion of named templates (Builder/Optimizer/Retester etc.)."
        )
    )
    async def workspace_cleanup(args: WorkspaceCleanupArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            projects_dir = eng.config.projects_dir
            if not projects_dir.exists():
                return {"ok": False, "error": f"projects dir missing: {projects_dir}"}

            keep_set = {n.lower() for n in args.keep}
            pattern_lower = args.pattern.lower()
            candidate_names: list[str] = []
            for entry in sorted(projects_dir.iterdir()):
                if not entry.is_dir():
                    continue
                if not fnmatch.fnmatch(entry.name.lower(), pattern_lower):
                    continue
                if entry.name.lower() in keep_set:
                    continue
                candidate_names.append(entry.name)

            expected_token = _dry_run_token(args.pattern, candidate_names)

            if args.dry_run:
                return {
                    "ok": True,
                    "dry_run": True,
                    "pattern": args.pattern,
                    "kept": sorted(keep_set),
                    "would_remove_count": len(candidate_names),
                    "would_remove": candidate_names,
                    "confirm_token": expected_token,
                    "note": (
                        "Re-call with dry_run=False AND confirm_token to actually delete."
                    ),
                }

            if args.confirm_token != expected_token:
                return {
                    "ok": False,
                    "error": "confirm_token does not match a dry-run for this pattern",
                    "expected_token_for_current_state": expected_token,
                    "hint": (
                        "Re-run with dry_run=True to get the right token, then pass it back."
                    ),
                }

            removed: list[dict[str, Any]] = []
            failed: list[dict[str, Any]] = []
            for name in candidate_names:
                project_dir = projects_dir / name
                steps: list[dict[str, Any]] = []
                # Quote names with spaces for engine commands when possible — sqcli
                # CANNOT reference names with spaces; we skip the engine half and rm directly.
                if " " in name or "(" in name or ")" in name:
                    steps.append(
                        {
                            "action": "engine_skip",
                            "reason": "name contains characters sqcli HTTP parser can't quote",
                        }
                    )
                else:
                    for action in ("stop", "remove"):
                        try:
                            text = await eng.call(
                                f"-project action={action} name={name}", timeout=30.0
                            )
                            steps.append({"action": action, "raw": text.strip()[:200]})
                        except EngineError as exc:
                            steps.append({"action": action, "error": str(exc)})

                if args.delete_directory and project_dir.exists():
                    try:
                        shutil.rmtree(project_dir)
                        steps.append({"action": "rm_dir"})
                        removed.append({"project": name, "steps": steps})
                    except OSError as exc:
                        steps.append({"action": "rm_dir", "error": str(exc)})
                        failed.append({"project": name, "steps": steps})
                else:
                    removed.append({"project": name, "steps": steps})

            return {
                "ok": True,
                "pattern": args.pattern,
                "dry_run": False,
                "removed_count": len(removed),
                "failed_count": len(failed),
                "removed": removed,
                "failed": failed,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Workspace insights: top/worst strategies by profit_to_dd_ratio across "
            "every project, symbol/timeframe distribution, total strategy count, "
            "license outlook (if engine is up), disk usage roll-up. One call to get "
            "the 'state of my trading workshop' big picture."
        )
    )
    async def workspace_insights(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            projects_dir = eng.config.projects_dir
            history_dir = eng.config.history_dir

            from sq_mcp.parsers.sqx import derive_metrics, parse_sqx
            all_strategies: list[dict[str, Any]] = []
            symbol_distribution: dict[str, int] = {}
            tf_distribution: dict[str, int] = {}
            per_project_summary: list[dict[str, Any]] = []

            if projects_dir.exists():
                for proj_dir in sorted(projects_dir.iterdir()):
                    if not proj_dir.is_dir():
                        continue
                    db_root = proj_dir / "databanks"
                    if not db_root.exists():
                        continue
                    proj_count = 0
                    try:
                        sqx_paths = list(db_root.rglob("*.sqx"))
                    except OSError:
                        sqx_paths = []
                    for sp in sqx_paths:
                        try:
                            info = parse_sqx(sp)
                            m = derive_metrics(info)
                        except (ValueError, OSError):
                            continue
                        proj_count += 1
                        sym = m.get("symbol") or "(unknown)"
                        tf = m.get("timeframe") or "(unknown)"
                        symbol_distribution[sym] = symbol_distribution.get(sym, 0) + 1
                        tf_distribution[tf] = tf_distribution.get(tf, 0) + 1
                        all_strategies.append(
                            {
                                "project": proj_dir.name,
                                "path": str(sp),
                                "name": m.get("strategy_name") or sp.stem,
                                "symbol": sym,
                                "timeframe": tf,
                                "trades": m.get("trades"),
                                "net_profit": m.get("net_profit"),
                                "drawdown_abs": m.get("drawdown_abs"),
                                "profit_to_dd_ratio": m.get("profit_to_dd_ratio"),
                                "fitness_oos": m.get("fitness_oos"),
                                "oos_is_ratio": m.get("oos_is_ratio"),
                            }
                        )
                    per_project_summary.append(
                        {"project": proj_dir.name, "strategy_count": proj_count}
                    )

            def _ratio_key(entry: dict) -> float:
                v = entry.get("profit_to_dd_ratio")
                return v if isinstance(v, (int, float)) else float("-inf")

            ranked = sorted(all_strategies, key=_ratio_key, reverse=True)
            top = ranked[:10]
            worst = [r for r in reversed(ranked) if r.get("profit_to_dd_ratio") is not None][:10]

            # License outlook
            license_outlook: dict[str, Any] | None = None
            if eng.is_running:
                try:
                    raw = await eng.call("-license action=info", timeout=10.0)
                    from sq_mcp.tools.projects import _parse_license_info
                    parsed = _parse_license_info(raw)
                    if parsed.get("expires_iso"):
                        from datetime import datetime, timezone
                        try:
                            exp = datetime.fromisoformat(parsed["expires_iso"])
                            days = (
                                exp - datetime.now(timezone.utc).replace(tzinfo=None)
                            ).days
                            parsed["days_remaining"] = days
                        except ValueError:
                            pass
                    license_outlook = parsed
                except EngineError:
                    license_outlook = {"error": "license query failed"}

            # Disk usage — light version
            total_strategy_bytes = 0
            for entry in all_strategies:
                try:
                    total_strategy_bytes += Path(entry["path"]).stat().st_size
                except OSError:
                    pass

            return {
                "ok": True,
                "projects_dir": str(projects_dir),
                "history_dir": str(history_dir),
                "project_count": len(per_project_summary),
                "total_strategy_count": len(all_strategies),
                "symbol_distribution": dict(
                    sorted(symbol_distribution.items(), key=lambda kv: kv[1], reverse=True)
                ),
                "timeframe_distribution": dict(
                    sorted(tf_distribution.items(), key=lambda kv: kv[1], reverse=True)
                ),
                "top_10_by_profit_to_dd": top,
                "worst_10_by_profit_to_dd": worst,
                "per_project_summary": sorted(
                    per_project_summary, key=lambda e: e["strategy_count"], reverse=True
                ),
                "total_strategy_bytes": total_strategy_bytes,
                "total_strategy_mb": round(total_strategy_bytes / (1024 * 1024), 2),
                "license": license_outlook,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Disk-usage report for the SQ X user workspace. Per-project: project.cfx size, "
            "databanks/ total bytes, .sqx file count, snapshot count, last-modified time. "
            "Plus history/ size summary. Use to find disk-hogs before cleanup."
        )
    )
    async def workspace_disk_usage(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            projects_dir = eng.config.projects_dir
            history_dir = eng.config.history_dir

            total_projects_bytes = 0
            project_entries: list[dict[str, Any]] = []
            if projects_dir.exists():
                for entry in sorted(projects_dir.iterdir(), key=lambda p: p.name.lower()):
                    if not entry.is_dir():
                        continue
                    cfx = entry / "project.cfx"
                    if not cfx.is_file():
                        continue
                    proj_bytes = 0
                    sqx_count = 0
                    snapshot_count = 0
                    for fp in entry.rglob("*"):
                        if fp.is_file():
                            try:
                                proj_bytes += fp.stat().st_size
                            except OSError:
                                pass
                            if fp.suffix == ".sqx":
                                sqx_count += 1
                            if fp.name.startswith("project.cfx.bak."):
                                snapshot_count += 1
                    total_projects_bytes += proj_bytes
                    project_entries.append(
                        {
                            "project": entry.name,
                            "bytes": proj_bytes,
                            "mb": round(proj_bytes / (1024 * 1024), 2),
                            "cfx_size": cfx.stat().st_size,
                            "sqx_count": sqx_count,
                            "snapshot_count": snapshot_count,
                        }
                    )
            project_entries.sort(key=lambda e: e["bytes"], reverse=True)

            history_bytes = 0
            history_symbols = 0
            if history_dir.exists():
                for sym in history_dir.iterdir():
                    if not sym.is_dir():
                        continue
                    history_symbols += 1
                    for fp in sym.rglob("*"):
                        if fp.is_file():
                            try:
                                history_bytes += fp.stat().st_size
                            except OSError:
                                pass

            return {
                "ok": True,
                "projects_dir": str(projects_dir),
                "history_dir": str(history_dir),
                "projects_total_bytes": total_projects_bytes,
                "projects_total_mb": round(total_projects_bytes / (1024 * 1024), 2),
                "project_count": len(project_entries),
                "history_total_bytes": history_bytes,
                "history_total_gb": round(history_bytes / (1024 ** 3), 3),
                "history_symbol_count": history_symbols,
                "projects": project_entries,
                "top_disk_hogs": project_entries[:5],
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)
