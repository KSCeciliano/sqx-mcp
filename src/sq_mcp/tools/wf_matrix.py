"""Walk-Forward Matrix and Walk-Forward Efficiency.

QuantAnalyzer's Walk-Forward Matrix runs walk-forward at multiple
(IS-length × OOS-length) combinations and reports a grid of results.
Walk-Forward Efficiency = (OOS performance) / (IS performance) — values
near 1.0 mean the strategy held up out-of-sample; values much below 1.0
indicate overfitting.

This module operates on caller-supplied fold results (i.e. it doesn't
re-run the engine; it summarizes whatever folds you've already produced).
Use ``walkforward_from_folds`` upstream to produce the per-fold metrics.

Tools:

- ``wf_efficiency`` — given paired (IS, OOS) metrics across folds,
  compute the WF efficiency ratio + verdict.
- ``wf_matrix_summary`` — given a list of {is_period, oos_period,
  is_sharpe, oos_sharpe} entries, produce a Markdown table sorted by
  efficiency.
- ``wf_anchored_vs_rolling`` — compare anchored (expanding) vs rolling
  (fixed-window) walk-forward results on the same folds.
- ``wf_recommendation`` — given a WF matrix summary, recommend the
  IS/OOS combination with the best balance of robustness (high
  efficiency) and statistical power (long OOS periods).

Pure Python.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    safe_div,
    validate_finite_floats,
)


class WfEfficiencyArgs(BaseModel):
    is_metrics: list[float] = Field(..., min_length=2, max_length=10_000)
    oos_metrics: list[float] = Field(..., min_length=2, max_length=10_000)


class WfMatrixEntry(BaseModel):
    is_period_bars: int = Field(..., ge=1)
    oos_period_bars: int = Field(..., ge=1)
    is_sharpe: float
    oos_sharpe: float
    n_folds: int = Field(..., ge=1)


class WfMatrixSummaryArgs(BaseModel):
    entries: list[WfMatrixEntry] = Field(..., min_length=1, max_length=200)


class WfAnchoredVsRollingArgs(BaseModel):
    anchored_oos: list[float] = Field(..., min_length=2, max_length=10_000)
    rolling_oos: list[float] = Field(..., min_length=2, max_length=10_000)


class WfRecommendationArgs(BaseModel):
    entries: list[WfMatrixEntry] = Field(..., min_length=1, max_length=200)
    min_efficiency: float = Field(0.5, ge=0.0, le=2.0)
    min_oos_bars: int = Field(100, ge=10, le=1_000_000)


def _wf_efficiency(is_metrics: list[float], oos_metrics: list[float]) -> dict[str, Any]:
    validate_finite_floats(is_metrics, name="is_metrics")
    validate_finite_floats(oos_metrics, name="oos_metrics")
    n = min(len(is_metrics), len(oos_metrics))
    if n == 0:
        return {"efficiency": None, "note": "no paired folds"}
    is_mean = sum(is_metrics[:n]) / n
    oos_mean = sum(oos_metrics[:n]) / n
    eff = safe_div(oos_mean, is_mean)
    per_fold = [
        {
            "fold": i,
            "is": round(is_metrics[i], 6),
            "oos": round(oos_metrics[i], 6),
            "efficiency": (
                round(oos_metrics[i] / is_metrics[i], 4)
                if is_metrics[i] != 0 else None
            ),
        }
        for i in range(n)
    ]
    return {
        "efficiency": round(eff, 4) if eff is not None else None,
        "is_mean": round(is_mean, 6),
        "oos_mean": round(oos_mean, 6),
        "n_folds": n,
        "per_fold": per_fold,
        "verdict": (
            "excellent" if eff is not None and eff >= 0.8
            else "good" if eff is not None and eff >= 0.6
            else "marginal" if eff is not None and eff >= 0.4
            else "overfit" if eff is not None and eff >= 0.0
            else "destructive"
        ),
    }


def _wf_matrix_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for e in entries:
        is_s = e["is_sharpe"]
        oos_s = e["oos_sharpe"]
        eff = safe_div(oos_s, is_s)
        rows.append({
            "is_bars": e["is_period_bars"],
            "oos_bars": e["oos_period_bars"],
            "is_sharpe": round(is_s, 4),
            "oos_sharpe": round(oos_s, 4),
            "efficiency": round(eff, 4) if eff is not None else None,
            "n_folds": e["n_folds"],
        })
    # Sort by efficiency desc, NaN/None last
    rows.sort(key=lambda r: (r["efficiency"] is None, -(r["efficiency"] or 0)))
    # Markdown table
    md_lines = [
        "| IS bars | OOS bars | IS Sharpe | OOS Sharpe | Efficiency | Folds |",
        "|---------|----------|-----------|------------|------------|-------|",
    ]
    for r in rows:
        eff_str = f"{r['efficiency']:.3f}" if r["efficiency"] is not None else "—"
        md_lines.append(
            f"| {r['is_bars']} | {r['oos_bars']} | {r['is_sharpe']:.3f} | "
            f"{r['oos_sharpe']:.3f} | {eff_str} | {r['n_folds']} |"
        )
    return {
        "rows": rows,
        "markdown": "\n".join(md_lines),
        "n_combinations": len(rows),
        "best_efficiency": rows[0]["efficiency"] if rows else None,
        "best_combo": (
            {"is_bars": rows[0]["is_bars"], "oos_bars": rows[0]["oos_bars"]}
            if rows else None
        ),
    }


def _anchored_vs_rolling(
    anchored: list[float], rolling: list[float]
) -> dict[str, Any]:
    validate_finite_floats(anchored, name="anchored_oos")
    validate_finite_floats(rolling, name="rolling_oos")
    n = min(len(anchored), len(rolling))
    if n == 0:
        return {"verdict": "no_paired_folds"}
    a_mean = sum(anchored[:n]) / n
    r_mean = sum(rolling[:n]) / n
    diff = a_mean - r_mean
    return {
        "anchored_mean": round(a_mean, 6),
        "rolling_mean": round(r_mean, 6),
        "diff": round(diff, 6),
        "n_folds": n,
        "verdict": (
            "anchored_wins" if diff > 0.05
            else "rolling_wins" if diff < -0.05
            else "comparable"
        ),
        "interpretation": (
            "Strategy benefits from more historical data — use anchored."
            if diff > 0.05
            else "Strategy benefits from recent-data emphasis — use rolling."
            if diff < -0.05
            else "Difference is within noise — choose by computational cost."
        ),
    }


def _wf_recommendation(
    entries: list[dict[str, Any]], min_eff: float, min_oos: int
) -> dict[str, Any]:
    candidates = []
    for e in entries:
        eff = safe_div(e["oos_sharpe"], e["is_sharpe"])
        if eff is None or eff < min_eff:
            continue
        if e["oos_period_bars"] < min_oos:
            continue
        candidates.append({
            "is_bars": e["is_period_bars"],
            "oos_bars": e["oos_period_bars"],
            "is_sharpe": e["is_sharpe"],
            "oos_sharpe": e["oos_sharpe"],
            "efficiency": eff,
            "n_folds": e["n_folds"],
            "score": eff * (e["oos_period_bars"] ** 0.5),  # reward longer OOS
        })
    if not candidates:
        return {
            "recommendation": None,
            "note": (
                f"No (IS, OOS) combination meets both min_efficiency≥{min_eff} "
                f"and min_oos_bars≥{min_oos}. Either widen thresholds, run "
                "more folds, or reconsider the strategy."
            ),
            "n_candidates": 0,
        }
    candidates.sort(key=lambda c: -c["score"])
    best = candidates[0]
    return {
        "recommendation": best,
        "alternatives": candidates[1:5],
        "n_candidates": len(candidates),
        "criteria": {
            "min_efficiency": min_eff,
            "min_oos_bars": min_oos,
            "scoring": "efficiency × sqrt(oos_bars) (reward longer OOS)",
        },
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Walk-Forward Efficiency = mean(OOS metric) / mean(IS metric) "
            "across paired folds. >0.8 = excellent, 0.6-0.8 = good, "
            "0.4-0.6 = marginal, <0.4 = overfit. Reports per-fold detail "
            "and a verdict."
        )
    )
    async def wf_efficiency(args: WfEfficiencyArgs) -> dict:
        try:
            return {"ok": True, **_wf_efficiency(args.is_metrics, args.oos_metrics)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Summarize a Walk-Forward Matrix (a grid of (IS bars, OOS bars) "
            "combinations) sorted by efficiency. Returns the rows + a "
            "Markdown-ready table + the best combination."
        )
    )
    async def wf_matrix_summary(args: WfMatrixSummaryArgs) -> dict:
        return {
            "ok": True,
            **_wf_matrix_summary([e.model_dump() for e in args.entries]),
        }

    @mcp.tool(
        description=(
            "Compare anchored (expanding-window) vs rolling (fixed-window) "
            "walk-forward OOS results. Verdict: anchored_wins / "
            "rolling_wins / comparable. Useful for deciding which WF "
            "variant suits the strategy."
        )
    )
    async def wf_anchored_vs_rolling(args: WfAnchoredVsRollingArgs) -> dict:
        try:
            return {
                "ok": True,
                **_anchored_vs_rolling(args.anchored_oos, args.rolling_oos),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Recommend the best (IS, OOS) combination from a WF matrix that "
            "meets minimum-efficiency and minimum-OOS-bars thresholds. "
            "Scoring rewards longer OOS periods (more statistical power). "
            "Returns the top recommendation + up to 4 alternatives."
        )
    )
    async def wf_recommendation(args: WfRecommendationArgs) -> dict:
        return {
            "ok": True,
            **_wf_recommendation(
                [e.model_dump() for e in args.entries],
                args.min_efficiency,
                args.min_oos_bars,
            ),
        }


__all__ = [
    "WfAnchoredVsRollingArgs",
    "WfEfficiencyArgs",
    "WfMatrixEntry",
    "WfMatrixSummaryArgs",
    "WfRecommendationArgs",
    "_anchored_vs_rolling",
    "_wf_efficiency",
    "_wf_matrix_summary",
    "_wf_recommendation",
    "register",
]
