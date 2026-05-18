"""Portfolio-level risk & concentration analytics for SQ X databanks.

These tools roll a databank's metrics into the kind of summary statistics a
risk-aware trader would want to see *before* exporting a portfolio:

- ``portfolio_risk_metrics`` — distributional stats on drawdown_pct and
  fitness_oos across the databank (mean/median/stddev, p95/p99, worst).
- ``portfolio_concentration`` — Herfindahl-Hirschman Index (HHI) measuring
  how concentrated the databank is in: trade-sequence hashes (are these
  really diverse strategies?), symbols, and timeframes.
- ``portfolio_capital_allocation`` — suggested capital weights across the
  top-N strategies, using inverse-drawdown weighting (lower DD → more
  capital) optionally tempered by a fitness bias.

All tools are read-only.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp._validation import ValidationError, validate_databank_name, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.portfolio import _filter_min_trades, _rank_rows, _scan_databank

# ---- argument schemas ------------------------------------------------------


class PortfolioRiskMetricsArgs(BaseModel):
    project: str
    databank: str = "Results"
    min_trades: int | None = None


class PortfolioConcentrationArgs(BaseModel):
    project: str
    databank: str = "Results"
    min_trades: int | None = None


class PortfolioCapitalAllocationArgs(BaseModel):
    project: str
    databank: str = "Results"
    n: int = Field(5, ge=1, le=50)
    rank_mode: str = Field("defensive")
    min_trades: int | None = 30
    fitness_bias: float = Field(
        0.5, ge=0.0, le=1.0,
        description=(
            "Blend factor between inverse-drawdown weighting (0.0) and "
            "fitness-proportional weighting (1.0). 0.5 = balanced."
        ),
    )


class PortfolioDiversityScoreArgs(BaseModel):
    project: str
    databank: str = "Results"
    min_trades: int | None = None


class DatabankMetricCorrelationArgs(BaseModel):
    project: str
    databank: str = "Results"
    metric_a: str = Field("fitness_oos", description="First metric name.")
    metric_b: str = Field("drawdown_pct", description="Second metric name.")
    min_trades: int | None = None


# ---- helpers ---------------------------------------------------------------


def _distribution_stats(values: list[float]) -> dict[str, Any]:
    """Mean / median / stddev / pX of a numeric series. Skips None."""
    cleaned = [v for v in values if v is not None and math.isfinite(v)]
    if not cleaned:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "stddev": None,
            "p10": None,
            "p25": None,
            "p75": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "min": None,
            "max": None,
        }
    cleaned.sort()
    n = len(cleaned)

    def _pct(p: float) -> float:
        if n == 1:
            return cleaned[0]
        rank = (p / 100.0) * (n - 1)
        lo = int(math.floor(rank))
        hi = int(math.ceil(rank))
        if lo == hi:
            return cleaned[lo]
        frac = rank - lo
        return cleaned[lo] * (1 - frac) + cleaned[hi] * frac

    return {
        "count": n,
        "mean": round(statistics.fmean(cleaned), 6),
        "median": round(statistics.median(cleaned), 6),
        "stddev": round(statistics.pstdev(cleaned), 6) if n > 1 else 0.0,
        "p10": round(_pct(10), 6),
        "p25": round(_pct(25), 6),
        "p75": round(_pct(75), 6),
        "p90": round(_pct(90), 6),
        "p95": round(_pct(95), 6),
        "p99": round(_pct(99), 6),
        "min": cleaned[0],
        "max": cleaned[-1],
    }


def _hhi(counts: dict[str, int]) -> dict[str, Any]:
    """Herfindahl-Hirschman Index over a count distribution.

    HHI ∈ [1/N, 1] where 1/N is "perfectly diversified across N buckets"
    and 1.0 is "everything in one bucket". We also return the normalized
    HHI ∈ [0, 1] where 0 = even split, 1 = concentrated.
    """
    total = sum(counts.values())
    if total == 0:
        return {"hhi": None, "hhi_normalized": None, "unique_buckets": 0, "total": 0}
    shares = [c / total for c in counts.values()]
    hhi = sum(s * s for s in shares)
    n = len(counts)
    hhi_norm: float | None = None
    if n > 1:
        # Normalized so the lowest possible (perfectly even) is 0.0
        hhi_norm = (hhi - 1 / n) / (1 - 1 / n)
    return {
        "hhi": round(hhi, 6),
        "hhi_normalized": round(hhi_norm, 6) if hhi_norm is not None else None,
        "unique_buckets": n,
        "total": total,
        "top_bucket_share": round(max(shares), 6),
    }


def _bucket_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        if key == "symbol_tf":
            k = f"{r.get('symbol') or '?'}_{r.get('timeframe') or '?'}"
        else:
            k = str(r.get(key)) if r.get(key) is not None else "__missing__"
        out[k] = out.get(k, 0) + 1
    return out


def _allocation_weights(
    rows: list[dict[str, Any]],
    *,
    fitness_bias: float,
) -> list[dict[str, Any]]:
    """Compute capital allocation weights blending inverse-DD with fitness."""
    # Inverse-DD: 1 / max(drawdown_pct, floor) — strategies with no DD info
    # get a neutral weight matching the median of the population.
    raw_dd: list[float] = []
    raw_fit: list[float] = []
    for r in rows:
        dd = r.get("drawdown_pct")
        fit = r.get("fitness_oos") or r.get("fitness_full") or r.get("fitness_is")
        raw_dd.append(float(dd) if dd is not None else 0.0)
        raw_fit.append(float(fit) if fit is not None else 0.0)

    inv_dd = [1.0 / max(d, 1.0) for d in raw_dd]
    inv_dd_total = sum(inv_dd) or 1.0
    w_inv_dd = [x / inv_dd_total for x in inv_dd]

    fit_clip = [max(f, 0.0) for f in raw_fit]
    fit_total = sum(fit_clip) or 1.0
    w_fit = [x / fit_total for x in fit_clip]

    out: list[dict[str, Any]] = []
    for r, a, b in zip(rows, w_inv_dd, w_fit, strict=True):
        blended = (1 - fitness_bias) * a + fitness_bias * b
        out.append(
            {
                "rel": r["rel"],
                "strategy_name": r.get("strategy_name"),
                "symbol": r.get("symbol"),
                "timeframe": r.get("timeframe"),
                "fitness_oos": r.get("fitness_oos"),
                "drawdown_pct": r.get("drawdown_pct"),
                "weight_inv_dd": round(a, 6),
                "weight_fitness": round(b, 6),
                "weight_blended": round(blended, 6),
            }
        )
    # Re-normalize the blended weights (so they sum to exactly 1 after rounding)
    s = sum(o["weight_blended"] for o in out) or 1.0
    for o in out:
        o["weight_blended"] = round(o["weight_blended"] / s, 6)
    out.sort(key=lambda o: o["weight_blended"], reverse=True)
    return out


def _metric_pair_pearson(
    rows: list[dict[str, Any]], *, metric_a: str, metric_b: str
) -> dict[str, Any]:
    """Pearson correlation between two metrics across a databank's rows.

    Drops rows where either metric is None / non-finite. Reuses
    ``comparison._pearson`` so behavior matches existing tools.
    """
    from sq_mcp.tools.comparison import _pearson
    xs: list[float] = []
    ys: list[float] = []
    for r in rows:
        a = r.get(metric_a)
        b = r.get(metric_b)
        if a is None or b is None:
            continue
        try:
            af = float(a)
            bf = float(b)
        except (TypeError, ValueError):
            continue
        if af != af or bf != bf:  # NaN check
            continue
        xs.append(af)
        ys.append(bf)
    if len(xs) < 3:
        return {
            "pairs_used": len(xs),
            "correlation": None,
            "note": "need at least 3 non-null pairs",
        }
    corr = _pearson(xs, ys)
    return {
        "pairs_used": len(xs),
        "correlation": round(corr, 4) if corr is not None else None,
        "mean_a": round(sum(xs) / len(xs), 6),
        "mean_b": round(sum(ys) / len(ys), 6),
    }


def _diversity_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Composite 0-100 diversity score across trade_hash, symbol/TF, and unique-fingerprint.

    Higher = more diverse. Three sub-scores, each 0-100:

    - trades_hash diversity = 100 × (1 - normalized HHI)
    - symbol_tf diversity = 100 × (1 - normalized HHI)
    - unique_fingerprint ratio = 100 × (unique / total)

    Final = mean of the three available sub-scores.
    """
    if not rows:
        return {"score": 0, "tier": "empty", "sub_scores": {}}

    th = _hhi(_bucket_counts(rows, "trades_hash"))
    stf = _hhi(_bucket_counts(rows, "symbol_tf"))
    fp = _hhi(_bucket_counts(rows, "fingerprint_exact"))

    def _sub(hhi_dict: dict[str, Any]) -> float | None:
        norm = hhi_dict.get("hhi_normalized")
        if norm is None:
            return None
        return round(100.0 * (1.0 - max(0.0, min(1.0, norm))), 2)

    th_score = _sub(th)
    stf_score = _sub(stf)
    fp_score = _sub(fp)

    available = [s for s in (th_score, stf_score, fp_score) if s is not None]
    if not available:
        # Likely a single-strategy databank — give a neutral score
        final = 50.0
    else:
        final = round(sum(available) / len(available), 1)
    tier = (
        "excellent" if final >= 75
        else "good" if final >= 60
        else "marginal" if final >= 40
        else "poor"
    )
    return {
        "score": final,
        "tier": tier,
        "sub_scores": {
            "trades_hash_diversity": th_score,
            "symbol_tf_diversity": stf_score,
            "fingerprint_diversity": fp_score,
        },
        "raw_hhi": {
            "trades_hash": th,
            "symbol_tf": stf,
            "fingerprint_exact": fp,
        },
    }


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Distributional risk stats across every strategy in a databank: "
            "mean/median/stddev/p10/p25/p75/p90/p95/p99/min/max for drawdown_pct, "
            "fitness_oos, profit_to_dd_ratio, and return_pct. Read-only."
        )
    )
    async def portfolio_risk_metrics(
        args: PortfolioRiskMetricsArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            project = validate_project_name(args.project)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            return {
                "ok": True,
                "project": project,
                "databank": databank,
                "unparseable_count": len(bad),
                "scanned": len(rows),
                "drawdown_pct": _distribution_stats([r.get("drawdown_pct") for r in rows]),
                "fitness_oos": _distribution_stats([r.get("fitness_oos") for r in rows]),
                "profit_to_dd_ratio": _distribution_stats(
                    [r.get("profit_to_dd_ratio") for r in rows]
                ),
                "return_pct": _distribution_stats([r.get("return_pct") for r in rows]),
                "trades": _distribution_stats(
                    [float(r["trades"]) if r.get("trades") is not None else None for r in rows]
                ),
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Herfindahl-Hirschman concentration scores on a databank, computed "
            "separately for trades_hash (true strategy diversity), symbol, "
            "timeframe, and symbol_tf (combo). HHI ∈ [1/N, 1]; normalized HHI "
            "∈ [0, 1] where 0 = perfectly diversified and 1 = single-bucket. "
            "Use to flag a databank that looks varied on the surface but is "
            "actually trading the same edge across every row."
        )
    )
    async def portfolio_concentration(
        args: PortfolioConcentrationArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            project = validate_project_name(args.project)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            return {
                "ok": True,
                "project": project,
                "databank": databank,
                "scanned": len(rows),
                "unparseable_count": len(bad),
                "trades_hash": _hhi(_bucket_counts(rows, "trades_hash")),
                "symbol": _hhi(_bucket_counts(rows, "symbol")),
                "timeframe": _hhi(_bucket_counts(rows, "timeframe")),
                "symbol_tf": _hhi(_bucket_counts(rows, "symbol_tf")),
                "fingerprint_exact": _hhi(_bucket_counts(rows, "fingerprint_exact")),
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Suggested capital allocation across the top-N strategies in a "
            "databank. Blends inverse-drawdown weighting (lower DD ⇒ more "
            "capital) with fitness-proportional weighting via the fitness_bias "
            "knob. Weights are normalized to 1. Read-only — does not modify "
            "any project."
        )
    )
    async def portfolio_capital_allocation(
        args: PortfolioCapitalAllocationArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            project = validate_project_name(args.project)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            ranked = _rank_rows(rows, args.rank_mode)[: args.n]
            allocations = _allocation_weights(ranked, fitness_bias=args.fitness_bias)
            return {
                "ok": True,
                "project": project,
                "databank": databank,
                "n_requested": args.n,
                "n_picked": len(allocations),
                "rank_mode": args.rank_mode,
                "fitness_bias": args.fitness_bias,
                "unparseable_count": len(bad),
                "allocations": allocations,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compute the Pearson correlation between two metrics across every "
            "strategy in a databank. Use to answer 'does higher fitness usually "
            "mean higher drawdown?' (positive correlation) or 'is profit-to-DD "
            "independent of trade count?' (near zero). Read-only."
        )
    )
    async def databank_metric_correlation(
        args: DatabankMetricCorrelationArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            project = validate_project_name(args.project)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            result = _metric_pair_pearson(rows, metric_a=args.metric_a, metric_b=args.metric_b)
            return {
                "ok": True,
                "project": project,
                "databank": databank,
                "metric_a": args.metric_a,
                "metric_b": args.metric_b,
                "scanned": len(rows),
                "unparseable_count": len(bad),
                **result,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Composite 0-100 diversity score for a databank — averages "
            "trades_hash diversity, symbol/TF diversity, and unique-fingerprint "
            "ratio (each derived from normalized HHI). Returns a tier label "
            "(excellent / good / marginal / poor) and the per-component breakdown. "
            "Read-only."
        )
    )
    async def portfolio_diversity_score(
        args: PortfolioDiversityScoreArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            project = validate_project_name(args.project)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            score = _diversity_score(rows)
            return {
                "ok": True,
                "project": project,
                "databank": databank,
                "row_count": len(rows),
                "unparseable_count": len(bad),
                **score,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "DatabankMetricCorrelationArgs",
    "PortfolioCapitalAllocationArgs",
    "PortfolioConcentrationArgs",
    "PortfolioDiversityScoreArgs",
    "PortfolioRiskMetricsArgs",
    "_allocation_weights",
    "_bucket_counts",
    "_distribution_stats",
    "_diversity_score",
    "_hhi",
    "_metric_pair_pearson",
    "register",
]
