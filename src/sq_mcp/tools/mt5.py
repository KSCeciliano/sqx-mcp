"""MetaTrader 5 (Wine / native) inspection & EA deployment tools.

These are filesystem-only — no Wine RPC, no terminal-control hooks. The point
is to let the agent verify the MT5 install, see which EAs are currently in
the Experts/ folder, deploy a new .mq5/.ex5 from a SQ build, and tail the
terminal log for backtest progress.

Install locations probed (env var `MT5_HOME` overrides everything):
  Linux Wine prefix: ~/.mt5/drive_c/Program Files/MetaTrader 5/
  macOS:             ~/Library/Application Support/MetaTrader 5/
  Windows:           C:/Program Files/MetaTrader 5/

Data folder ("portable mode") is the same as install dir when portable.txt
exists. Otherwise it lives under %APPDATA%/MetaQuotes/Terminal/<terminal-id>/.
"""

from __future__ import annotations

import os
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, resolve_safe_path
from sq_mcp.tools._common import safe_error_payload


# Default install-root probes per platform.
def _default_install_candidates() -> list[Path]:
    home = Path.home()
    s = platform.system()
    if s == "Linux":
        return [
            home / ".mt5" / "drive_c" / "Program Files" / "MetaTrader 5",
            home / ".wine" / "drive_c" / "Program Files" / "MetaTrader 5",
        ]
    if s == "Darwin":
        return [
            home / "Library" / "Application Support" / "MetaTrader 5",
            home / "Library" / "Application Support" / "net.metaquotes.wine.metatrader5"
            / "drive_c" / "Program Files" / "MetaTrader 5",
        ]
    if s == "Windows":
        return [
            Path("C:/Program Files/MetaTrader 5"),
            Path("C:/Program Files (x86)/MetaTrader 5"),
        ]
    return []


def _resolve_mt5_install_root() -> Path | None:
    """Find the MT5 install root. Honors MT5_HOME env var."""
    env_val = os.environ.get("MT5_HOME")
    candidates: list[Path] = []
    if env_val:
        candidates.append(Path(env_val).expanduser())
    candidates.extend(_default_install_candidates())
    for cand in candidates:
        if (cand / "MQL5").is_dir():
            return cand
    return None


