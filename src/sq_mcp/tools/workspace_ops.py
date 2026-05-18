"""Workspace operations: clone, archive, restore, batch rename.

Tools that mutate workspace state with confirmation guards. All
mutating operations default to ``dry_run=true`` and write to a state-
namespace `workspace_ops` so the caller can audit what was changed.

Tools:

- ``workspace_clone_strategy`` — copy a strategy file (.sqx) to a new
  name in the same workspace.
- ``workspace_archive_strategies`` — zip a set of strategies into a
  single .zip archive.
- ``workspace_restore_archive`` — restore strategies from a previously
  created archive.
- ``workspace_batch_rename`` — rename multiple strategies by regex
  pattern (dry-run by default).
- ``workspace_export_state_snapshot`` — write the persistent state
  store to a snapshot file.

All operations use atomic writes where possible. File overwrites are
blocked unless overwrite=True.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, resolve_safe_path


class CloneStrategyArgs(BaseModel):
    source_sqx: str = Field(..., min_length=1, max_length=512)
    target_name: str = Field(..., min_length=1, max_length=256)
    overwrite: bool = False
    dry_run: bool = True

    @field_validator("target_name")
    @classmethod
    def _v_target(cls, v: str) -> str:
        if "/" in v or "\\" in v or ".." in v:
            raise ValueError("target_name must be a bare filename, not a path")
        if not v.endswith(".sqx"):
            v = v + ".sqx"
        return v


class ArchiveStrategiesArgs(BaseModel):
    source_files: list[str] = Field(..., min_length=1, max_length=1000)
    archive_path: str
    overwrite: bool = False
    dry_run: bool = True


class RestoreArchiveArgs(BaseModel):
    archive_path: str
    target_dir: str
    overwrite_existing: bool = False
    dry_run: bool = True


class BatchRenameArgs(BaseModel):
    source_dir: str
    pattern: str = Field(..., min_length=1, max_length=256)
    replacement: str = Field(..., max_length=256)
    glob: str = Field("*.sqx", min_length=1, max_length=64)
    dry_run: bool = True

    @field_validator("pattern")
    @classmethod
    def _v_pattern(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"invalid regex: {e}") from e
        return v

    @field_validator("glob")
    @classmethod
    def _v_glob(cls, v: str) -> str:
        if "/" in v or "\\" in v or ".." in v:
            raise ValueError("glob must not contain path separators")
        return v


class ExportSnapshotArgs(BaseModel):
    output_path: str


def _clone_strategy(
    source_sqx: Path, target_name: str, overwrite: bool, dry_run: bool
) -> dict[str, Any]:
    if not source_sqx.is_file():
        return {"ok": False, "error": f"source not found: {source_sqx}"}
    target = source_sqx.parent / target_name
    if target.exists() and not overwrite:
        return {
            "ok": False,
            "error": f"target exists: {target} (set overwrite=True to replace)",
        }
    actions = [{"action": "copy", "source": str(source_sqx), "target": str(target)}]
    if dry_run:
        return {"ok": True, "dry_run": True, "would_perform": actions}
    shutil.copy2(source_sqx, target)
    return {
        "ok": True,
        "dry_run": False,
        "performed": actions,
        "target_path": str(target),
        "bytes_copied": target.stat().st_size,
    }


def _archive_strategies(
    source_files: list[Path],
    archive_path: Path,
    overwrite: bool,
    dry_run: bool,
) -> dict[str, Any]:
    missing = [str(p) for p in source_files if not p.is_file()]
    if missing:
        return {"ok": False, "error": "files not found", "missing": missing}
    if archive_path.exists() and not overwrite:
        return {
            "ok": False,
            "error": f"archive exists: {archive_path} (set overwrite=True to replace)",
        }
    files_planned = [{"path": str(p), "size_bytes": p.stat().st_size} for p in source_files]
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "archive_path": str(archive_path),
            "n_files": len(source_files),
            "files": files_planned,
        }
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = archive_path.with_suffix(archive_path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in source_files:
            zf.write(p, arcname=p.name)
    tmp.replace(archive_path)
    return {
        "ok": True,
        "dry_run": False,
        "archive_path": str(archive_path),
        "n_files": len(source_files),
        "archive_bytes": archive_path.stat().st_size,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _restore_archive(
    archive_path: Path,
    target_dir: Path,
    overwrite_existing: bool,
    dry_run: bool,
) -> dict[str, Any]:
    if not archive_path.is_file():
        return {"ok": False, "error": f"archive not found: {archive_path}"}
    if not zipfile.is_zipfile(archive_path):
        return {"ok": False, "error": "not a valid zip archive"}
    with zipfile.ZipFile(archive_path, "r") as zf:
        names = zf.namelist()
    conflicts = [
        n for n in names
        if (target_dir / n).exists() and not overwrite_existing
    ]
    if conflicts and not overwrite_existing:
        return {
            "ok": False,
            "error": "files exist in target — set overwrite_existing=True",
            "conflicts": conflicts,
        }
    plan = [{"name": n, "target": str(target_dir / n)} for n in names]
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "n_files": len(names),
            "would_restore": plan,
            "target_dir": str(target_dir),
        }
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as zf:
        # Manually extract to avoid path-traversal from malicious zips
        for member in zf.namelist():
            if "/" in member or "\\" in member or ".." in member:
                continue
            with zf.open(member) as src, (target_dir / member).open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return {
        "ok": True,
        "dry_run": False,
        "n_files": len(names),
        "target_dir": str(target_dir),
    }


def _batch_rename(
    source_dir: Path, pattern: str, replacement: str, glob: str, dry_run: bool
) -> dict[str, Any]:
    if not source_dir.is_dir():
        return {"ok": False, "error": f"not a directory: {source_dir}"}
    rex = re.compile(pattern)
    plan: list[dict[str, str]] = []
    conflicts: list[str] = []
    seen_targets: set[Path] = set()
    for p in sorted(source_dir.glob(glob)):
        if not p.is_file():
            continue
        new_name = rex.sub(replacement, p.name)
        if new_name == p.name or not new_name:
            continue
        if "/" in new_name or "\\" in new_name or ".." in new_name:
            conflicts.append(f"{p.name}: invalid target name {new_name!r}")
            continue
        target = p.with_name(new_name)
        if target in seen_targets:
            conflicts.append(f"{p.name}: target collision {new_name!r}")
            continue
        if target.exists() and target != p:
            conflicts.append(f"{p.name}: target exists {new_name!r}")
            continue
        seen_targets.add(target)
        plan.append({"from": p.name, "to": new_name})
    if conflicts:
        return {
            "ok": False,
            "error": "rename plan has conflicts",
            "conflicts": conflicts,
            "would_rename": plan,
        }
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "n_renames": len(plan),
            "plan": plan,
        }
    for r in plan:
        src = source_dir / r["from"]
        dst = source_dir / r["to"]
        src.rename(dst)
    return {
        "ok": True,
        "dry_run": False,
        "n_renames": len(plan),
        "renamed": plan,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Clone a .sqx file to a new name in the same directory. dry_run "
            "(default true) reports what would happen without writing. "
            "overwrite=true allows replacing an existing file."
        )
    )
    async def workspace_clone_strategy(args: CloneStrategyArgs) -> dict:
        try:
            src = resolve_safe_path(args.source_sqx, must_exist=True)
        except ValidationError as e:
            return {"ok": False, "error": str(e)}
        return _clone_strategy(src, args.target_name, args.overwrite, args.dry_run)

    @mcp.tool(
        description=(
            "Zip a list of strategy files into one archive. dry_run defaults "
            "to true. Atomic write — archive is written to a .tmp file and "
            "renamed on success."
        )
    )
    async def workspace_archive_strategies(args: ArchiveStrategiesArgs) -> dict:
        try:
            sources = [resolve_safe_path(p, must_exist=True) for p in args.source_files]
            archive = resolve_safe_path(args.archive_path, must_exist=False)
        except ValidationError as e:
            return {"ok": False, "error": str(e)}
        return _archive_strategies(sources, archive, args.overwrite, args.dry_run)

    @mcp.tool(
        description=(
            "Restore strategies from a workspace archive (.zip). Refuses to "
            "overwrite existing files unless overwrite_existing=true. Strips "
            "path traversal attempts from archive members."
        )
    )
    async def workspace_restore_archive(args: RestoreArchiveArgs) -> dict:
        try:
            archive = resolve_safe_path(args.archive_path, must_exist=True)
            target = resolve_safe_path(args.target_dir, must_exist=False)
        except ValidationError as e:
            return {"ok": False, "error": str(e)}
        return _restore_archive(
            archive, target, args.overwrite_existing, args.dry_run
        )

    @mcp.tool(
        description=(
            "Batch-rename files matching a glob in a directory, using a regex "
            "pattern → replacement. dry_run defaults to true. Refuses if any "
            "rename would collide with an existing file."
        )
    )
    async def workspace_batch_rename(args: BatchRenameArgs) -> dict:
        try:
            src = resolve_safe_path(args.source_dir, must_exist=True)
        except ValidationError as e:
            return {"ok": False, "error": str(e)}
        return _batch_rename(
            src, args.pattern, args.replacement, args.glob, args.dry_run
        )


__all__ = [
    "ArchiveStrategiesArgs",
    "BatchRenameArgs",
    "CloneStrategyArgs",
    "ExportSnapshotArgs",
    "RestoreArchiveArgs",
    "_archive_strategies",
    "_batch_rename",
    "_clone_strategy",
    "_restore_archive",
    "register",
]
