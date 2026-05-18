"""Advanced CFX configuration patchers.

These extend ``cfx_config.py`` with deeper, more surgical patches to the
Builder task XML that the agent might need before kicking off a long run:

- ``cfx_set_trade_caps`` — patch ``BuildTradingOptions`` params (max trades
  per day, exit-on-Friday, exit-at-end-of-day, time-range filter).
- ``cfx_set_sl_pt_range`` — patch ``SLPTOptions`` (min/max stop-loss and
  profit-target pips/percent).
- ``cfx_set_genetic_options`` — patch ``BuildMode`` genetic-evolution caps
  (population size, max generations, IS/OOS ratio).
- ``cfx_set_max_strategies`` — patch ``<MaxStrategies>`` cap and the
  matching ``StopCondition passedStrategies`` value.
- ``cfx_list_building_blocks`` — read-only listing of every ``<Block key>``
  in the Builder XML with its current ``use=true|false`` and category.
- ``cfx_toggle_building_blocks`` — flip ``use`` flags on Block elements
  matching a key prefix or category. Auto-snapshots the .cfx first.

Every write tool reuses the same atomic .cfx swap mechanism as
``cfx_config._apply_to_cfx_buildlike``.
"""

from __future__ import annotations

import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lxml import etree
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_project_name
from sq_mcp._xml import safe_fromstring
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.cfx_config import _apply_to_cfx_buildlike, _is_build_like_task
from sq_mcp.tools.projects import _is_task_xml, _make_snapshot

# ---- argument schemas ------------------------------------------------------


class CfxSetTradeCapsArgs(BaseModel):
    project: str
    max_trades_per_day: int | None = Field(
        None, ge=0, le=1000,
        description="0 = no limit. Patches Param key='MaxTradesPerDay'.",
    )
    exit_at_end_of_day: bool | None = None
    exit_on_friday: bool | None = None
    limit_time_range: bool | None = Field(
        None,
        description=(
            "Toggle the LimitTimeRange filter. Pair with signal_time_range_from/to "
            "(seconds since midnight, e.g. 28800 = 08:00) to actually constrain hours."
        ),
    )
    signal_time_range_from: int | None = Field(None, ge=0, le=86400)
    signal_time_range_to: int | None = Field(None, ge=0, le=86400)
    target_first_only: bool = Field(
        True,
        description="If True (default), patch only the first Build/Optimize task XML.",
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class CfxSetSlPtRangeArgs(BaseModel):
    project: str
    min_sl_pips: float | None = Field(None, ge=0)
    max_sl_pips: float | None = Field(None, ge=0)
    min_pt_pips: float | None = Field(None, ge=0)
    max_pt_pips: float | None = Field(None, ge=0)
    min_sl_percent: float | None = Field(None, ge=0)
    max_sl_percent: float | None = Field(None, ge=0)
    min_pt_percent: float | None = Field(None, ge=0)
    max_pt_percent: float | None = Field(None, ge=0)
    sl_required: bool | None = None
    pt_required: bool | None = None
    target_first_only: bool = True

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class CfxSetGeneticOptionsArgs(BaseModel):
    project: str
    population_size: int | None = Field(None, ge=2, le=10000)
    max_generations: int | None = Field(None, ge=1, le=10000)
    in_sample_ratio_pct: int | None = Field(
        None, ge=10, le=100,
        description=(
            "EvoInSamplePeriod ratio (percent). 50 => 50% IS / 50% OOS. "
            "100 => no OOS holdout (all in-sample)."
        ),
    )
    crossover_probability: int | None = Field(None, ge=0, le=100)
    mutation_probability: int | None = Field(None, ge=0, le=100)
    target_first_only: bool = True

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class CfxSetMaxStrategiesArgs(BaseModel):
    project: str
    max_strategies: int = Field(..., ge=1, le=1_000_000)
    sync_stop_condition: bool = Field(
        True,
        description=(
            "Also patch StopCondition[type='databank-full']/passedStrategies so the "
            "Builder actually stops at the new cap."
        ),
    )
    target_first_only: bool = True

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class CfxListBuildingBlocksArgs(BaseModel):
    project: str
    category_prefix: str | None = Field(
        None, description="Restrict to blocks whose category startswith this."
    )
    only_enabled: bool = Field(False, description="If True, return only Block use='true' rows.")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class CfxSetSetupAttrsArgs(BaseModel):
    project: str
    session: str | None = Field(
        None,
        description="Session attribute on Data/Setups/Setup (e.g. 'No Session', 'NewYork').",
        max_length=64,
    )
    slippage: int | None = Field(None, ge=0, le=100)
    min_dist: int | None = Field(None, ge=0, le=10000)
    engine: str | None = Field(
        None,
        description="Backtest engine ('MetaTrader4', 'MetaTrader5', 'SQ4Engine', etc.).",
        max_length=32,
    )
    chart_spread: int | None = Field(
        None,
        ge=0,
        le=10000,
        description="Spread attribute on the inner <Chart> element.",
    )
    test_precision: str | None = Field(
        None,
        description="testPrecision attribute. '0'=bar-open, '1'=M1, '2'=tick.",
    )
    target_first_only: bool = True

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class CfxToggleBuildingBlocksArgs(BaseModel):
    project: str
    enable: bool = Field(..., description="Target Block use= value: True enables, False disables.")
    key_prefix: str | None = Field(
        None,
        description=(
            "Match Blocks where 'key' attribute starts with this prefix. "
            "Required unless 'category' is set."
        ),
    )
    category: str | None = Field(
        None,
        description=(
            "Match Blocks with this exact 'category' attribute (e.g. 'signals', "
            "'stopLimitBlocks'). Combine with key_prefix to narrow further."
        ),
    )
    target_first_only: bool = True

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


# ---- patchers --------------------------------------------------------------


def _patch_options_params(
    xml_bytes: bytes, *, updates: dict[str, str | None]
) -> tuple[bytes, Counter, bool]:
    """Patch <BuildTradingOptions>/<Params>/<Param key=...> values in-place.

    Only touches Params that already exist; missing keys are silently ignored
    so callers can patch any subset.
    """
    changes: Counter = Counter()
    root = safe_fromstring(xml_bytes)
    params_root = root.find(".//Options/BuildTradingOptions/Params")
    if params_root is None:
        return xml_bytes, changes, False
    for key, new_val in updates.items():
        if new_val is None:
            continue
        for p in params_root.findall(f"Param[@key='{key}']"):
            old = p.text
            if old == new_val:
                continue
            p.text = new_val
            changes[f"BuildTradingOptions/{key}"] += 1
    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
        True,
    )