def _enumerate_files(
    root: Path, *, suffixes: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Recursively list files with given extensions, returning size + mtime."""
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in suffixes:
            continue
        try:
            stat = p.stat()
            out.append(
                {
                    "name": p.name,
                    "rel_path": str(p.relative_to(root)),
                    "abs_path": str(p),
                    "size_bytes": stat.st_size,
                    "mtime": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
        except OSError:
            continue
    return out


class Mt5DeployEaArgs(BaseModel):
    source_path: str = Field(
        ..., description="Source .mq5 or .ex5 path (e.g. an SQ-exported EA)."
    )
    subdir: str | None = Field(
        None,
        description=(
            "Optional subfolder under MQL5/Experts/ to deploy into (e.g. 'SQ', 'Polymarket'). "
            "Created if missing. Defaults to MQL5/Experts/ root."
        ),
    )
    rename_to: str | None = Field(
        None,
        description="If set, rename the deployed file (e.g. 'GBPJPY_strat1.mq5').",
    )
    overwrite: bool = Field(
        False,
        description=(
            "If False (default), refuse to overwrite an existing destination file. "
            "If True, replace it (after backing up via .bak suffix)."
        ),
    )

    @field_validator("subdir")
    @classmethod
    def _v_subdir(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > 128:
            raise ValueError("subdir too long (max 128 chars)")
        # Only allow path component characters + forward slash for nested dirs.
        # Block backslash to prevent Windows-style escapes confusing the path parser.
        cleaned = (
            v.replace("_", "")
             .replace("-", "")
             .replace(" ", "")
             .replace("/", "")
        )
        if cleaned and not cleaned.isalnum():
            raise ValueError("subdir may only contain letters, digits, _, -, space and /")
        if v.startswith(("/", "\\")):
            raise ValueError("subdir must be relative (no leading separator)")
        if "\\" in v:
            raise ValueError("subdir must use forward slashes, not backslashes")
        # Block any path traversal token, even nested ("a/../b").
        parts = [p for p in v.split("/") if p]
        if any(p in ("..", ".") for p in parts):
            raise ValueError("subdir may not contain '..' or '.' segments")
        return v

    @field_validator("rename_to")
    @classmethod
    def _v_rename(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > 128:
            raise ValueError("rename_to too long (max 128 chars)")
        if "/" in v or "\\" in v or ".." in v:
            raise ValueError("rename_to must be a bare filename, no path separators")
        if v in ("", ".", ".."):
            raise ValueError("rename_to must be a real filename")
        if not v.endswith((".mq5", ".ex5")):
            raise ValueError("rename_to must end in .mq5 or .ex5")
        return v


class Mt5LogTailArgs(BaseModel):
    log_type: Literal["terminal", "mql5"] = Field(
        "terminal",
        description=(
            "'terminal' = MetaTrader 5/logs/YYYYMMDD.log (terminal-wide); "
            "'mql5' = MetaTrader 5/MQL5/logs/YYYYMMDD.log (EA-specific)."
        ),
    )
    date: str | None = Field(
        None,
        description=(
            "yyyymmdd to read (e.g. '20260518'). Defaults to most recent log file."
        ),
        pattern=r"^\d{8}$",
    )
    lines: int = Field(
        200, ge=1, le=10_000,
        description="Max number of lines from the END of the log file.",
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Locate the MetaTrader 5 installation. Returns the install root, MQL5 path, "
            "and platform-specific data folder (portable vs roaming). Honors `MT5_HOME` "
            "env var if you have a non-standard install. Pure filesystem probe — does "
            "not run MT5 or Wine."
        )
    )
    async def mt5_locate(ctx: Context) -> dict:
        try:
            root = _resolve_mt5_install_root()
            if root is None:
                return {
                    "ok": False,
                    "error": "MT5 install not found",
                    "candidates_checked": [str(p) for p in _default_install_candidates()],
                    "hint": "Set MT5_HOME=/path/to/MetaTrader 5 if installed in a non-default location.",
                }
            mql5 = root / "MQL5"
            portable_flag_candidates: list[Path] = []
            roaming_terminals: list[Path] = []
            if platform.system() == "Linux":
                roaming_root = (
                    Path.home() / ".mt5" / "drive_c" / "users" / os.environ.get("USER", "davserver")
                    / "AppData" / "Roaming" / "MetaQuotes" / "Terminal"
                )
            elif platform.system() == "Darwin":
                roaming_root = (
                    Path.home() / "Library" / "Application Support" / "net.metaquotes.wine.metatrader5"
                    / "drive_c" / "users" / os.environ.get("USER", "")
                    / "AppData" / "Roaming" / "MetaQuotes" / "Terminal"
                )
            else:
                roaming_root = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
            if roaming_root.is_dir():
                for entry in sorted(roaming_root.iterdir()):
                    if entry.is_dir():
                        roaming_terminals.append(entry)
                        portable_flag_candidates.append(entry / "portable.txt")
            is_portable = any(p.is_file() for p in portable_flag_candidates) or (
                root / "portable.txt"
            ).is_file()

            return {
                "ok": True,
                "install_root": str(root),
                "mql5_dir": str(mql5),
                "experts_dir": str(mql5 / "Experts"),
                "indicators_dir": str(mql5 / "Indicators"),
                "scripts_dir": str(mql5 / "Scripts"),
                "include_dir": str(mql5 / "Include"),
                "libraries_dir": str(mql5 / "Libraries"),
                "files_dir": str(mql5 / "Files"),
                "terminal_log_dir": str(root / "logs"),
                "mql5_log_dir": str(mql5 / "logs"),
                "portable_mode": is_portable,
                "roaming_terminal_ids": [t.name for t in roaming_terminals],
                "platform": platform.system(),
                "note": (
                    "Portable mode uses the install dir for all data; roaming mode uses "
                    "AppData/Roaming/MetaQuotes/Terminal/<id>/."
                ),
            }
        except OSError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "List Expert Advisors (.mq5 source + .ex5 compiled) currently present in "
            "MQL5/Experts/. Recursive. Returns name, relative subfolder, size, mtime. "
            "Useful to verify an SQ-exported EA was actually deployed."
        )
    )
    async def mt5_list_experts(ctx: Context) -> dict:
        try:
            root = _resolve_mt5_install_root()
            if root is None:
                return {"ok": False, "error": "MT5 install not found"}
            experts_dir = root / "MQL5" / "Experts"
            entries = _enumerate_files(experts_dir, suffixes=(".mq5", ".ex5"))
            mq5_count = sum(1 for e in entries if e["name"].endswith(".mq5"))
            ex5_count = sum(1 for e in entries if e["name"].endswith(".ex5"))
            return {
                "ok": True,
                "experts_dir": str(experts_dir),
                "exists": experts_dir.is_dir(),
                "total_files": len(entries),
                "mq5_count": mq5_count,
                "ex5_count": ex5_count,
                "entries": entries,
            }
        except OSError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "List custom indicators (MQL5/Indicators/). Same shape as mt5_list_experts."
        )
    )
    async def mt5_list_indicators(ctx: Context) -> dict:
        try:
            root = _resolve_mt5_install_root()
            if root is None:
                return {"ok": False, "error": "MT5 install not found"}
            ind_dir = root / "MQL5" / "Indicators"
            entries = _enumerate_files(ind_dir, suffixes=(".mq5", ".ex5"))
            return {
                "ok": True,
                "indicators_dir": str(ind_dir),
                "exists": ind_dir.is_dir(),
                "total_files": len(entries),
                "entries": entries,
            }
        except OSError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Deploy an Expert Advisor (.mq5 / .ex5) to MQL5/Experts/, optionally into a "
            "subfolder. Refuses to overwrite an existing file unless overwrite=True (in "
            "which case the previous file is renamed to .bak first). Useful after SQ X "
            "exports a strategy and you want to drop it straight into MT5 for backtesting. "
            "Does NOT run the EA — MT5 has to be (re)started to pick up new .ex5 files."
        )
    )
    async def mt5_deploy_ea(args: Mt5DeployEaArgs, ctx: Context) -> dict:
        try:
            root = _resolve_mt5_install_root()
            if root is None:
                return {"ok": False, "error": "MT5 install not found"}
            src = resolve_safe_path(args.source_path, must_exist=True)
            if src.suffix.lower() not in (".mq5", ".ex5"):
                return {
                    "ok": False,
                    "error": f"source must be .mq5 or .ex5, got {src.suffix!r}",
                }
            experts_dir = root / "MQL5" / "Experts"
            if args.subdir:
                experts_dir = experts_dir / args.subdir
            experts_dir.mkdir(parents=True, exist_ok=True)
            dst_name = args.rename_to or src.name
            dst = experts_dir / dst_name
            backup_path: Path | None = None
            if dst.exists():
                if not args.overwrite:
                    return {
                        "ok": False,
                        "error": f"destination already exists: {dst}",
                        "hint": "Re-run with overwrite=True to replace (a .bak is kept).",
                    }
                backup_path = dst.with_suffix(dst.suffix + ".bak")
                shutil.move(str(dst), str(backup_path))
            shutil.copy2(src, dst)
            return {
                "ok": True,
                "src": str(src),
                "dest": str(dst),
                "backup": str(backup_path) if backup_path else None,
                "experts_dir": str(experts_dir),
                "note": (
                    "Restart MetaTrader 5 (or use F4 → File → Refresh) so the new EA appears "
                    "in the Navigator. For .mq5 source, compile in MetaEditor first."
                ),
            }
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Tail the MetaTrader 5 log file. Two scopes: 'terminal' = the platform-wide "
            "<install>/logs/YYYYMMDD.log; 'mql5' = the EA-specific MQL5/logs/YYYYMMDD.log. "
            "Defaults to today's log (or most recent if absent). Returns the last N lines. "
            "Use this to debug an EA backtest run or check why a deployed EA fails to load."
        )
    )
    async def mt5_log_tail(args: Mt5LogTailArgs, ctx: Context) -> dict:
        try:
            root = _resolve_mt5_install_root()
            if root is None:
                return {"ok": False, "error": "MT5 install not found"}
            log_dir = (root / "logs") if args.log_type == "terminal" else (root / "MQL5" / "logs")
            if not log_dir.is_dir():
                return {
                    "ok": True,
                    "log_dir": str(log_dir),
                    "exists": False,
                    "lines": [],
                    "note": "log directory missing — MT5 may not have run yet.",
                }
            candidates = sorted(log_dir.glob("*.log"), reverse=True)
            if args.date:
                target_name = f"{args.date}.log"
                pick = next((p for p in candidates if p.name == target_name), None)
                if pick is None:
                    return {
                        "ok": False,
                        "error": f"no log file matching {target_name} in {log_dir}",
                        "available": [p.name for p in candidates[:10]],
                    }
            else:
                pick = candidates[0] if candidates else None
                if pick is None:
                    return {
                        "ok": True,
                        "log_dir": str(log_dir),
                        "lines": [],
                        "note": "log directory empty.",
                    }
            try:
                # MT5 logs are UTF-16LE BOM-prefixed on Windows / Wine. We try utf-16 first
                # then fall back to utf-8.
                raw = pick.read_bytes()
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            try:
                text = raw.decode("utf-16")
            except UnicodeError:
                text = raw.decode("utf-8", errors="replace")
            all_lines = text.splitlines()
            tail = all_lines[-args.lines:]
            return {
                "ok": True,
                "log_path": str(pick),
                "total_lines": len(all_lines),
                "returned_lines": len(tail),
                "size_bytes": pick.stat().st_size,
                "lines": tail,
            }
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Health probe for the MT5 install: install present, expected subfolders, "
            "writability of Experts/, free disk space, last-modified log file. Use this "
            "before deploying an EA to confirm the target is ready."
        )
    )
    async def mt5_health_check(ctx: Context) -> dict:
        try:
            root = _resolve_mt5_install_root()
            if root is None:
                return {
                    "ok": False,
                    "found": False,
                    "candidates_checked": [str(p) for p in _default_install_candidates()],
                }
            mql5 = root / "MQL5"
            sub_status: dict[str, bool] = {}
            for sub in ("Experts", "Indicators", "Scripts", "Include", "Libraries", "Files", "logs"):
                sub_status[sub] = (mql5 / sub).is_dir()
            experts_writable = os.access(mql5 / "Experts", os.W_OK)
            stat = shutil.disk_usage(root)
            last_log: str | None = None
            for d in (root / "logs", mql5 / "logs"):
                if d.is_dir():
                    files = sorted(d.glob("*.log"))
                    if files:
                        last_log = str(files[-1])
                        break
            issues: list[str] = []
            if not all(sub_status.values()):
                missing = [k for k, v in sub_status.items() if not v]
                issues.append(f"missing MQL5 subdirs: {missing}")
            if not experts_writable:
                issues.append("MQL5/Experts/ is not writable by the current user")
            if stat.free < 100 * 1024 * 1024:
                issues.append(f"disk free < 100 MB at {root}")
            return {
                "ok": not issues,
                "found": True,
                "install_root": str(root),
                "mql5_subdirs_present": sub_status,
                "experts_writable": experts_writable,
                "disk_free_bytes": stat.free,
                "disk_free_gb": round(stat.free / (1024 ** 3), 2),
                "last_log_file": last_log,
                "issues": issues,
            }
        except OSError as exc:
            return safe_error_payload(exc)
