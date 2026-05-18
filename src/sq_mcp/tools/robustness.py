"""Walk-Forward & Monte Carlo / robustness configuration tools.

These edit the `<CrossChecks>` block inside Retest task XMLs (Retest-Task*.xml)
of a project.cfx, toggling individual checks on/off and tuning their
parameters. All operations are snapshot-safe (auto-backup before write).

CrossCheck names (the `use=` attribute toggles the block):
  * RetestWithHigherPrecision
  * MonteCarloRetest              — randomize history bars, slippage, min distance
  * MonteCarloManipulation        — randomize trade order, skip trades
  * WalkForwardOptimization
  * WalkForwardMatrix
  * RetestOnAdditionalMarkets
  * OptProfileSysParamPermutation
  * SequentialOptimization
  * WhatIf

WalkForward sub-element:
  <WalkForward type="N" period="N_oos" optimization="N_is">
  type values (verified from SQ X Build 143):
    0 — disabled
    1 — anchored
    2 — rolling
  period       = OOS sample size (months / runs)
  optimization = IS sample size (months / runs)
"""

from __future__ import annotations

import io
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from lxml import etree
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_project_name
from sq_mcp._xml import safe_fromstring
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.projects import _is_task_xml, _make_snapshot

# Names of CrossCheck blocks we know how to toggle.
_KNOWN_CROSSCHECKS = (
    "RetestWithHigherPrecision",
    "MonteCarloRetest",
    "MonteCarloManipulation",
    "WalkForwardOptimization",
    "WalkForwardMatrix",
    "RetestOnAdditionalMarkets",
    "OptProfileSysParamPermutation",
    "SequentialOptimization",
    "WhatIf",
)

_WF_TYPE_LABELS = {0: "disabled", 1: "anchored", 2: "rolling"}


class CfxConfigureWalkForwardArgs(BaseModel):
    project: str = Field(..., description="Project to retarget. Must contain Retest-Task*.xml entries.")
    enable: bool = Field(
        True,
        description=(
            "If True, set <WalkForwardOptimization use='true'>. If False, set use='false'."
        ),
    )
    wf_type: Literal["disabled", "anchored", "rolling"] | None = Field(
        None,
        description=(
            "WF mode. 'anchored' starts IS at the same point and grows the window; "
            "'rolling' slides a fixed-size IS window forward. Omit to keep current value."
        ),
    )
    period_oos: int | None = Field(
        None, ge=1, le=120, description="OOS sample size (e.g. months). Omit to keep current."
    )
    period_is: int | None = Field(
        None, ge=1, le=240, description="IS / optimization sample size. Omit to keep current."
    )
    target: Literal["all_retests", "first_retest"] = Field(
        "all_retests",
        description=(
            "Apply to every Retest-Task XML in the project, or only the first one. "
            "First-only is safer when a project has multiple Retest tasks with different roles."
        ),
    )
    snapshot: bool = Field(True, description="Take a snapshot before patching.")
    snapshot_label: str | None = Field(None, description="Optional snapshot label.")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class CfxConfigureRobustnessArgs(BaseModel):
    project: str = Field(..., description="Project to retarget.")
    enable: dict[str, bool] = Field(
        ...,
        description=(
            "Map of CrossCheck name → enabled. Recognized names: "
            "RetestWithHigherPrecision, MonteCarloRetest, MonteCarloManipulation, "
            "WalkForwardOptimization, WalkForwardMatrix, RetestOnAdditionalMarkets, "
            "OptProfileSysParamPermutation, SequentialOptimization, WhatIf. "
            "Unknown names are ignored with a warning in the response."
        ),
    )
    target: Literal["all_retests", "first_retest"] = Field("all_retests")
    snapshot: bool = Field(True)
    snapshot_label: str | None = Field(None)

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("enable")
    @classmethod
    def _v_enable(cls, v: dict[str, bool]) -> dict[str, bool]:
        if not v:
            raise ValueError("enable map must have at least one entry")
        clean: dict[str, bool] = {}
        for k, val in v.items():
            if not isinstance(k, str) or not k:
                raise ValueError(f"enable key must be a non-empty string, got {k!r}")
            if not isinstance(val, bool):
                raise ValueError(f"enable[{k!r}] must be a bool, got {type(val).__name__}")
            clean[k] = val
        return clean


