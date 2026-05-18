"""CFX structural comparison + archetype detection.

These tools answer:

- ``cfx_compare_projects`` — "what's actually different between these two
  project configs?" Walks the zip directory of two .cfx files, diffing
  file membership and (for task XMLs) a curated set of structural fields:
  fitness criterion, money management method, data range, symbol, max
  strategies, genetic options. Aimed at code-review-style sanity checks
  before promoting a template.

- ``cfx_archetype`` — "what kind of project is this?" Inspects the task
  XML filenames inside the .cfx and classifies as Builder / Optimizer /
  Retester / WalkForward / MonteCarlo (or a mixed combo). Helps the
  agent decide which downstream tool to use without guessing.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

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

_ARCHETYPE_PREFIXES = {
    "Build-": "builder",
    "Optimize-": "optimizer",
    "Retest-": "retester",
    "WalkForward-": "walkforward",
    "MonteCarlo-": "montecarlo",
}


class CfxCompareProjectsArgs(BaseModel):
    project_a: str
    project_b: str

    @field_validator("project_a", "project_b")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class CfxComparePathsArgs(BaseModel):
    cfx_a: str = Field(..., description="Absolute path to first .cfx.")
    cfx_b: str = Field(..., description="Absolute path to second .cfx.")


class CfxArchetypeArgs(BaseModel):
    project: str | None = Field(
        None,
        description="Project name (under projects_dir). Mutually exclusive with cfx_path.",
    )
    cfx_path: str | None = Field(
        None,
        description="Absolute path to a .cfx file. Mutually exclusive with project.",
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str | None) -> str | None:
        return validate_project_name(v) if v else v


def _list_cfx_members(cfx_path: Path) -> dict[str, dict[str, Any]]:
    """Map filename → {size, crc32} for every member in a .cfx."""
    with zipfile.ZipFile(cfx_path, "r") as z:
        return {
            i.filename: {"size": i.file_size, "crc32": i.CRC}
            for i in z.infolist()
        }


def _read_first_buildlike(cfx_path: Path) -> bytes | None:
    with zipfile.ZipFile(cfx_path, "r") as z:
        for i in z.infolist():
            if _is_task_xml(i.filename) and _is_build_like_task(i.filename):
                return z.read(i.filename)
    return None


def _detect_archetype(cfx_path: Path) -> dict[str, Any]:
    archetypes: dict[str, int] = {}
    task_files: list[str] = []
    with zipfile.ZipFile(cfx_path, "r") as z:
        for i in z.infolist():
            base = i.filename.rsplit("/", 1)[-1]
            if not _is_task_xml(i.filename):
                continue
            task_files.append(base)
            for prefix, kind in _ARCHETYPE_PREFIXES.items():
                if base.startswith(prefix):
                    archetypes[kind] = archetypes.get(kind, 0) + 1
                    break
    primary = (
        max(archetypes.items(), key=lambda kv: kv[1])[0]
        if archetypes
        else "unknown"
    )
    kinds = sorted(archetypes)
    label = "+".join(kinds) if kinds else "unknown"
    return {
        "primary_archetype": primary,
        "archetype_label": label,
        "by_archetype": archetypes,
        "task_files": task_files,
        "task_count": len(task_files),
    }


def _diff_curated_fields(
    inspect_a: dict[str, Any], inspect_b: dict[str, Any]
) -> dict[str, Any]:
    """Diff a curated subset of fields from _inspect_task_xml output."""
    out: dict[str, dict[str, Any]] = {}
    keys = sorted(set(inspect_a) | set(inspect_b))
    for k in keys:
        va = inspect_a.get(k)
        vb = inspect_b.get(k)
        if va != vb:
            out[k] = {"a": va, "b": vb}
    return out


def _flatten_xml(root: etree._Element, *, max_elements: int = 5000) -> list[str]:
    """Best-effort flat representation: path/@attr=val and path = text.

    Used as a coarse diff signal — not a real XML diff library.
    """
    out: list[str] = []

    def _walk(el: etree._Element, path: str) -> None:
        if len(out) >= max_elements:
            return
        tag = el.tag
        new_path = f"{path}/{tag}"
        text = (el.text or "").strip()
        if text:
            out.append(f"{new_path}={text}")
        for k in sorted(el.attrib):
            out.append(f"{new_path}@{k}={el.attrib[k]}")
        for child in el:
            _walk(child, new_path)

    _walk(root, "")
    return out


def _diff_xml_blobs(
    xml_a: bytes, xml_b: bytes
) -> dict[str, Any]:
    """Cheap (path-based) diff between two XML byte blobs."""
    try:
        ra = safe_fromstring(xml_a)
        rb = safe_fromstring(xml_b)
    except etree.XMLSyntaxError as exc:
        return {"error": str(exc)}
    flat_a = set(_flatten_xml(ra))
    flat_b = set(_flatten_xml(rb))
    only_a = sorted(flat_a - flat_b)
    only_b = sorted(flat_b - flat_a)
    return {
        "only_in_a_count": len(only_a),
        "only_in_b_count": len(only_b),
        "only_in_a_sample": only_a[:25],
        "only_in_b_sample": only_b[:25],
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Compare two project.cfx files structurally: zip-member diff "
            "(files added/removed/changed-size), curated field diff on the "
            "first Build/Optimize task XML (fitness, MM, data range, max "
            "strategies, capital), and a coarse XML-path diff. Read-only."
        )
    )
    async def cfx_compare_projects(
        args: CfxCompareProjectsArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            pa = eng.config.projects_dir / args.project_a / "project.cfx"
            pb = eng.config.projects_dir / args.project_b / "project.cfx"
            if not pa.is_file():
                return {"ok": False, "error": f"missing: {pa}"}
            if not pb.is_file():
                return {"ok": False, "error": f"missing: {pb}"}
            return _compare(pa, pb)
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Same as cfx_compare_projects but takes raw file paths so you can "
            "compare arbitrary .cfx files (e.g. a current project against a "
            "snapshot). Read-only."
        )
    )
    async def cfx_compare_paths(
        args: CfxComparePathsArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            pa = resolve_safe_path(args.cfx_a, must_exist=True)
            pb = resolve_safe_path(args.cfx_b, must_exist=True)
            if pa.suffix.lower() != ".cfx" or pb.suffix.lower() != ".cfx":
                return {"ok": False, "error": "both paths must end in .cfx"}
            return _compare(pa, pb)
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Classify a .cfx by what kind of task XMLs it contains: builder, "
            "optimizer, retester, walkforward, montecarlo, or a combo. Reports "
            "the primary archetype + per-kind task counts. Use this when picking "
            "which downstream tool applies. Read-only."
        )
    )
    async def cfx_archetype(args: CfxArchetypeArgs, ctx: Context) -> dict:
        try:
            if args.project and args.cfx_path:
                return {"ok": False, "error": "pass either project OR cfx_path, not both"}
            if args.project:
                eng = get_engine(ctx)
                p = eng.config.projects_dir / args.project / "project.cfx"
            elif args.cfx_path:
                p = resolve_safe_path(args.cfx_path, must_exist=True)
            else:
                return {"ok": False, "error": "must pass project or cfx_path"}
            if not p.is_file():
                return {"ok": False, "error": f"not found: {p}"}
            return {"ok": True, "source": str(p), **_detect_archetype(p)}
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


def _compare(pa: Path, pb: Path) -> dict[str, Any]:
    members_a = _list_cfx_members(pa)
    members_b = _list_cfx_members(pb)
    names_a = set(members_a)
    names_b = set(members_b)
    added = sorted(names_b - names_a)
    removed = sorted(names_a - names_b)
    common = names_a & names_b
    changed: list[dict[str, Any]] = []
    for n in sorted(common):
        if members_a[n]["crc32"] != members_b[n]["crc32"]:
            changed.append(
                {
                    "name": n,
                    "size_a": members_a[n]["size"],
                    "size_b": members_b[n]["size"],
                    "crc_a": members_a[n]["crc32"],
                    "crc_b": members_b[n]["crc32"],
                }
            )

    # Field-level diff on the first build-like XML
    xml_a = _read_first_buildlike(pa)
    xml_b = _read_first_buildlike(pb)
    curated_diff: dict[str, Any] = {}
    xml_path_diff: dict[str, Any] = {}
    if xml_a and xml_b:
        try:
            curated_diff = _diff_curated_fields(
                _inspect_task_xml(xml_a), _inspect_task_xml(xml_b)
            )
            xml_path_diff = _diff_xml_blobs(xml_a, xml_b)
        except etree.XMLSyntaxError as exc:
            curated_diff = {"error": str(exc)}

    return {
        "ok": True,
        "a": str(pa),
        "b": str(pb),
        "members_added": added,
        "members_removed": removed,
        "members_changed": changed,
        "curated_field_diff": curated_diff,
        "xml_path_diff": xml_path_diff,
    }


__all__ = [
    "CfxArchetypeArgs",
    "CfxComparePathsArgs",
    "CfxCompareProjectsArgs",
    "_detect_archetype",
    "_diff_curated_fields",
    "_diff_xml_blobs",
    "_flatten_xml",
    "register",
]