def _patch_slpt(
    xml_bytes: bytes, *, updates: dict[str, str | None]
) -> tuple[bytes, Counter, bool]:
    """Patch direct text children of <SLPTOptions> (MinSLInPips, MaxSLInPips, ...)."""
    changes: Counter = Counter()
    root = safe_fromstring(xml_bytes)
    slpt = root.find(".//SLPTOptions")
    if slpt is None:
        return xml_bytes, changes, False
    for key, new_val in updates.items():
        if new_val is None:
            continue
        el = slpt.find(key)
        if el is None:
            continue
        if el.text == new_val:
            continue
        el.text = new_val
        changes[f"SLPTOptions/{key}"] += 1
    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
        True,
    )


def _patch_buildmode(
    xml_bytes: bytes, *, updates: dict[str, str | None]
) -> tuple[bytes, Counter, bool]:
    """Patch <BuildMode> direct text children + <EvoInSamplePeriod ratio>."""
    changes: Counter = Counter()
    root = safe_fromstring(xml_bytes)
    bm = root.find(".//BuildMode")
    if bm is None:
        return xml_bytes, changes, False
    for key, new_val in updates.items():
        if new_val is None:
            continue
        if key == "in_sample_ratio":
            el = bm.find("EvoInSamplePeriod")
            if el is None:
                continue
            if el.get("ratio") == new_val:
                continue
            el.set("ratio", new_val)
            changes["BuildMode/EvoInSamplePeriod@ratio"] += 1
            continue
        el = bm.find(key)
        if el is None:
            continue
        if el.text == new_val:
            continue
        el.text = new_val
        changes[f"BuildMode/{key}"] += 1
    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
        True,
    )


def _patch_max_strategies(
    xml_bytes: bytes, *, new_max: int, sync_stop: bool
) -> tuple[bytes, Counter, bool]:
    changes: Counter = Counter()
    root = safe_fromstring(xml_bytes)
    rankings = root.find(".//Rankings")
    if rankings is None:
        return xml_bytes, changes, False
    el = rankings.find("MaxStrategies")
    if el is not None and el.text != str(new_max):
        el.text = str(new_max)
        changes["Rankings/MaxStrategies"] += 1
    if sync_stop:
        for sc in root.findall(".//StopCondition[@type='databank-full']"):
            if sc.get("passedStrategies") != str(new_max):
                sc.set("passedStrategies", str(new_max))
                changes["StopCondition/passedStrategies"] += 1
    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
        True,
    )