def _patch_walkforward_in_xml(
    xml_bytes: bytes,
    *,
    enable: bool,
    wf_type: str | None,
    period_oos: int | None,
    period_is: int | None,
) -> tuple[bytes, Counter, bool]:
    """Patch a single Retest task XML's WalkForwardOptimization block.

    Returns (new_bytes, change_counter, was_present). was_present is False if
    the XML has no `<WalkForwardOptimization>` block (e.g. an Optimize task,
    not a Retest task); the caller should skip such files.
    """
    root = safe_fromstring(xml_bytes)
    changes: Counter = Counter()
    wf_outer = root.find(".//WalkForwardOptimization")
    if wf_outer is None:
        return xml_bytes, changes, False

    current_use = wf_outer.get("use") or "false"
    desired_use = "true" if enable else "false"
    if current_use != desired_use:
        wf_outer.set("use", desired_use)
        changes["use"] += 1

    inner = wf_outer.find("Settings/WalkForward")
    if inner is None:
        # Some projects only have the outer block — create the inner so the
        # engine has parameters when the block is enabled.
        settings_el = wf_outer.find("Settings")
        if settings_el is None:
            settings_el = etree.SubElement(wf_outer, "Settings")
        inner = etree.SubElement(settings_el, "WalkForward")
        inner.set("type", "1")
        inner.set("period", "10")
        inner.set("optimization", "15")
        changes["wf_block_created"] += 1

    if wf_type is not None:
        type_val = {"disabled": "0", "anchored": "1", "rolling": "2"}[wf_type]
        if inner.get("type") != type_val:
            inner.set("type", type_val)
            changes["wf_type"] += 1

    if period_oos is not None and inner.get("period") != str(period_oos):
        inner.set("period", str(period_oos))
        changes["wf_period_oos"] += 1
    if period_is is not None and inner.get("optimization") != str(period_is):
        inner.set("optimization", str(period_is))
        changes["wf_period_is"] += 1

    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
        True,
    )


def _patch_robustness_in_xml(
    xml_bytes: bytes, *, enable_map: dict[str, bool]
) -> tuple[bytes, Counter, list[str], list[str]]:
    """Patch CrossCheck use= attrs. Returns (bytes, counter, touched, ignored)."""
    root = safe_fromstring(xml_bytes)
    counter: Counter = Counter()
    touched: list[str] = []
    not_found: list[str] = []
    for name, want in enable_map.items():
        el = root.find(f".//{name}")
        if el is None:
            not_found.append(name)
            continue
        desired = "true" if want else "false"
        current = el.get("use") or "false"
        if current != desired:
            el.set("use", desired)
            counter[name] += 1
            touched.append(f"{name}={'on' if want else 'off'}")
    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        counter,
        touched,
        not_found,
    )


