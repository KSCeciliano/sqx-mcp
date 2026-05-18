"""CFX project configuration helpers — fitness, money management, date range.

These complement the Walk-Forward / Robustness tools in `robustness.py`. They
operate on the *task XML* files inside a project.cfx (Build-Task*.xml,
Optimize-Task*.xml, etc.), patching specific config blocks in place with an
auto-snapshot for rollback.

Why this module exists: today, the only way to programmatically configure a
project is to hand-edit XML, clone an existing project, or use the GUI. These
helpers give an AI agent a safe, structured way to retarget fitness or MM
without leaving SQ X.
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

from sq_mcp._validation import (
    ValidationError,
    validate_date,
    validate_project_name,
)
from sq_mcp._xml import safe_fromstring
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.projects import _is_task_xml, _make_snapshot

# Most common SQ ranking types that the GUI shows. Not exhaustive — SQ allows
# many more; we accept any string but list these for the description.
_COMMON_RANKING_TYPES = (
    "ReturnDDRatio",
    "NetProfit",
    "ProfitFactor",
    "SQNScore",
    "StatSignificance",
    "ReturnDownsideRatio",
    "Stability",
    "Sharpe",
    "RSquared",
)

# Common Money Management method types in Build 143.
_COMMON_MM_METHODS = (
    "FixedSize",
    "RiskFixedBalancePct",
    "RiskFixedPctOfAccount",
    "FixedAmount",
    "StocksSizeByPrice",
)

# XML element paths considered "task root" — Build / Optimize tasks have the
# config blocks we want to patch. Retest tasks do too but they're handled by
# the robustness module.
_BUILD_LIKE_PREFIXES = ("Build-", "Optimize-")


def _is_build_like_task(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return (
        any(base.startswith(p) for p in _BUILD_LIKE_PREFIXES)
        and base.endswith(".xml")
    )


class CfxSetFitnessArgs(BaseModel):
    project: str = Field(..., description="Project name (resolves to projects_dir / project / project.cfx).")
    ranking_type: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description=(
            "Fitness ranking type. Common: ReturnDDRatio, NetProfit, ProfitFactor, "
            "SQNScore, Stability, Sharpe, ReturnDownsideRatio."
        ),
    )
    method: str | None = Field(
        None,
        max_length=64,
        description=(
            "Optional: override the FitnessCriteria@method attribute (e.g. "
            "'ComputeFromStrategyResult'). Omit to keep current."
        ),
    )
    target: Literal["all_tasks", "first_task"] = Field("all_tasks")
    snapshot: bool = Field(True)
    snapshot_label: str | None = Field(None)

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("ranking_type", "method")
    @classmethod
    def _v_no_bad_chars(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if any(c in v for c in '<>"&'):
            raise ValueError(f"value must not contain XML metacharacters: {v!r}")
        return v


class CfxSetMoneyManagementArgs(BaseModel):
    project: str = Field(..., description="Project name.")
    method_type: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description=(
            "Money management method to activate. Common: FixedSize, "
            "RiskFixedBalancePct, RiskFixedPctOfAccount, FixedAmount, StocksSizeByPrice."
        ),
    )
    params: dict[str, str] | None = Field(
        None,
        description=(
            "Optional map of Param key -> value (string). E.g. {'Size': '0.1'} "
            "for FixedSize, {'Risk': '2', 'StopLoss': '100'} for RiskFixedPctOfAccount. "
            "Only keys that already exist are updated; new ones are NOT created."
        ),
    )
    initial_capital: float | None = Field(
        None,
        ge=0,
        le=1_000_000_000,
        description="Optional: also set the <InitialCapital> value.",
    )
    target: Literal["all_tasks", "first_task"] = Field("all_tasks")
    snapshot: bool = Field(True)
    snapshot_label: str | None = Field(None)

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("method_type")
    @classmethod
    def _v_mm(cls, v: str) -> str:
        if any(c in v for c in '<>"&'):
            raise ValueError(f"method_type must not contain XML metacharacters: {v!r}")
        return v

    @field_validator("params")
    @classmethod
    def _v_params(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("params dict must be non-empty if supplied")
        clean: dict[str, str] = {}
        for k, val in v.items():
            if not isinstance(k, str) or not k:
                raise ValueError(f"param key must be a non-empty string, got {k!r}")
            if any(c in k for c in '<>"&'):
                raise ValueError(f"param key contains XML metacharacters: {k!r}")
            sval = str(val)
            if any(c in sval for c in '<>"&'):
                raise ValueError(f"param value contains XML metacharacters: {sval!r}")
            clean[k] = sval
        return clean


class CfxSetDataRangeArgs(BaseModel):
    project: str = Field(..., description="Project name.")
    date_from: str | None = Field(
        None, description="New start date, format yyyy.MM.dd. Omit to leave unchanged."
    )
    date_to: str | None = Field(
        None, description="New end date, format yyyy.MM.dd. Omit to leave unchanged."
    )
    target: Literal["all_tasks", "first_task"] = Field("all_tasks")
    snapshot: bool = Field(True)
    snapshot_label: str | None = Field(None)

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("date_from", "date_to")
    @classmethod
    def _v_date(cls, v: str | None) -> str | None:
        return validate_date(v) if v else v


class CfxInspectArgs(BaseModel):
    project: str = Field(..., description="Project name.")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


# ---- XML patchers ----------------------------------------------------------


def _patch_fitness_in_xml(
    xml_bytes: bytes, *, ranking_type: str, method: str | None
) -> tuple[bytes, Counter, bool]:
    """Set the FitnessCriteria ranking type (and optionally method) inside a task XML."""
    root = safe_fromstring(xml_bytes)
    changes: Counter = Counter()
    fc = root.find(".//FitnessCriteria")
    if fc is None:
        return xml_bytes, changes, False

    if method is not None:
        cur = fc.get("method")
        if cur != method:
            fc.set("method", method)
            changes["fitness_method"] += 1

    rank_el = fc.find(".//Ranking")
    if rank_el is None:
        # Some legacy structures only have FitnessCriteria/Settings — create it.
        settings_el = fc.find("Settings")
        if settings_el is None:
            settings_el = etree.SubElement(fc, "Settings")
        rank_el = etree.SubElement(settings_el, "Ranking")
        rank_el.set("type", ranking_type)
        changes["ranking_block_created"] += 1
    elif rank_el.get("type") != ranking_type:
        rank_el.set("type", ranking_type)
        changes["ranking_type"] += 1

    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
        True,
    )


def _patch_money_management_in_xml(
    xml_bytes: bytes,
    *,
    method_type: str,
    params: dict[str, str] | None,
    initial_capital: float | None,
) -> tuple[bytes, Counter, bool, list[str]]:
    """Activate one Money Management method, optionally tweak its params and capital.

    Returns (bytes, counter, was_present, available_methods).
    """
    root = safe_fromstring(xml_bytes)
    changes: Counter = Counter()
    mm = root.find(".//MoneyManagement")
    if mm is None:
        return xml_bytes, changes, False, []

    available: list[str] = []
    target_method = None
    for meth in mm.findall("./Method"):
        t = meth.get("type")
        if t:
            available.append(t)
            if t == method_type:
                target_method = meth

    if target_method is None:
        return xml_bytes, changes, True, available

    # Flip use= attrs: only the target gets "true".
    for meth in mm.findall("./Method"):
        desired = "true" if meth is target_method else "false"
        current = meth.get("use") or "false"
        if current != desired:
            meth.set("use", desired)
            changes[f"method_{meth.get('type')}_use={desired}"] += 1

    # Update params on the target method, but never CREATE new ones.
    missing_params: list[str] = []
    if params:
        params_el = target_method.find("./Params")
        if params_el is None:
            missing_params = list(params.keys())
        else:
            param_index = {p.get("key"): p for p in params_el.findall("./Param")}
            for key, value in params.items():
                p = param_index.get(key)
                if p is None:
                    missing_params.append(key)
                    continue
                if (p.text or "") != value:
                    p.text = value
                    changes[f"param_{method_type}_{key}"] += 1

    if missing_params:
        changes[f"params_missing_on_method:{','.join(missing_params)}"] += 1

    if initial_capital is not None:
        cap_el = mm.find("./InitialCapital")
        if cap_el is None:
            cap_el = etree.SubElement(mm, "InitialCapital")
            changes["initial_capital_created"] += 1
        new_text = (
            str(int(initial_capital))
            if float(initial_capital).is_integer()
            else str(initial_capital)
        )
        if (cap_el.text or "") != new_text:
            cap_el.text = new_text
            changes["initial_capital"] += 1

    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
        True,
        available,
    )


def _patch_data_range_in_xml(
    xml_bytes: bytes, *, date_from: str | None, date_to: str | None
) -> tuple[bytes, Counter, bool]:
    """Update dateFrom / dateTo on every Data/Setups/Setup element."""
    root = safe_fromstring(xml_bytes)
    changes: Counter = Counter()
    setups = root.findall(".//Data/Setups/Setup")
    if not setups:
        return xml_bytes, changes, False

    for setup in setups:
        if date_from is not None and setup.get("dateFrom") != date_from:
            setup.set("dateFrom", date_from)
            changes["dateFrom"] += 1
        if date_to is not None and setup.get("dateTo") != date_to:
            setup.set("dateTo", date_to)
            changes["dateTo"] += 1

    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
        True,
    )


# ---- file-level apply ------------------------------------------------------


def _apply_to_cfx_buildlike(
    cfx_path: Path,
    *,
    target_first_only: bool,
    patcher,  # (bytes) -> (bytes, changes, was_present)
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Walk a .cfx, apply patcher to every Build-/Optimize- task XML, write atomically."""
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
            if _is_task_xml(item.filename) and _is_build_like_task(item.filename):
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
        os.replace(tmp_path, cfx_path)
    except OSError:
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


