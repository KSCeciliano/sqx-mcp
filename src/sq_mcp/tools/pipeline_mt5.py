"""Mega-pipeline: SQ databank → diversified picks → audit → MT5 pack.

This is the end-to-end tool the agent reaches for when shipping a portfolio
to MT5. It chains the existing primitives in one call:

  1. ``portfolio._select_diverse`` — pick N strategies from a databank,
     respecting trade-hash / symbol-tf diversity.
  2. ``audit._audit_strategy_metrics`` — risk-finding scan on each pick.
  3. ``pipeline._verdict_from_findings`` — traffic-light verdict per pick.
  4. ``mt5_extra._assign_magic_numbers`` — collision-free magic-number map.
  5. (optional, behind ``deploy=True``) copy the files into
     ``MQL5/Experts/<pack_name>/`` and emit a ``manifest.json``.

Picks classified as red (block_deploy) are excluded from the deploy plan
unless the caller explicitly passes ``include_blocked=True``.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    validate_databank_name,
    validate_project_name,
)
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.audit import _audit_strategy_metrics
from sq_mcp.tools.mt5 import _resolve_mt5_install_root
from sq_mcp.tools.mt5_extra import _assign_magic_numbers, _sha256
from sq_mcp.tools.pipeline import _finding_to_dict, _verdict_from_findings
from sq_mcp.tools.portfolio import (
    _filter_min_trades,
    _rank_rows,
    _scan_databank,
    _select_diverse,
)


class PipelineExportToMt5Args(BaseModel):
    project: str
    databank: str = "Results"
    n: int = Field(default=5, ge=1, le=50)
    rank_mode: str = Field(default="defensive")
    diversify_by: str | None = "trades_hash"
    min_trades: int | None = 30
    block_on: list[Literal["critical", "high", "medium", "low", "info"]] = Field(
        default_factory=lambda: ["critical", "high"]
    )
    pack_name: str = Field(
        ..., max_length=64,
        description="Subfolder under MQL5/Experts/ to hold the pack.",
    )
    namespace: str = Field("sq", max_length=32)
    overwrite: bool = False
    include_blocked: bool = Field(
        False,
        description=(
            "If True, RED-verdict picks are also included in the deploy plan. "
            "Off by default — typical use is to ship only green/yellow."
        ),
    )
    deploy: bool = Field(
        False,
        description=(
            "If True, actually copy files into MQL5/Experts/<pack_name>/. "
            "If False (default), returns the deploy plan but performs no I/O."
        ),
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("pack_name", "namespace")
    @classmethod
    def _v_safe(cls, v: str) -> str:
        import re
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", v):
            raise ValueError("must be alphanumeric / _ / -")
        return v


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Mega-pipeline: from an SQ databank to a ready-to-deploy MT5 pack. "
            "Selects N diversified strategies, audits each one, classifies "
            "green/yellow/red, assigns unique magic numbers, and (optionally) "
            "copies the .sqx files into MQL5/Experts/<pack_name>/ with a "
            "manifest.json. With deploy=False (default) returns the plan only — "
            "no MT5 modification — so the agent can confirm before committing."
        )
    )
    async def pipeline_export_to_mt5(
        args: PipelineExportToMt5Args, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = (
                eng.config.projects_dir
                / args.project
                / "databanks"
                / validate_databank_name(args.databank)
            )
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}

            # ---- Pick + audit ------------------------------------------------
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            if args.diversify_by:
                picks = _select_diverse(
                    rows,
                    n=args.n,
                    rank_mode=args.rank_mode,
                    diversify_by=args.diversify_by,
                )
            else:
                picks = _rank_rows(rows, args.rank_mode)[: args.n]

            picks_audited: list[dict[str, Any]] = []
            for r in picks:
                findings = _audit_strategy_metrics(r)
                verdict = _verdict_from_findings(findings, list(args.block_on))
                picks_audited.append(
                    {
                        "rel": r["rel"],
                        "source_path": r.get("file"),
                        "strategy_name": r.get("strategy_name"),
                        "symbol": r.get("symbol"),
                        "timeframe": r.get("timeframe"),
                        "trades": r.get("trades"),
                        "fitness_oos": r.get("fitness_oos"),
                        "drawdown_pct": r.get("drawdown_pct"),
                        "verdict": verdict,
                        "findings": [_finding_to_dict(f) for f in findings],
                    }
                )

            # ---- Plan the pack: filter by traffic light ---------------------
            in_pack: list[dict[str, Any]] = []
            excluded_red: list[dict[str, Any]] = []
            for p in picks_audited:
                if p["verdict"]["traffic_light"] == "red" and not args.include_blocked:
                    excluded_red.append(p)
                else:
                    in_pack.append(p)

            # Magic-number assignment uses the .sqx basename. EAs are typically
            # exported with the same stem as their .sqx, so this map carries
            # through to the .mq5 file the user will eventually compile.
            magic = _assign_magic_numbers(
                [Path(p["rel"]).name for p in in_pack],
                namespace=args.namespace,
                floor=100_000,
            )
            for p in in_pack:
                p["magic"] = magic[Path(p["rel"]).name]

            plan = {
                "pack_name": args.pack_name,
                "namespace": args.namespace,
                "ea_count_planned": len(in_pack),
                "excluded_red_count": len(excluded_red),
                "entries_planned": in_pack,
            }

            # ---- Execute deploy if requested --------------------------------
            deploy_report: dict[str, Any] = {"deployed": False}
            if args.deploy:
                mt5_root = _resolve_mt5_install_root()
                if mt5_root is None:
                    return {
                        "ok": False,
                        "error": "MT5 install not found",
                        "plan": plan,
                    }
                target_root = mt5_root / "MQL5" / "Experts" / args.pack_name
                target_root.mkdir(parents=True, exist_ok=True)

                deployed_entries: list[dict[str, Any]] = []
                skipped: list[dict[str, str]] = []
                for p in in_pack:
                    src = Path(p["source_path"])
                    if not src.is_file():
                        skipped.append({"name": src.name, "reason": "source missing"})
                        continue
                    dst = target_root / src.name
                    if dst.exists() and not args.overwrite:
                        skipped.append(
                            {"name": src.name, "reason": "exists; overwrite=False"}
                        )
                        continue
                    shutil.copy2(src, dst)
                    deployed_entries.append(
                        {
                            "rel": p["rel"],
                            "name": src.name,
                            "magic": p["magic"],
                            "verdict": p["verdict"]["traffic_light"],
                            "deployed_to": str(dst),
                            "sha256": _sha256(dst),
                        }
                    )
                manifest = {
                    "pack_name": args.pack_name,
                    "namespace": args.namespace,
                    "created_at": datetime.now(tz=timezone.utc).isoformat(),
                    "source_project": args.project,
                    "source_databank": args.databank,
                    "ea_count": len(deployed_entries),
                    "entries": deployed_entries,
                }
                manifest_path = target_root / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                deploy_report = {
                    "deployed": True,
                    "pack_dir": str(target_root),
                    "manifest_path": str(manifest_path),
                    "deployed_count": len(deployed_entries),
                    "skipped": skipped,
                    "manifest": manifest,
                }

            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "candidates_total": len(rows),
                "unparseable_count": len(bad),
                "totals": {
                    "green": sum(
                        1 for p in picks_audited
                        if p["verdict"]["traffic_light"] == "green"
                    ),
                    "yellow": sum(
                        1 for p in picks_audited
                        if p["verdict"]["traffic_light"] == "yellow"
                    ),
                    "red": sum(
                        1 for p in picks_audited
                        if p["verdict"]["traffic_light"] == "red"
                    ),
                },
                "plan": plan,
                "excluded_red": excluded_red,
                "deploy": deploy_report,
            }
        except (EngineError, ValidationError, OSError, RuntimeError) as exc:
            return safe_error_payload(exc)


__all__ = ["PipelineExportToMt5Args", "register"]