def _apply_to_cfx(
    cfx_path: Path,
    *,
    target_first_only: bool,
    patcher,  # callable: (bytes) -> (bytes, changes, was_present)
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Walk a .cfx, apply `patcher` to every Retest-Task*.xml, write back atomically.

    Writes the rebuilt archive to a sibling temp file first, then os.replace()s it
    over the target. If anything fails before the replace, the original file is
    untouched — and the snapshot taken by the caller remains valid for rollback.

    Returns (overall_summary, per_file_detail).
    """
    per_file: dict[str, dict[str, Any]] = {}
    total_changes: Counter = Counter()
    handled = 0
    skipped: list[str] = []

    buf = io.BytesIO()
    with zipfile.ZipFile(cfx_path, "r") as src, zipfile.ZipFile(
        buf, "w", compression=zipfile.ZIP_DEFLATED
    ) as dst:
        for item in src.infolist():
            raw = src.read(item.filename)
            base = item.filename.rsplit("/", 1)[-1]
            applies = _is_task_xml(item.filename) and base.startswith("Retest-")
            if applies:
                if target_first_only and handled >= 1:
                    skipped.append(item.filename)
                else:
                    try:
                        new_bytes, changes, was_present = patcher(raw)
                    except etree.XMLSyntaxError as exc:
                        per_file[item.filename] = {"error": f"XML parse: {exc}"}
                        new_bytes = raw
                    else:
                        if was_present:
                            handled += 1
                            if changes:
                                raw = new_bytes
                                total_changes.update(changes)
                                per_file[item.filename] = {"changes": dict(changes)}
                            else:
                                per_file[item.filename] = {"changes": {}, "noop": True}
                        else:
                            per_file[item.filename] = {"skipped": "no target block"}
            dst.writestr(item, raw)

    import os
    tmp_path = cfx_path.with_suffix(cfx_path.suffix + ".tmp")
    try:
        tmp_path.write_bytes(buf.getvalue())
        os.replace(tmp_path, cfx_path)  # atomic on POSIX + Windows
    except OSError:
        # Best-effort cleanup of the temp file before re-raising.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise
    return (
        {
            "files_handled": handled,
            "files_skipped_first_only": skipped,
            "total_changes": dict(total_changes),
        },
        per_file,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Configure Walk-Forward analysis on a project's Retest tasks. Toggles the "
            "<WalkForwardOptimization> CrossCheck on/off and tunes its mode (anchored / "
            "rolling), IS period (`optimization`), and OOS period (`period`). Operates "
            "on the Retest-Task*.xml files inside project.cfx, in place, with an "
            "auto-snapshot for rollback. Creates the inner <WalkForward> block if the "
            "project only has the outer wrapper. Type mapping: anchored=1 (IS start "
            "fixed), rolling=2 (IS window slides)."
        )
    )
    async def cfx_configure_walkforward(
        args: CfxConfigureWalkForwardArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}

            snapshot_path = None
            if args.snapshot:
                snapshot_path = _make_snapshot(
                    cfx_path, label=args.snapshot_label or "wf_config"
                )
                await ctx.info(f"snapshot saved: {snapshot_path.name}")

            def _patch(raw: bytes):
                return _patch_walkforward_in_xml(
                    raw,
                    enable=args.enable,
                    wf_type=args.wf_type,
                    period_oos=args.period_oos,
                    period_is=args.period_is,
                )

            summary, per_file = _apply_to_cfx(
                cfx_path,
                target_first_only=(args.target == "first_retest"),
                patcher=_patch,
            )
            return {
                "ok": True,
                "project": args.project,
                "cfx_path": str(cfx_path),
                "snapshot_path": str(snapshot_path) if snapshot_path else None,
                "wf_settings_applied": {
                    "enable": args.enable,
                    "wf_type": args.wf_type,
                    "period_oos": args.period_oos,
                    "period_is": args.period_is,
                    "target": args.target,
                },
                **summary,
                "per_file": per_file,
            }
        except (EngineError, ValidationError, OSError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Enable/disable Monte Carlo and other robustness CrossChecks on a project's "
            "Retest tasks. Pass an `enable` map e.g. {'MonteCarloRetest': true, "
            "'MonteCarloManipulation': true, 'WalkForwardOptimization': true, "
            "'WhatIf': false}. Recognized: RetestWithHigherPrecision, MonteCarloRetest, "
            "MonteCarloManipulation, WalkForwardOptimization, WalkForwardMatrix, "
            "RetestOnAdditionalMarkets, OptProfileSysParamPermutation, "
            "SequentialOptimization, WhatIf. Auto-snapshots before writing."
        )
    )
    async def cfx_configure_robustness(
        args: CfxConfigureRobustnessArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}

            unknown = [name for name in args.enable if name not in _KNOWN_CROSSCHECKS]
            recognized = {k: v for k, v in args.enable.items() if k in _KNOWN_CROSSCHECKS}
            if not recognized:
                return {
                    "ok": False,
                    "error": "no recognized CrossCheck names supplied",
                    "known_crosschecks": list(_KNOWN_CROSSCHECKS),
                    "unknown_supplied": unknown,
                }

            snapshot_path = None
            if args.snapshot:
                snapshot_path = _make_snapshot(
                    cfx_path, label=args.snapshot_label or "robustness_config"
                )
                await ctx.info(f"snapshot saved: {snapshot_path.name}")

            not_found_in_files: dict[str, int] = {}

            def _patch(raw: bytes):
                new_bytes, counter, _touched, not_found = _patch_robustness_in_xml(
                    raw, enable_map=recognized
                )
                for n in not_found:
                    not_found_in_files[n] = not_found_in_files.get(n, 0) + 1
                # was_present=True for the per-file walker even if nothing matched —
                # CrossCheck blocks may be partially present.
                return new_bytes, counter, True

            summary, per_file = _apply_to_cfx(
                cfx_path,
                target_first_only=(args.target == "first_retest"),
                patcher=_patch,
            )
            return {
                "ok": True,
                "project": args.project,
                "cfx_path": str(cfx_path),
                "snapshot_path": str(snapshot_path) if snapshot_path else None,
                "enable_applied": recognized,
                "unknown_crosschecks_ignored": unknown,
                "missing_in_files_count": not_found_in_files,
                **summary,
                "per_file": per_file,
            }
        except (EngineError, ValidationError, OSError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Read-only report of the current Walk-Forward + CrossCheck configuration of "
            "every Retest task XML in a project. Returns per-task: WalkForwardOptimization "
            "use/type/period/optimization, plus the use= flag for every other known "
            "CrossCheck block. Use this before configure_walkforward / configure_robustness "
            "to know what's currently enabled."
        )
    )
    async def cfx_inspect_robustness(args: CfxConfigureWalkForwardArgs, ctx: Context) -> dict:
        # Reuses CfxConfigureWalkForwardArgs only for the `project` field; other fields are ignored
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}

            tasks: list[dict[str, Any]] = []
            with zipfile.ZipFile(cfx_path, "r") as z:
                for name in sorted(z.namelist()):
                    base = name.rsplit("/", 1)[-1]
                    if not (_is_task_xml(name) and base.startswith("Retest-")):
                        continue
                    try:
                        root = safe_fromstring(z.read(name))
                    except etree.XMLSyntaxError as exc:
                        tasks.append({"file": name, "error": f"XML parse: {exc}"})
                        continue
                    entry: dict[str, Any] = {"file": name, "crosschecks": {}}
                    for cc in _KNOWN_CROSSCHECKS:
                        el = root.find(f".//{cc}")
                        if el is not None:
                            entry["crosschecks"][cc] = (el.get("use") or "false").lower() == "true"
                    wf_inner = root.find(".//WalkForwardOptimization/Settings/WalkForward")
                    if wf_inner is not None:
                        t = wf_inner.get("type")
                        try:
                            t_int = int(t) if t is not None else None
                        except ValueError:
                            t_int = None
                        entry["walk_forward"] = {
                            "type": t_int,
                            "type_label": _WF_TYPE_LABELS.get(t_int or -1, "unknown"),
                            "period_oos": wf_inner.get("period"),
                            "period_is": wf_inner.get("optimization"),
                        }
                    tasks.append(entry)
            return {
                "ok": True,
                "project": args.project,
                "cfx_path": str(cfx_path),
                "tasks": tasks,
                "task_count": len(tasks),
                "known_crosschecks": list(_KNOWN_CROSSCHECKS),
            }
        except (EngineError, ValidationError, OSError, zipfile.BadZipFile) as exc:
            return safe_error_payload(exc)