# ---- inspection ------------------------------------------------------------


def _inspect_task_xml(xml_bytes: bytes) -> dict[str, Any]:
    """Read fitness, MM, data range, capital from one task XML. Best-effort."""
    out: dict[str, Any] = {}
    try:
        root = safe_fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        return {"error": f"XML parse: {exc}"}

    # Fitness
    fc = root.find(".//FitnessCriteria")
    if fc is not None:
        rank = fc.find(".//Ranking")
        out["fitness"] = {
            "method": fc.get("method"),
            "ranking_type": rank.get("type") if rank is not None else None,
        }

    # Money Management
    mm = root.find(".//MoneyManagement")
    if mm is not None:
        methods: list[dict[str, Any]] = []
        active = None
        for meth in mm.findall("./Method"):
            t = meth.get("type")
            use = (meth.get("use") or "false").lower() == "true"
            params: dict[str, str] = {}
            for p in meth.findall("./Params/Param"):
                k = p.get("key")
                if k:
                    params[k] = (p.text or "").strip()
            entry = {"type": t, "use": use, "params": params}
            methods.append(entry)
            if use:
                active = entry
        cap_el = mm.find("./InitialCapital")
        out["money_management"] = {
            "active_method": active["type"] if active else None,
            "active_params": active["params"] if active else None,
            "initial_capital": (
                (cap_el.text or "").strip() if cap_el is not None else None
            ),
            "available_methods": [m["type"] for m in methods if m["type"]],
        }

    # Data range (Setup-level)
    setups = root.findall(".//Data/Setups/Setup")
    if setups:
        out["data_setups"] = [
            {
                "dateFrom": s.get("dateFrom"),
                "dateTo": s.get("dateTo"),
                "testPrecision": s.get("testPrecision"),
                "engine": s.get("engine"),
                "chart": (
                    {"symbol": c.get("symbol"), "timeframe": c.get("timeframe"), "spread": c.get("spread")}
                    if (c := s.find("Chart")) is not None
                    else None
                ),
            }
            for s in setups
        ]

    # OOS percentage / hide
    oos = root.find(".//Data/OutOfSample")
    if oos is not None:
        out["out_of_sample"] = dict(oos.attrib)

    # Symbols (top-level Symbols block, in epoch ms)
    sym_block = root.find(".//Symbols")
    if sym_block is not None:
        out["symbols"] = [
            {
                "name": s.get("name"),
                "uSymbol": s.get("uSymbol"),
                "broker": s.get("broker"),
                "source": s.get("source"),
                "precision": s.get("precision"),
                "dateFrom_ms": s.get("dateFrom"),
                "dateTo_ms": s.get("dateTo"),
            }
            for s in sym_block.findall("./Symbol")
        ]

    return out


