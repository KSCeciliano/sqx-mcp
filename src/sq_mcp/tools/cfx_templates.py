"""CFX template library — capture, list, apply reusable project configs.

Pattern: the user spends time tuning a project's settings (data range, MM,
fitness, building blocks, genetic options) and wants to reuse those exact
settings for a new symbol/TF.

This module saves a project's task XMLs to a templates directory and lets the
user apply them to a fresh project. Templates are stored at
``<projects_dir>/_cfx_templates/<name>/`` and survive across sessions.

Tools:

- ``cfx_template_capture`` — copy a project's task XMLs (Build/Optimize/etc.)
  to a named template folder under ``_cfx_templates/``.
- ``cfx_template_list`` — list all saved templates.
- ``cfx_template_apply`` — copy a template's task XMLs into an existing
  project's .cfx, replacing them in-place (snapshots first).
- ``cfx_template_delete`` — remove a saved template.
"""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.projects import _is_task_xml, _make_snapshot


def _templates_root(eng) -> Path:  # noqa: ANN001
    return eng.config.projects_dir / "_cfx_templates"


def _safe_name(v: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", v):
        raise ValueError("template name must be alphanumeric / _ / -")
    return v


class CfxTemplateCaptureArgs(BaseModel):
    project: str
    template_name: str = Field(..., max_length=64)
    overwrite: bool = False

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("template_name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return _safe_name(v)


class CfxTemplateApplyArgs(BaseModel):
    project: str
    template_name: str = Field(..., max_length=64)

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("template_name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return _safe_name(v)


class CfxTemplateNameArgs(BaseModel):
    template_name: str = Field(..., max_length=64)

    @field_validator("template_name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return _safe_name(v)


def _capture_task_xmls(
    cfx_path: Path, dest_dir: Path
) -> tuple[list[str], list[str]]:
    """Extract every task XML inside the .cfx to dest_dir/. Returns (captured, skipped)."""
    captured: list[str] = []
    skipped: list[str] = []
    with zipfile.ZipFile(cfx_path, "r") as z:
        for i in z.infolist():
            base = i.filename.rsplit("/", 1)[-1]
            if not _is_task_xml(i.filename):
                skipped.append(i.filename)
                continue
            payload = z.read(i.filename)
            (dest_dir / base).write_bytes(payload)
            captured.append(base)
    return captured, skipped


def _apply_template_to_cfx(
    cfx_path: Path, template_dir: Path
) -> dict[str, Any]:
    """Replace task XMLs in cfx_path with files from template_dir. Atomic write."""
    replaced: list[str] = []
    not_in_target: list[str] = []
    available_templates = {p.name for p in template_dir.glob("*.xml")}

    buf = io.BytesIO()
    with zipfile.ZipFile(cfx_path, "r") as src, zipfile.ZipFile(
        buf, "w", compression=zipfile.ZIP_DEFLATED
    ) as dst:
        for item in src.infolist():
            base = item.filename.rsplit("/", 1)[-1]
            if _is_task_xml(item.filename) and base in available_templates:
                replacement = (template_dir / base).read_bytes()
                dst.writestr(item, replacement)
                replaced.append(item.filename)
            else:
                dst.writestr(item, src.read(item.filename))
        # Track template files that don't have a matching slot in the .cfx
        existing_in_cfx = {
            item.filename.rsplit("/", 1)[-1] for item in src.infolist()
        }
        for tpl_file in available_templates:
            if tpl_file not in existing_in_cfx:
                not_in_target.append(tpl_file)

    import os
    tmp_path = cfx_path.with_suffix(cfx_path.suffix + ".tmp")
    tmp_path.write_bytes(buf.getvalue())
    os.replace(tmp_path, cfx_path)

    return {
        "replaced": replaced,
        "replaced_count": len(replaced),
        "template_files_unused": not_in_target,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Capture an existing project's task XMLs (Build-/Optimize-/Retest-/etc.) "
            "into a named template folder under <projects_dir>/_cfx_templates/. "
            "Useful when you've tuned a project's settings and want to reuse them "
            "for a new symbol. Templates persist across sessions."
        )
    )
    async def cfx_template_capture(
        args: CfxTemplateCaptureArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            templates_root = _templates_root(eng)
            templates_root.mkdir(parents=True, exist_ok=True)
            dest_dir = templates_root / args.template_name
            if dest_dir.exists():
                if not args.overwrite:
                    return {
                        "ok": False,
                        "error": (
                            f"template {args.template_name!r} already exists; "
                            "pass overwrite=True to replace"
                        ),
                        "existing_path": str(dest_dir),
                    }
                shutil.rmtree(dest_dir)
            dest_dir.mkdir(parents=True)

            captured, skipped = _capture_task_xmls(cfx_path, dest_dir)

            # Write a manifest so the template carries its source identity
            (dest_dir / "_template_manifest.json").write_text(
                f'{{\n  "source_project": "{args.project}",\n'
                f'  "captured_at": "{datetime.now(tz=timezone.utc).isoformat()}",\n'
                f'  "task_xml_count": {len(captured)}\n}}\n',
                encoding="utf-8",
            )
            return {
                "ok": True,
                "template_name": args.template_name,
                "template_path": str(dest_dir),
                "source_project": args.project,
                "captured_count": len(captured),
                "captured": captured,
                "skipped_non_task_members": skipped,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "List every saved CFX template under <projects_dir>/_cfx_templates/. "
            "Returns name, task-XML count, captured-at timestamp."
        )
    )
    async def cfx_template_list(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            root = _templates_root(eng)
            if not root.is_dir():
                return {"ok": True, "templates_dir": str(root), "templates": []}
            templates: list[dict[str, Any]] = []
            for d in sorted(root.iterdir()):
                if not d.is_dir():
                    continue
                xmls = list(d.glob("*.xml"))
                manifest_path = d / "_template_manifest.json"
                manifest_str = manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else None
                templates.append(
                    {
                        "name": d.name,
                        "path": str(d),
                        "task_xml_count": len(xmls),
                        "task_xmls": [x.name for x in xmls],
                        "manifest": manifest_str,
                    }
                )
            return {
                "ok": True,
                "templates_dir": str(root),
                "template_count": len(templates),
                "templates": templates,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Apply a saved CFX template to an existing project: replace each "
            "matching task XML inside the .cfx with the template's version. "
            "Snapshots .cfx first. Template files that don't have a matching "
            "slot in the target .cfx are listed as 'unused' (not copied in)."
        )
    )
    async def cfx_template_apply(
        args: CfxTemplateApplyArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            template_dir = _templates_root(eng) / args.template_name
            if not template_dir.is_dir():
                return {"ok": False, "error": f"template not found: {template_dir}"}

            snap = _make_snapshot(cfx_path, label=f"template_apply_{args.template_name}")
            report = _apply_template_to_cfx(cfx_path, template_dir)
            return {
                "ok": True,
                "project": args.project,
                "template_name": args.template_name,
                "snapshot": str(snap),
                **report,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Delete a saved CFX template. Irreversible — removes the folder under "
            "<projects_dir>/_cfx_templates/<name>/."
        )
    )
    async def cfx_template_delete(
        args: CfxTemplateNameArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            template_dir = _templates_root(eng) / args.template_name
            if not template_dir.is_dir():
                return {"ok": False, "error": f"template not found: {template_dir}"}
            shutil.rmtree(template_dir)
            return {
                "ok": True,
                "template_name": args.template_name,
                "removed_path": str(template_dir),
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "CfxTemplateApplyArgs",
    "CfxTemplateCaptureArgs",
    "CfxTemplateNameArgs",
    "_apply_template_to_cfx",
    "_capture_task_xmls",
    "_templates_root",
    "register",
]