def _list_building_blocks(
    xml_bytes: bytes,
    *,
    category_prefix: str | None,
    only_enabled: bool,
) -> list[dict[str, Any]]:
    """Read-only listing of Block elements with their use= and category attrs."""
    out: list[dict[str, Any]] = []
    try:
        root = safe_fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return out
    for blk in root.findall(".//Blocks/BuildingBlocks/Block"):
        cat = blk.get("category") or ""
        if category_prefix and not cat.startswith(category_prefix):
            continue
        use = (blk.get("use") or "false").lower() == "true"
        if only_enabled and not use:
            continue
        out.append(
            {
                "key": blk.get("key"),
                "category": cat,
                "weight": blk.get("weight"),
                "use": use,
            }
        )
    return out


def _patch_blocks_toggle(
    xml_bytes: bytes,
    *,
    enable: bool,
    key_prefix: str | None,
    category: str | None,
) -> tuple[bytes, Counter, bool]:
    """Toggle Block use= on matching blocks. Match is (key_prefix AND category)."""
    changes: Counter = Counter()
    root = safe_fromstring(xml_bytes)
    bb = root.find(".//Blocks/BuildingBlocks")
    if bb is None:
        return xml_bytes, changes, False
    target_use = "true" if enable else "false"
    n_matched = 0
    for blk in bb.findall("Block"):
        key = blk.get("key") or ""
        cat = blk.get("category") or ""
        if key_prefix and not key.startswith(key_prefix):
            continue
        if category and cat != category:
            continue
        n_matched += 1
        if blk.get("use") != target_use:
            blk.set("use", target_use)
            changes[f"BuildingBlocks/{cat}/use={target_use}"] += 1
    changes[f"matched_blocks={n_matched}"] = n_matched
    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
        True,
    )


def _patch_setup_attrs(
    xml_bytes: bytes,
    *,
    setup_updates: dict[str, str | None],
    chart_spread: str | None,
    test_precision: str | None,
) -> tuple[bytes, Counter, bool]:
    """Patch attributes on <Data>/<Setups>/<Setup ...> elements (and inner <Chart>)."""
    changes: Counter = Counter()
    root = safe_fromstring(xml_bytes)
    setups = root.findall(".//Data/Setups/Setup")
    if not setups:
        return xml_bytes, changes, False
    for setup in setups:
        for attr, val in setup_updates.items():
            if val is None:
                continue
            if setup.get(attr) == val:
                continue
            setup.set(attr, val)
            changes[f"Setup/@{attr}"] += 1
        if test_precision is not None and setup.get("testPrecision") != test_precision:
            setup.set("testPrecision", test_precision)
            changes["Setup/@testPrecision"] += 1
        if chart_spread is not None:
            chart = setup.find("Chart")
            if chart is not None and chart.get("spread") != chart_spread:
                chart.set("spread", chart_spread)
                changes["Chart/@spread"] += 1
    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
        True,
    )


