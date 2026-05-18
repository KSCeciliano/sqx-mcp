"""Walk-forward / cross-validation analysis for strategy robustness.

SQ X has a built-in walk-forward optimizer that produces a databank of
per-fold .sqx files. The tools here aggregate those folds, compute
fold-to-fold variance, walk-forward efficiency, and flag strategies
whose in-sample vs out-of-sample gap is suspiciously wide.

There are two flavors:

1. **Databank-based** — pass a project + databank that holds the WF
   folds. The scanner reads every .sqx, pulls IS/OOS fitness, and
   summarizes the population.

2. **Explicit input** — pass a list of (fold_id, is_metric, oos_metric)
   tuples (the caller may have parsed an MT5 report or other source).
   The math is identical.

Tools:

- ``walkforward_databank_summary`` — fold-by-fold IS/OOS table from a
  databank, plus aggregate stats (mean, std, min, max of OOS) and
  walk-forward efficiency (mean OOS / mean IS).
- ``walkforward_consistency_score`` — 0-100 score combining OOS mean,
  OOS variance, and IS→OOS slippage. Higher = more consistent.
- ``walkforward_from_folds`` — same math from an explicit folds array,
  useful when the source isn't an .sqx (MT5 report, custom CSV).
- ``walkforward_overfit_flags`` — list every fold whose OOS metric is
  less than ``oos_floor_ratio`` * its IS metric. Quick overfit screen.

Read-only.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_databank_name, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.portfolio import _scan_databank

# ---- argument schemas ------------------------------------------------------


class WfDatabankArgs(BaseModel):
    project: str
    databank: str = "Walk-Forward"
    metric: str = "fitness"

    @field_validator("project")
    @classmethod
    def _v_p(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_d(cls, v: str) -> str:
        return validate_databank_name(v)


class Fold(BaseModel):
    fold_id: str
    is_metric: float
    oos_metric: float | None = None


class WfFoldsArgs(BaseModel):
    folds: list[Fold] = Field(..., min_length=2, max_length=200)


class WfOverfitArgs(BaseModel):
    project: str
    databank: str = "Walk-Forward"
    oos_floor_ratio: float = Field(0.5, ge=0.0, le=2.0)

    @field_validator("project")
    @classmethod
    def _v_p(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_d(cls, v: str) -> str:
        return validate_databank_name(v)


# ---- helpers ---------------------------------------------------------------


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)  # sample std
    return math.sqrt(var)


def _aggregate_folds(folds: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute IS/OOS aggregates and walk-forward efficiency."""
    is_vals = [f["is_metric"] for f in folds if f.get("is_metric") is not None]
    oos_vals = [
        f["oos_metric"] for f in folds if f.get("oos_metric") is not None
    ]
    is_mean = _mean(is_vals) if is_vals else None
    oos_mean = _mean(oos_vals) if oos_vals else None
    wf_eff = (
        round(oos_mean / is_mean, 4)
        if (is_mean and oos_mean is not None and is_mean > 0)
        else None
    )
    return {
        "n_folds": len(folds),
        "n_with_oos": len(oos_vals),
        "is_mean": round(is_mean, 6) if is_mean is not None else None,
        "is_std": round(_std(is_vals), 6) if len(is_vals) >= 2 else None,
        "is_min": round(min(is_vals), 6) if is_vals else None,
        "is_max": round(max(is_vals), 6) if is_vals else None,
        "oos_mean": round(oos_mean, 6) if oos_mean is not None else None,
        "oos_std": round(_std(oos_vals), 6) if len(oos_vals) >= 2 else None,
        "oos_min": round(min(oos_vals), 6) if oos_vals else None,
        "oos_max": round(max(oos_vals), 6) if oos_vals else None,
        "walk_forward_efficiency": wf_eff,
    }


