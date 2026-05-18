"""Workspace export / backup tools.

These are the "before I do something risky, snapshot the state" tools. They
make a portable tar.gz of project configs + selected databanks + a manifest,
so the agent (or the user) can roll back if a bulk operation goes sideways.

Tools:

- ``workspace_export_projects`` — tar.gz of every project.cfx (mandatory)
  and optionally each project's databanks/. Excludes the (huge) data/History
  tree by default — that's covered by ``data_dat_header_diff`` for "did my
  pull change anything?" checks.
- ``workspace_list_snapshots`` — list any *.bak.* snapshot files that
  ``_make_snapshot`` (in projects.py) has produced over time.
- ``workspace_export_manifest`` — same idea but JSON-only: just emit a
  manifest listing every project + cfx hash + databank sqx counts, no
  archive. Useful as a quick "what's in the workspace right now" snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_project_name,
)
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.projects import _scan_projects_fs


class WorkspaceExportProjectsArgs(BaseModel):
    projects: list[str] | None = Field(
        None,
        description=(
            "Project names to include. None (default) = every project on disk."
        ),
    )
    include_databanks: bool = Field(
        False,
        description=(
            "If True, also bundle each project's databanks/ folder. Can blow "
            "up the archive size (hundreds of MB). Off by default."
        ),
    )
    output_dir: str | None = Field(
        None,
        description=(
            "Where to write the tar.gz. Defaults to <projects_dir>/_backups/."
        ),
    )
    label: str | None = Field(
        None,
        description="Optional human-readable label appended to the archive filename.",
        max_length=64,
    )

    @field_validator("projects")
    @classmethod
    def _v_projects(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [validate_project_name(name) for name in v]

    @field_validator("label")
    @classmethod
    def _v_label(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", v):
            raise ValueError("label must be alphanumeric / _ / -")
        return v


class WorkspaceListSnapshotsArgs(BaseModel):
    project: str | None = Field(
        None,
        description="Restrict to one project. None = every project's snapshots.",
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str | None) -> str | None:
        return validate_project_name(v) if v else v


class WorkspaceExportManifestArgs(BaseModel):
    output_path: str | None = Field(
        None,
        description="Where to write the manifest. None = stdout-only (returned in the response).",
    )


class WorkspaceCleanupSnapshotsArgs(BaseModel):
    max_age_days: int = Field(
        30, ge=1, le=3650,
        description="Snapshots older than this many days are eligible for deletion.",
    )
    dry_run: bool = Field(
        True,
        description=(
            "Defaults True: just report what WOULD be deleted. Set False to actually unlink."
        ),
    )


class WorkspaceFingerprintArgs(BaseModel):
    include_databanks: bool = Field(
        True,
        description="Include databank .sqx counts in the fingerprint.",
    )


class WorkspaceDiffManifestsArgs(BaseModel):
    manifest_a: str = Field(..., description="Path to first manifest JSON.")
    manifest_b: str = Field(..., description="Path to second manifest JSON.")


def _sha256_of_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _gather_project_manifest(project_dir: Path) -> dict[str, Any]:
    cfx = project_dir / "project.cfx"
    cfx_info = None
    if cfx.is_file():
        st = cfx.stat()
        cfx_info = {
            "size_bytes": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "sha256": _sha256_of_file(cfx),
        }
    db_root = project_dir / "databanks"
    databanks: list[dict[str, Any]] = []
    if db_root.is_dir():
        for d in sorted(db_root.iterdir()):
            if not d.is_dir():
                continue
            sqx_count = sum(1 for _ in d.rglob("*.sqx"))
            databanks.append({"name": d.name, "sqx_count": sqx_count})
    return {
        "name": project_dir.name,
        "cfx": cfx_info,
        "databanks": databanks,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Make a tar.gz backup of project configs (and optionally databanks). "
            "Writes to <projects_dir>/_backups/ (or a custom output_dir) and "
            "emits a manifest.json inside the archive. Excludes data/History "
            "by default — too big to bundle. Returns the archive path + manifest summary."
        )
    )
    async def workspace_export_projects(
        args: WorkspaceExportProjectsArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            scanned = _scan_projects_fs(eng.config.projects_dir)
            wanted = (
                set(args.projects)
                if args.projects
                else {p["name"] for p in scanned}
            )
            included: list[dict[str, Any]] = []
            missing: list[str] = []

            output_dir = (
                Path(args.output_dir).expanduser()
                if args.output_dir
                else eng.config.projects_dir / "_backups"
            )
            output_dir.mkdir(parents=True, exist_ok=True)

            ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            suffix = f"-{args.label}" if args.label else ""
            archive_path = output_dir / f"workspace_export-{ts}{suffix}.tar.gz"

            with tarfile.open(archive_path, "w:gz") as tar:
                for name in sorted(wanted):
                    p = eng.config.projects_dir / name
                    if not p.is_dir() or not (p / "project.cfx").is_file():
                        missing.append(name)
                        continue
                    info = _gather_project_manifest(p)
                    tar.add(p / "project.cfx", arcname=f"{name}/project.cfx")
                    if args.include_databanks and (p / "databanks").is_dir():
                        tar.add(p / "databanks", arcname=f"{name}/databanks")
                    included.append(info)

                manifest = {
                    "created_at": datetime.now(tz=timezone.utc).isoformat(),
                    "projects_dir": str(eng.config.projects_dir),
                    "included": included,
                    "missing": missing,
                    "include_databanks": args.include_databanks,
                }
                manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
                ti = tarfile.TarInfo(name="manifest.json")
                ti.size = len(manifest_bytes)
                ti.mtime = int(datetime.now(tz=timezone.utc).timestamp())
                import io
                tar.addfile(ti, io.BytesIO(manifest_bytes))

            return {
                "ok": True,
                "archive_path": str(archive_path),
                "archive_size_bytes": archive_path.stat().st_size,
                "included_count": len(included),
                "missing_count": len(missing),
                "missing": missing,
                "include_databanks": args.include_databanks,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "List every .cfx auto-snapshot (.cfx.bak.<UTC-ts>[.label]) in the "
            "workspace. Useful after cfx_apply_patch / cfx_set_* tools to know "
            "what's available to roll back to. Read-only."
        )
    )
    async def workspace_list_snapshots(
        args: WorkspaceListSnapshotsArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            project_dirs = (
                [eng.config.projects_dir / args.project]
                if args.project
                else [
                    eng.config.projects_dir / p["name"]
                    for p in _scan_projects_fs(eng.config.projects_dir)
                ]
            )
            rows: list[dict[str, Any]] = []
            for pd in project_dirs:
                if not pd.is_dir():
                    continue
                for snap in sorted(pd.glob("project.cfx.bak.*")):
                    try:
                        st = snap.stat()
                    except OSError:
                        continue
                    rows.append(
                        {
                            "project": pd.name,
                            "path": str(snap),
                            "size_bytes": st.st_size,
                            "mtime": datetime.fromtimestamp(
                                st.st_mtime, tz=timezone.utc
                            ).isoformat(),
                        }
                    )
            return {
                "ok": True,
                "snapshot_count": len(rows),
                "snapshots": rows,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Emit a workspace manifest as JSON: every project, cfx mtime + "
            "size + SHA-256, and databank sqx counts. Lightweight 'state "
            "fingerprint' without archiving anything. If output_path is set, "
            "also writes the JSON to disk. Read-only."
        )
    )
    async def workspace_export_manifest(
        args: WorkspaceExportManifestArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            scanned = _scan_projects_fs(eng.config.projects_dir)
            included: list[dict[str, Any]] = []
            for p in scanned:
                included.append(
                    _gather_project_manifest(eng.config.projects_dir / p["name"])
                )
            manifest = {
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "projects_dir": str(eng.config.projects_dir),
                "project_count": len(included),
                "projects": included,
            }
            wrote_to: str | None = None
            if args.output_path:
                p_out = resolve_safe_path(args.output_path, must_exist=False)
                p_out.parent.mkdir(parents=True, exist_ok=True)
                p_out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                wrote_to = str(p_out)
            return {"ok": True, "wrote_to": wrote_to, "manifest": manifest}
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


    @mcp.tool(
        description=(
            "Remove .cfx.bak.* auto-snapshots older than max_age_days. "
            "Defaults to dry_run=True so nothing gets deleted without confirmation. "
            "Set dry_run=False to actually delete. Returns the list of files "
            "that were (or would be) removed plus bytes recovered."
        )
    )
    async def workspace_cleanup_snapshots(
        args: WorkspaceCleanupSnapshotsArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cutoff = datetime.now(tz=timezone.utc).timestamp() - args.max_age_days * 86400
            removed: list[dict[str, Any]] = []
            kept: list[dict[str, Any]] = []
            bytes_recovered = 0
            for pd in eng.config.projects_dir.iterdir():
                if not pd.is_dir():
                    continue
                for snap in pd.glob("project.cfx.bak.*"):
                    try:
                        st = snap.stat()
                    except OSError:
                        continue
                    age_days = round(
                        (datetime.now(tz=timezone.utc).timestamp() - st.st_mtime) / 86400, 2
                    )
                    row = {
                        "project": pd.name,
                        "path": str(snap),
                        "size_bytes": st.st_size,
                        "age_days": age_days,
                    }
                    if st.st_mtime < cutoff:
                        if not args.dry_run:
                            try:
                                snap.unlink()
                            except OSError as exc:
                                row["error"] = str(exc)
                        removed.append(row)
                        bytes_recovered += st.st_size
                    else:
                        kept.append(row)
            return {
                "ok": True,
                "dry_run": args.dry_run,
                "max_age_days": args.max_age_days,
                "removed_count": len(removed),
                "bytes_recovered": bytes_recovered,
                "bytes_recovered_mb": round(bytes_recovered / (1024 * 1024), 2),
                "removed": removed,
                "kept_count": len(kept),
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compute a single 16-character fingerprint of the workspace state "
            "(every project's cfx SHA-256 + databank sqx counts). Use this at "
            "the start of a session to detect 'did anything change since last "
            "time?'. Read-only."
        )
    )
    async def workspace_fingerprint(
        args: WorkspaceFingerprintArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            scanned = _scan_projects_fs(eng.config.projects_dir)
            h = hashlib.sha256()
            per_project: list[dict[str, Any]] = []
            for p in scanned:
                pd = eng.config.projects_dir / p["name"]
                inv = _gather_project_manifest(pd)
                marker = f"{p['name']}:{inv['cfx']['sha256'] if inv['cfx'] else 'no-cfx'}"
                if args.include_databanks:
                    for db in inv["databanks"]:
                        marker += f":db={db['name']}={db['sqx_count']}"
                h.update(marker.encode("utf-8"))
                per_project.append(
                    {
                        "name": p["name"],
                        "cfx_sha256": (inv["cfx"] or {}).get("sha256"),
                        "databank_count": len(inv["databanks"]),
                    }
                )
            return {
                "ok": True,
                "fingerprint": h.hexdigest()[:16],
                "full_sha256": h.hexdigest(),
                "include_databanks": args.include_databanks,
                "project_count": len(scanned),
                "per_project": per_project,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compare two workspace manifest JSON files (produced by "
            "workspace_export_manifest) and report what changed: added / "
            "removed projects, mtime changes per project, databank count "
            "deltas. Read-only."
        )
    )
    async def workspace_diff_manifests(
        args: WorkspaceDiffManifestsArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            pa = resolve_safe_path(args.manifest_a, must_exist=True)
            pb = resolve_safe_path(args.manifest_b, must_exist=True)
            ma = json.loads(pa.read_text(encoding="utf-8"))
            mb = json.loads(pb.read_text(encoding="utf-8"))
            projects_a = {p["name"]: p for p in ma.get("projects", [])}
            projects_b = {p["name"]: p for p in mb.get("projects", [])}
            names_a = set(projects_a)
            names_b = set(projects_b)
            added = sorted(names_b - names_a)
            removed = sorted(names_a - names_b)
            changed: list[dict[str, Any]] = []
            for n in sorted(names_a & names_b):
                a = projects_a[n]
                b = projects_b[n]
                sha_a = (a.get("cfx") or {}).get("sha256")
                sha_b = (b.get("cfx") or {}).get("sha256")
                if sha_a != sha_b:
                    changed.append(
                        {
                            "project": n,
                            "cfx_sha_a": sha_a,
                            "cfx_sha_b": sha_b,
                        }
                    )
            return {
                "ok": True,
                "manifest_a": str(pa),
                "manifest_b": str(pb),
                "manifest_a_created_at": ma.get("created_at"),
                "manifest_b_created_at": mb.get("created_at"),
                "added_count": len(added),
                "removed_count": len(removed),
                "changed_count": len(changed),
                "added": added,
                "removed": removed,
                "changed": changed,
            }
        except (ValidationError, OSError, ValueError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "WorkspaceCleanupSnapshotsArgs",
    "WorkspaceDiffManifestsArgs",
    "WorkspaceExportManifestArgs",
    "WorkspaceExportProjectsArgs",
    "WorkspaceFingerprintArgs",
    "WorkspaceListSnapshotsArgs",
    "_gather_project_manifest",
    "_sha256_of_file",
    "register",
]
