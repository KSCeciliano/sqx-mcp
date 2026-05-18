"""Preset configurations for common scenarios.

Pre-canned bundles of cfx patches for typical setups. Each tool here applies
a curated combination of the lower-level ``cfx_set_*`` patchers in one call.
Use these to start from a sane baseline rather than wiring every setting by
hand.

Tools:

- ``preset_crypto_24_7`` — disable forex-specific filters (ExitOnFriday,
  LimitTimeRange), set SLPT to use percent (not pips), set data range to
  the post-2023 BTCUSDT window. Applies to one project.
- ``preset_recommended_genetic`` — set Population=100, Generations=100,
  IS/OOS=50%, MaxStrategies=1000. The "I want a real GA run" defaults.
- ``preset_quick_smoke`` — minimal settings for a fast Builder smoke test
  (Population=20, Generations=10, MaxStrategies=20). Use to verify a
  project actually runs before kicking off a real search.

Each preset reuses the lower-level patchers, snapshots the .cfx first, and
returns a per-change summary.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, field_validator

from sq_mcp._validation import ValidationError, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.cfx_advanced import (
    _patch_buildmode,
    _patch_max_strategies,
    _patch_options_params,
    _patch_slpt,
)
from sq_mcp.tools.cfx_config import _apply_to_cfx_buildlike
from sq_mcp.tools.projects import _make_snapshot


class PresetProjectArgs(BaseModel):
    project: str
    target_first_only: bool = True

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


def _project_cfx(eng, project: str):  # noqa: ANN001
    return eng.config.projects_dir / project / "project.cfx"


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Apply a crypto-24/7 preset to a project's first Build/Optimize task: "
            "disable ExitOnFriday + LimitTimeRange, switch SLPT to percent-based "
            "(MinSLInPercent=1.0 / MaxSLInPercent=10.0). Snapshots the .cfx first. "
            "Pair with cfx_set_data_range to set the date window for your symbol."
        )
    )
    async def preset_crypto_24_7(args: PresetProjectArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = _project_cfx(eng, args.project)
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            snap = _make_snapshot(cfx_path, label="preset_crypto_24_7")

            options_updates: dict[str, Any] = {
                "ExitOnFriday": "false",
                "LimitTimeRange": "false",
                "ExitAtEndOfDay": "false",
            }
            slpt_updates: dict[str, Any] = {
                # Encourage %-based SLPT for crypto (price moves are huge)
                "MinSLInPercent": "1",
                "MaxSLInPercent": "10",
                "MinPTInPercent": "1",
                "MaxPTInPercent": "20",
            }

            def _p_options(raw: bytes):
                return _patch_options_params(raw, updates=options_updates)

            def _p_slpt(raw: bytes):
                return _patch_slpt(raw, updates=slpt_updates)

            summary1, per_file1 = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p_options
            )
            summary2, per_file2 = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p_slpt
            )
            return {
                "ok": True,
                "project": args.project,
                "snapshot": str(snap),
                "options_summary": summary1,
                "slpt_summary": summary2,
                "options_per_file": per_file1,
                "slpt_per_file": per_file2,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Apply 'real GA run' defaults to a project: PopulationSize=100, "
            "MaxGenerations=100, EvoInSamplePeriod ratio=50 (50% OOS holdout), "
            "MaxStrategies=1000 with matching StopCondition. Snapshots .cfx."
        )
    )
    async def preset_recommended_genetic(
        args: PresetProjectArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = _project_cfx(eng, args.project)
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            snap = _make_snapshot(cfx_path, label="preset_recommended_genetic")

            bm_updates = {
                "PopulationSize": "100",
                "MaxGenerations": "100",
                "in_sample_ratio": "50",
            }

            def _p_bm(raw: bytes):
                return _patch_buildmode(raw, updates=bm_updates)

            def _p_max(raw: bytes):
                return _patch_max_strategies(raw, new_max=1000, sync_stop=True)

            summary1, per_file1 = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p_bm
            )
            summary2, per_file2 = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p_max
            )
            return {
                "ok": True,
                "project": args.project,
                "snapshot": str(snap),
                "buildmode_summary": summary1,
                "max_strategies_summary": summary2,
                "buildmode_per_file": per_file1,
                "max_strategies_per_file": per_file2,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Apply minimal smoke-test defaults: PopulationSize=20, MaxGenerations=10, "
            "MaxStrategies=20. Use to verify a project actually runs before "
            "committing trial-license time to a full search. Snapshots .cfx."
        )
    )
    async def preset_quick_smoke(args: PresetProjectArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = _project_cfx(eng, args.project)
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            snap = _make_snapshot(cfx_path, label="preset_quick_smoke")

            bm_updates = {
                "PopulationSize": "20",
                "MaxGenerations": "10",
            }

            def _p_bm(raw: bytes):
                return _patch_buildmode(raw, updates=bm_updates)

            def _p_max(raw: bytes):
                return _patch_max_strategies(raw, new_max=20, sync_stop=True)

            summary1, per_file1 = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p_bm
            )
            summary2, per_file2 = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_p_max
            )
            return {
                "ok": True,
                "project": args.project,
                "snapshot": str(snap),
                "buildmode_summary": summary1,
                "max_strategies_summary": summary2,
                "buildmode_per_file": per_file1,
                "max_strategies_per_file": per_file2,
                "note": (
                    "Smoke-test settings — strategies built with these are NOT "
                    "production-quality. Re-run preset_recommended_genetic before a real search."
                ),
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = ["PresetProjectArgs", "register"]