# ---- public tools ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Set the fitness ranking criterion on a project's Build/Optimize tasks "
            "inside project.cfx. Common ranking types: ReturnDDRatio, NetProfit, "
            "ProfitFactor, SQNScore, Stability, Sharpe, ReturnDownsideRatio. "
            "Optionally override the FitnessCriteria@method attribute. Auto-snapshot "
            "before write."
        )
    )
    async def cfx_set_fitness_criterion(args: CfxSetFitnessArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}

            snapshot_path = None
            if args.snapshot:
                snapshot_path = _make_snapshot(
                    cfx_path, label=args.snapshot_label or "fitness_config"
                )
                await ctx.info(f"snapshot saved: {snapshot_path.name}")

            def _patch(raw: bytes):
                return _patch_fitness_in_xml(
                    raw, ranking_type=args.ranking_type, method=args.method
                )

            summary, per_file = _apply_to_cfx_buildlike(
                cfx_path,
                target_first_only=(args.target == "first_task"),
                patcher=_patch,
            )
            return {
                "ok": True,
                "project": args.project,
                "cfx_path": str(cfx_path),
                "snapshot_path": str(snapshot_path) if snapshot_path else None,
                "applied": {
                    "ranking_type": args.ranking_type,
                    "method": args.method,
                    "target": args.target,
                },
                "common_ranking_types": list(_COMMON_RANKING_TYPES),
                **summary,
                "per_file": per_file,
            }
        except (EngineError, ValidationError, OSError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Activate a Money Management method on a project's Build/Optimize tasks, "
            "and optionally update its parameters and the initial capital. Sets "
            "use='true' on the chosen <Method> and use='false' on all others. Common "
            "method types: FixedSize (param Size), RiskFixedBalancePct (Risk, Decimals, "
            "LotsIfNoMM, MaxLots), RiskFixedPctOfAccount (Risk, StopLoss, ...), "
            "FixedAmount (RiskedMoney, ...). Param keys that don't already exist on "
            "the method are NOT created (they're listed in per_file.changes)."
        )
    )
    async def cfx_set_money_management(
        args: CfxSetMoneyManagementArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}

            snapshot_path = None
            if args.snapshot:
                snapshot_path = _make_snapshot(
                    cfx_path, label=args.snapshot_label or "mm_config"
                )
                await ctx.info(f"snapshot saved: {snapshot_path.name}")

            available_seen: set[str] = set()
            method_found = {"found": False}

            def _patch(raw: bytes):
                new_bytes, counter, was_present, available = _patch_money_management_in_xml(
                    raw,
                    method_type=args.method_type,
                    params=args.params,
                    initial_capital=args.initial_capital,
                )
                for a in available:
                    available_seen.add(a)
                if args.method_type in available:
                    method_found["found"] = True
                return new_bytes, counter, was_present

            summary, per_file = _apply_to_cfx_buildlike(
                cfx_path,
                target_first_only=(args.target == "first_task"),
                patcher=_patch,
            )

            warnings: list[str] = []
            if not method_found["found"]:
                warnings.append(
                    f"method_type {args.method_type!r} not present in any task XML — "
                    f"available methods seen: {sorted(available_seen)}"
                )

            return {
                "ok": True,
                "project": args.project,
                "cfx_path": str(cfx_path),
                "snapshot_path": str(snapshot_path) if snapshot_path else None,
                "applied": {
                    "method_type": args.method_type,
                    "params": args.params,
                    "initial_capital": args.initial_capital,
                    "target": args.target,
                },
                "common_methods": list(_COMMON_MM_METHODS),
                "available_methods_seen": sorted(available_seen),
                "warnings": warnings,
                **summary,
                "per_file": per_file,
            }
        except (EngineError, ValidationError, OSError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Update the Data/Setup dateFrom / dateTo on a project's Build/Optimize "
            "task XMLs. Dates must be yyyy.MM.dd. Either one can be omitted to leave "
            "it unchanged. NOTE: this updates the Setup-level date range; the "
            "<Symbol> dateFrom/dateTo (epoch ms) inside <Symbols> is NOT touched — "
            "those drive data preparation and rarely need updating in tandem."
        )
    )
    async def cfx_set_data_range(args: CfxSetDataRangeArgs, ctx: Context) -> dict:
        try:
            if args.date_from is None and args.date_to is None:
                return {"ok": False, "error": "at least one of date_from / date_to must be supplied"}

            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}

            snapshot_path = None
            if args.snapshot:
                snapshot_path = _make_snapshot(
                    cfx_path, label=args.snapshot_label or "data_range"
                )
                await ctx.info(f"snapshot saved: {snapshot_path.name}")

            def _patch(raw: bytes):
                return _patch_data_range_in_xml(
                    raw, date_from=args.date_from, date_to=args.date_to
                )

            summary, per_file = _apply_to_cfx_buildlike(
                cfx_path,
                target_first_only=(args.target == "first_task"),
                patcher=_patch,
            )
            return {
                "ok": True,
                "project": args.project,
                "cfx_path": str(cfx_path),
                "snapshot_path": str(snapshot_path) if snapshot_path else None,
                "applied": {
                    "date_from": args.date_from,
                    "date_to": args.date_to,
                    "target": args.target,
                },
                **summary,
                "per_file": per_file,
            }
        except (EngineError, ValidationError, OSError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Read-only inspection of project.cfx config: fitness criterion, money "
            "management (active method + params + initial capital), data setup date "
            "ranges, OOS settings, and the embedded Symbols list. Use this before "
            "calling cfx_set_* to know the current state."
        )
    )
    async def cfx_inspect(args: CfxInspectArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}

            tasks: list[dict[str, Any]] = []
            with zipfile.ZipFile(cfx_path, "r") as z:
                for name in sorted(z.namelist()):
                    if not _is_task_xml(name):
                        continue
                    info = _inspect_task_xml(z.read(name))
                    info["file"] = name
                    tasks.append(info)

            return {
                "ok": True,
                "project": args.project,
                "cfx_path": str(cfx_path),
                "task_count": len(tasks),
                "tasks": tasks,
                "common_ranking_types": list(_COMMON_RANKING_TYPES),
                "common_mm_methods": list(_COMMON_MM_METHODS),
            }
        except (EngineError, ValidationError, OSError, zipfile.BadZipFile) as exc:
            return safe_error_payload(exc)


__all__ = [
    "register",
    "_patch_fitness_in_xml",
    "_patch_money_management_in_xml",
    "_patch_data_range_in_xml",
    "_inspect_task_xml",
    "_is_build_like_task",
    "_COMMON_RANKING_TYPES",
    "_COMMON_MM_METHODS",
]