def _walk_first_buildlike_xml(cfx_path: Path) -> bytes | None:
    """Return the bytes of the FIRST Build-/Optimize- task XML in the .cfx. Read-only."""
    with zipfile.ZipFile(cfx_path, "r") as src:
        for item in src.infolist():
            if _is_task_xml(item.filename) and _is_build_like_task(item.filename):
                return src.read(item.filename)
    return None


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Patch <BuildTradingOptions> params: max-trades-per-day, "
            "exit-at-end-of-day, exit-on-friday, and the LimitTimeRange filter. "
            "Snapshots the .cfx first; only touches params that already exist."
        )
    )
    async def cfx_set_trade_caps(args: CfxSetTradeCapsArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            snap = _make_snapshot(cfx_path, label="set_trade_caps")

            updates: dict[str, str | None] = {
                "MaxTradesPerDay": (
                    None if args.max_trades_per_day is None else str(args.max_trades_per_day)
                ),
                "ExitAtEndOfDay": (
                    None if args.exit_at_end_of_day is None else str(args.exit_at_end_of_day).lower()
                ),
                "ExitOnFriday": (
                    None if args.exit_on_friday is None else str(args.exit_on_friday).lower()
                ),
                "LimitTimeRange": (
                    None if args.limit_time_range is None else str(args.limit_time_range).lower()
                ),
                "SignalTimeRangeFrom": (
                    None if args.signal_time_range_from is None
                    else str(args.signal_time_range_from)
                ),
                "SignalTimeRangeTo": (
                    None if args.signal_time_range_to is None
                    else str(args.signal_time_range_to)
                ),
            }

            def _p(raw: bytes) -> tuple[bytes, Counter, bool]:
                return _patch_options_params(raw, updates=updates)

            summary, per_file = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p
            )
            return {
                "ok": True,
                "project": args.project,
                "snapshot": str(snap),
                "summary": summary,
                "per_file": per_file,
                "updates_requested": {k: v for k, v in updates.items() if v is not None},
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Patch <SLPTOptions> values: min/max stop-loss and profit-target in "
            "pips or percent, plus the SLRequired / PTRequired flags. "
            "Snapshots the .cfx first; only touches values that already exist."
        )
    )
    async def cfx_set_sl_pt_range(args: CfxSetSlPtRangeArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            snap = _make_snapshot(cfx_path, label="set_sl_pt_range")

            updates: dict[str, str | None] = {
                "MinSLInPips": _fmt(args.min_sl_pips),
                "MaxSLInPips": _fmt(args.max_sl_pips),
                "MinPTInPips": _fmt(args.min_pt_pips),
                "MaxPTInPips": _fmt(args.max_pt_pips),
                "MinSLInPercent": _fmt(args.min_sl_percent),
                "MaxSLInPercent": _fmt(args.max_sl_percent),
                "MinPTInPercent": _fmt(args.min_pt_percent),
                "MaxPTInPercent": _fmt(args.max_pt_percent),
                "SLRequired": _bool_or_none(args.sl_required),
                "PTRequired": _bool_or_none(args.pt_required),
            }

            def _p(raw: bytes) -> tuple[bytes, Counter, bool]:
                return _patch_slpt(raw, updates=updates)

            summary, per_file = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p
            )
            return {
                "ok": True,
                "project": args.project,
                "snapshot": str(snap),
                "summary": summary,
                "per_file": per_file,
                "updates_requested": {k: v for k, v in updates.items() if v is not None},
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Patch the genetic-evolution options in <BuildMode>: PopulationSize, "
            "MaxGenerations, IS/OOS ratio (EvoInSamplePeriod@ratio), crossover/mutation "
            "probabilities. Snapshots the .cfx first."
        )
    )
    async def cfx_set_genetic_options(args: CfxSetGeneticOptionsArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            snap = _make_snapshot(cfx_path, label="set_genetic_options")

            updates: dict[str, str | None] = {
                "PopulationSize": _fmt_int(args.population_size),
                "MaxGenerations": _fmt_int(args.max_generations),
                "CrossoverProbability": _fmt_int(args.crossover_probability),
                "MutationProbability": _fmt_int(args.mutation_probability),
                "in_sample_ratio": _fmt_int(args.in_sample_ratio_pct),
            }

            def _p(raw: bytes) -> tuple[bytes, Counter, bool]:
                return _patch_buildmode(raw, updates=updates)

            summary, per_file = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p
            )
            return {
                "ok": True,
                "project": args.project,
                "snapshot": str(snap),
                "summary": summary,
                "per_file": per_file,
                "updates_requested": {k: v for k, v in updates.items() if v is not None},
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Patch <Rankings>/<MaxStrategies> (and StopCondition passedStrategies "
            "by default) so the Builder caps the databank at the requested count. "
            "Snapshots the .cfx first."
        )
    )
    async def cfx_set_max_strategies(args: CfxSetMaxStrategiesArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            snap = _make_snapshot(cfx_path, label="set_max_strategies")

            def _p(raw: bytes) -> tuple[bytes, Counter, bool]:
                return _patch_max_strategies(
                    raw,
                    new_max=args.max_strategies,
                    sync_stop=args.sync_stop_condition,
                )

            summary, per_file = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p
            )
            return {
                "ok": True,
                "project": args.project,
                "snapshot": str(snap),
                "max_strategies": args.max_strategies,
                "summary": summary,
                "per_file": per_file,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Quick summary of which Builder blocks are currently ENABLED in a "
            "project. Returns the count by category and a flat list of enabled "
            "block keys. Use as a faster discovery step than cfx_list_building_blocks "
            "(which returns every block, enabled or not). Read-only."
        )
    )
    async def cfx_active_blocks(
        args: CfxListBuildingBlocksArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            raw = _walk_first_buildlike_xml(cfx_path)
            if raw is None:
                return {"ok": False, "error": "no Build-/Optimize- task XML"}
            blocks = _list_building_blocks(
                raw,
                category_prefix=args.category_prefix,
                only_enabled=True,
            )
            by_cat: dict[str, int] = {}
            for b in blocks:
                c = b["category"] or "?"
                by_cat[c] = by_cat.get(c, 0) + 1
            return {
                "ok": True,
                "project": args.project,
                "active_block_count": len(blocks),
                "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
                "active_block_keys": [b["key"] for b in blocks],
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Read-only listing of every <Block> in the Builder XML, with its key, "
            "category, weight, and current use=true|false. Use this to discover "
            "what's available before calling cfx_toggle_building_blocks."
        )
    )
    async def cfx_list_building_blocks(
        args: CfxListBuildingBlocksArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            raw = _walk_first_buildlike_xml(cfx_path)
            if raw is None:
                return {
                    "ok": False,
                    "error": "no Build-/Optimize- task XML in this .cfx",
                }
            blocks = _list_building_blocks(
                raw,
                category_prefix=args.category_prefix,
                only_enabled=args.only_enabled,
            )
            by_cat: dict[str, dict[str, int]] = {}
            for b in blocks:
                c = b["category"] or "?"
                by_cat.setdefault(c, {"total": 0, "enabled": 0})
                by_cat[c]["total"] += 1
                if b["use"]:
                    by_cat[c]["enabled"] += 1
            return {
                "ok": True,
                "project": args.project,
                "blocks_count": len(blocks),
                "by_category": by_cat,
                "blocks": blocks,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Patch Setup-level attributes on <Data>/<Setups>/<Setup ...>: session, "
            "slippage, min_dist, engine, plus the inner <Chart> element's spread, "
            "and the Setup's testPrecision. Snapshots .cfx first."
        )
    )
    async def cfx_set_setup_attrs(
        args: CfxSetSetupAttrsArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            snap = _make_snapshot(cfx_path, label="set_setup_attrs")

            setup_updates: dict[str, str | None] = {
                "session": args.session,
                "slippage": _fmt_int(args.slippage),
                "minDist": _fmt_int(args.min_dist),
                "engine": args.engine,
            }

            def _p(raw: bytes) -> tuple[bytes, Counter, bool]:
                return _patch_setup_attrs(
                    raw,
                    setup_updates=setup_updates,
                    chart_spread=_fmt_int(args.chart_spread),
                    test_precision=args.test_precision,
                )

            summary, per_file = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p
            )
            return {
                "ok": True,
                "project": args.project,
                "snapshot": str(snap),
                "summary": summary,
                "per_file": per_file,
                "updates_requested": {
                    "session": args.session,
                    "slippage": args.slippage,
                    "min_dist": args.min_dist,
                    "engine": args.engine,
                    "chart_spread": args.chart_spread,
                    "test_precision": args.test_precision,
                },
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Enable or disable <Block> entries in the Builder XML by key prefix "
            "and/or category (e.g. category='signals' to flip every indicator block). "
            "Snapshots the .cfx first. Returns how many blocks matched and were flipped."
        )
    )
    async def cfx_toggle_building_blocks(
        args: CfxToggleBuildingBlocksArgs, ctx: Context
    ) -> dict:
        try:
            if not args.key_prefix and not args.category:
                return {"ok": False, "error": "must set at least one of key_prefix or category"}
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            snap = _make_snapshot(cfx_path, label="toggle_building_blocks")

            def _p(raw: bytes) -> tuple[bytes, Counter, bool]:
                return _patch_blocks_toggle(
                    raw,
                    enable=args.enable,
                    key_prefix=args.key_prefix,
                    category=args.category,
                )

            summary, per_file = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p
            )
            return {
                "ok": True,
                "project": args.project,
                "snapshot": str(snap),
                "enable": args.enable,
                "key_prefix": args.key_prefix,
                "category": args.category,
                "summary": summary,
                "per_file": per_file,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


# ---- tiny formatters reused by multiple tools above -----------------------


def _fmt(v: float | None) -> str | None:
    if v is None:
        return None
    # SQ accepts e.g. "30" or "1.5" — drop trailing .0 for ints
    s = f"{v:g}"
    return s


def _fmt_int(v: int | None) -> str | None:
    return None if v is None else str(int(v))


def _bool_or_none(v: bool | None) -> str | None:
    if v is None:
        return None
    return "true" if v else "false"


__all__ = [
    "CfxListBuildingBlocksArgs",
    "CfxSetGeneticOptionsArgs",
    "CfxSetMaxStrategiesArgs",
    "CfxSetSetupAttrsArgs",
    "CfxSetSlPtRangeArgs",
    "CfxSetTradeCapsArgs",
    "CfxToggleBuildingBlocksArgs",
    "_bool_or_none",
    "_fmt",
    "_fmt_int",
    "_list_building_blocks",
    "_patch_blocks_toggle",
    "_patch_buildmode",
    "_patch_max_strategies",
    "_patch_options_params",
    "_patch_setup_attrs",
    "_patch_slpt",
    "register",
]