def _consistency_score(folds: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine OOS mean, variance, and IS→OOS slippage into a 0-100 score."""
    agg = _aggregate_folds(folds)
    oos_mean = agg.get("oos_mean")
    oos_std = agg.get("oos_std")
    is_mean = agg.get("is_mean")

    if oos_mean is None or oos_mean <= 0:
        return {"score": 0, "agg": agg, "verdict": "no positive OOS performance"}

    # Sub-score 1: OOS magnitude vs noise (capped at 40 pts)
    sharpe_like = (oos_mean / oos_std) if oos_std and oos_std > 0 else 5.0
    s1 = min(40.0, max(0.0, sharpe_like * 10.0))

    # Sub-score 2: stability — what fraction of folds have positive OOS (30 pts)
    pos_frac = sum(1 for f in folds if (f.get("oos_metric") or 0) > 0) / len(folds)
    s2 = pos_frac * 30.0

    # Sub-score 3: IS→OOS slippage (30 pts)
    if is_mean is None or is_mean <= 0:
        s3 = 0.0
    else:
        ratio = max(0.0, min(1.0, oos_mean / is_mean))
        s3 = ratio * 30.0

    score = round(s1 + s2 + s3, 1)
    verdict = (
        "robust"
        if score >= 70
        else "borderline"
        if score >= 40
        else "weak"
    )
    return {
        "score": score,
        "sub_score_signal_to_noise": round(s1, 1),
        "sub_score_positive_fraction": round(s2, 1),
        "sub_score_is_oos_efficiency": round(s3, 1),
        "verdict": verdict,
        "agg": agg,
    }


def _overfit_flags(
    folds: list[dict[str, Any]], floor_ratio: float
) -> list[dict[str, Any]]:
    flagged = []
    for f in folds:
        is_v = f.get("is_metric")
        oos_v = f.get("oos_metric")
        if is_v is None or oos_v is None:
            continue
        if is_v <= 0:
            continue
        ratio = oos_v / is_v
        if ratio < floor_ratio:
            flagged.append(
                {
                    "fold_id": f.get("fold_id"),
                    "rel": f.get("rel"),
                    "is_metric": round(is_v, 6),
                    "oos_metric": round(oos_v, 6),
                    "oos_is_ratio": round(ratio, 4),
                    "slippage_pct": round(100.0 * (1.0 - ratio), 2),
                }
            )
    flagged.sort(key=lambda x: x["oos_is_ratio"])
    return flagged


def _databank_to_folds(
    rows: list[dict[str, Any]], metric_key: str
) -> list[dict[str, Any]]:
    """Convert databank scan rows to fold dicts using `metric_key`.

    Maps:
        is_metric  ← row["{key}_is"] or row["{key}"]
        oos_metric ← row["{key}_oos"]
        fold_id    ← row["rel"]
    """
    folds = []
    for r in rows:
        # Be permissive — accept "fitness", "fitness_is", "fitness_oos" or just "fitness"
        is_v = r.get(f"{metric_key}_is")
        if is_v is None:
            is_v = r.get(f"{metric_key}_full")
        if is_v is None:
            is_v = r.get(metric_key)
        oos_v = r.get(f"{metric_key}_oos")
        if is_v is None and oos_v is None:
            continue
        folds.append(
            {
                "fold_id": r.get("rel"),
                "rel": r.get("rel"),
                "is_metric": is_v,
                "oos_metric": oos_v,
            }
        )
    return folds


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Walk-forward summary from a databank of WF folds. Each .sqx in the "
            "databank is treated as one fold. Returns per-fold IS/OOS values plus "
            "aggregates (mean, std, min, max) and walk-forward efficiency "
            "(mean OOS / mean IS). metric='fitness' looks at fitness_is + "
            "fitness_oos columns. Read-only."
        )
    )
    async def walkforward_databank_summary(args: WfDatabankArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = Path(eng.config.projects_dir) / args.project / "databanks" / args.databank
            if not db_dir.is_dir():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, _bad = _scan_databank(db_dir)
            folds = _databank_to_folds(rows, args.metric)
            if len(folds) < 2:
                return {
                    "ok": False,
                    "error": f"need >= 2 folds with {args.metric} metric, found {len(folds)}",
                }
            agg = _aggregate_folds(folds)
            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "metric": args.metric,
                "folds": [
                    {
                        "fold_id": f["fold_id"],
                        "is_metric": (
                            round(f["is_metric"], 6) if f["is_metric"] is not None else None
                        ),
                        "oos_metric": (
                            round(f["oos_metric"], 6) if f["oos_metric"] is not None else None
                        ),
                    }
                    for f in folds
                ],
                "aggregate": agg,
            }
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Walk-forward consistency score (0-100) from an explicit folds array. "
            "Combines OOS magnitude vs noise, positive-fold fraction, and "
            "IS→OOS efficiency into a single defensive score. >=70 = robust, "
            "40-70 = borderline, <40 = weak. Read-only."
        )
    )
    async def walkforward_consistency_score(args: WfFoldsArgs) -> dict:
        folds = [f.model_dump() for f in args.folds]
        return {"ok": True, **_consistency_score(folds)}

    @mcp.tool(
        description=(
            "Walk-forward aggregate from an explicit folds array (works without "
            "a databank). Each fold is (fold_id, is_metric, oos_metric?). Read-only."
        )
    )
    async def walkforward_from_folds(args: WfFoldsArgs) -> dict:
        folds = [f.model_dump() for f in args.folds]
        return {"ok": True, "aggregate": _aggregate_folds(folds), "folds": folds}

    @mcp.tool(
        description=(
            "List folds whose OOS metric is below oos_floor_ratio × IS metric "
            "(default 0.5 = OOS less than half of IS). Quick overfit screen "
            "against a walk-forward databank. Read-only."
        )
    )
    async def walkforward_overfit_flags(args: WfOverfitArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = Path(eng.config.projects_dir) / args.project / "databanks" / args.databank
            if not db_dir.is_dir():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, _bad = _scan_databank(db_dir)
            folds = _databank_to_folds(rows, "fitness")
            flagged = _overfit_flags(folds, args.oos_floor_ratio)
            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "floor_ratio": args.oos_floor_ratio,
                "n_total_folds": len(folds),
                "n_flagged": len(flagged),
                "flagged": flagged,
            }
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)
