"""Safe .sqx file lifecycle tools — rename and quarantine-delete.

These complement ``databank_promote`` / ``databank_merge`` (which act on
groups) by providing safe single-file operations:

- ``sqx_rename`` — rename a single .sqx file. Refuses to overwrite an
  existing target unless ``overwrite=True``. Validates the new name is a
  plain filename (no path separators, no parent traversal).
- ``sqx_safe_delete`` — move a .sqx into a quarantine folder
  (``<databank_dir>/.quarantine/``) instead of permanently deleting.
  Reversible — you can move it back manually if needed.
- ``sqx_safe_delete_purge`` — purge files in quarantine older than N days.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, resolve_safe_path
from sq_mcp.tools._common import safe_error_payload

_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_\-\.]+$")


class SqxRenameArgs(BaseModel):
    sqx_path: str
    new_name: str = Field(
        ...,
        max_length=128,
        description="New filename (no path separators). Must end in .sqx.",
    )
    overwrite: bool = False

    @field_validator("new_name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        if "/" in v or "\\" in v or ".." in v:
            raise ValueError("new_name must be a bare filename")
        if not v.lower().endswith(".sqx"):
            raise ValueError("new_name must end in .sqx")
        if not _FILENAME_PATTERN.match(v):
            raise ValueError("new_name must be alphanumeric / _ / - / .")
        return v


class SqxSafeDeleteArgs(BaseModel):
    sqx_path: str


class SqxSafeDeletePurgeArgs(BaseModel):
    project: str
    databank: str = "Results"
    max_age_days: int = Field(7, ge=1, le=3650)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Rename a .sqx file safely. Validates the new name has no path "
            "separators / traversal tokens. Refuses to overwrite an existing "
            "file unless overwrite=True. Returns old and new absolute paths."
        )
    )
    async def sqx_rename(args: SqxRenameArgs, ctx: Context) -> dict:  # noqa: ARG001
        try:
            src = resolve_safe_path(args.sqx_path, must_exist=True)
            if src.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            dst = src.parent / args.new_name
            if dst.exists():
                if not args.overwrite:
                    return {
                        "ok": False,
                        "error": f"destination exists: {dst}",
                        "hint": "pass overwrite=True to replace",
                    }
                dst_backup = dst.with_suffix(dst.suffix + ".bak")
                shutil.move(str(dst), str(dst_backup))
            shutil.move(str(src), str(dst))
            return {
                "ok": True,
                "renamed_from": str(src),
                "renamed_to": str(dst),
            }
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Move a .sqx into a quarantine folder (<databank>/.quarantine/) "
            "instead of deleting it. Reversible by moving it back. Use this "
            "instead of plain rm when removing strategies — it gives you a "
            "rollback window. Returns the quarantine path."
        )
    )
    async def sqx_safe_delete(
        args: SqxSafeDeleteArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            src = resolve_safe_path(args.sqx_path, must_exist=True)
            if src.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            quarantine = src.parent / ".quarantine"
            quarantine.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dst = quarantine / f"{src.stem}__{ts}{src.suffix}"
            shutil.move(str(src), str(dst))
            return {
                "ok": True,
                "moved_from": str(src),
                "moved_to": str(dst),
                "quarantine_dir": str(quarantine),
                "hint": "move back manually with `mv` to restore",
            }
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Permanently purge .sqx files from a databank's quarantine folder "
            "that are older than max_age_days. Use to reclaim disk space after "
            "you're sure you don't want to roll back. Returns the list of "
            "purged files plus bytes recovered."
        )
    )
    async def sqx_safe_delete_purge(
        args: SqxSafeDeletePurgeArgs, ctx: Context
    ) -> dict:
        try:
            from sq_mcp._validation import validate_databank_name, validate_project_name
            from sq_mcp.tools._common import get_engine
            eng = get_engine(ctx)
            project = validate_project_name(args.project)
            databank = validate_databank_name(args.databank)
            q = eng.config.projects_dir / project / "databanks" / databank / ".quarantine"
            if not q.is_dir():
                return {
                    "ok": True,
                    "quarantine_dir": str(q),
                    "exists": False,
                    "purged_count": 0,
                }
            cutoff = datetime.now(tz=timezone.utc).timestamp() - args.max_age_days * 86400
            purged: list[dict] = []
            bytes_recovered = 0
            for p in q.glob("*.sqx"):
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    bytes_recovered += st.st_size
                    purged.append({"path": str(p), "size_bytes": st.st_size})
                    try:
                        p.unlink()
                    except OSError:
                        purged[-1]["error"] = "unlink failed"
            return {
                "ok": True,
                "quarantine_dir": str(q),
                "purged_count": len(purged),
                "bytes_recovered": bytes_recovered,
                "purged": purged,
            }
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "SqxRenameArgs",
    "SqxSafeDeleteArgs",
    "SqxSafeDeletePurgeArgs",
    "register",
]


# silence unused warning for Path import (used by callers below)
_ = Path
