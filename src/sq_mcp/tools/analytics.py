"""Analytics across multiple strategies — simulate combined behavior.

The .sqx files SQ X writes carry an embedded equity-curve sparkline. By
adding those curves together (optionally scaled by an allocation weight),
we can synthesize a *portfolio* equity curve as a cheap pre-flight check
— no engine spin-up needed.

Tools:

- ``portfolio_combined_equity`` — pick top-N strategies from a databank,
  sum their equity curves (uniform or composite-score-weighted), and
  report on the synthetic portfolio: total return, max DD, longest
  underwater, recovery factor, hit ratio.
- ``portfolio_combined_equity_explicit`` — same but takes an explicit list
  of (sqx_path, weight) pairs, so the agent can simulate exact allocations
  produced by portfolio_capital_allocation.
- ``strategy_monthly_returns_estimate`` — approximate per-month returns from
  an equity sparkline by bucketing samples uniformly. The sparkline isn't
  literally month-indexed, so this is an estimate that assumes uniform
  per-sample time spacing across history_years.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_databank_name,
    validate_project_name,
)
from sq_mcp.engine import EngineError
from sq_mcp.parsers.sqx import derive_metrics, parse_sqx
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.comparison import _curve_to_returns
from sq_mcp.tools.portfolio import _composite_score, _filter_min_trades, _rank_rows, _scan_databank
from sq_mcp.tools.strategy_inspect import _equity_drawdown_stats, _hit_ratio, _select_curve

# ---- argument schemas ------------------------------------------------------


class PortfolioCombinedEquityArgs(BaseModel):
    project: str
    databank: str = "Results"
    n: int = Field(5, ge=1, le=50)
    rank_mode: str = Field("defensive")
    min_trades: int | None = 30
    weight_by: str = Field(
        "uniform",
        description="'uniform' = equal weights, 'composite' = composite_score-weighted.",
    )
    curve: str = Field("full", description="full / is / oos.")


class PortfolioCombinedEquityExplicitArgs(BaseModel):
    items: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "List of {'sqx_path': '...', 'weight': float} dicts. Weight defaults "
            "to 1.0 when missing; weights are normalized to sum 1."
        ),
    )
    curve: str = "full"


class PortfolioContributionArgs(BaseModel):
    items: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "List of {'sqx_path': '...', 'weight': float} dicts. Weights are "
            "normalized to sum 1 before decomposition."
        ),
    )
    curve: str = "full"


class StrategyMonthlyReturnsArgs(BaseModel):
    sqx_path: str
    months_override: int | None = Field(
        None, ge=1, le=240,
        description=(
            "Default: use history_years from the .sqx (clipped to ≥1). Override "
            "if you know the actual sample span better."
        ),
    )


# ---- helpers ---------------------------------------------------------------


def _normalize_weights(weights: list[float]) -> list[float]:
    total = sum(max(w, 0.0) for w in weights)
    if total <= 0:
        return [1.0 / len(weights)] * len(weights)
    return [max(w, 0.0) / total for w in weights]


def _combine_curves(
    curves: list[list[float]], weights: list[float]
) -> list[float]:
    """Sum curves index-by-index up to the shortest one's length."""
    if not curves:
        return []
    min_len = min(len(c) for c in curves)
    if min_len == 0:
        return []
    norm = _normalize_weights(weights)
    out: list[float] = [0.0] * min_len
    for c, w in zip(curves, norm, strict=True):
        for i in range(min_len):
            out[i] += c[i] * w
    return out


def _bucket_curve_by_count(curve: list[float], buckets: int) -> list[float]:
    """Reduce a curve to N approximately-equal-spaced sample points (first / last of each bucket)."""
    if buckets <= 0 or not curve:
        return []
    n = len(curve)
    if buckets >= n:
        return list(curve)
    step = n / buckets
    return [curve[int(i * step)] for i in range(buckets)]


def _monthly_returns_estimate(
    curve: list[float], *, months: int
) -> list[dict[str, Any]]:
    """Estimate per-month returns by uniformly bucketing the sparkline."""
    pts = _bucket_curve_by_count(curve, months + 1)
    rows: list[dict[str, Any]] = []
    for i in range(1, len(pts)):
        prev = pts[i - 1]
        curr = pts[i]
        if prev == 0:
            ret = None
        else:
            ret = round((curr - prev) / abs(prev), 6)
        rows.append({"month_index": i, "start": prev, "end": curr, "return": ret})
    return rows


def _sharpe_ratio(
    returns: list[float], *, periods_per_year: int = 12, risk_free_rate: float = 0.0
) -> float | None:
    """Annualized Sharpe ratio from a list of period returns.

    Standard formula: (mean(R) - r_f) / stddev(R) × sqrt(periods_per_year).
    Returns None when the sample is too thin (n < 2) or zero-variance.
    """
    cleaned = [r for r in returns if r is not None]
    if len(cleaned) < 2:
        return None
    import statistics
    mean = statistics.fmean(cleaned)
    sd = statistics.pstdev(cleaned)
    if sd == 0:
        return None
    excess = mean - (risk_free_rate / periods_per_year)
    return round(excess / sd * (periods_per_year ** 0.5), 4)


def _sortino_ratio(
    returns: list[float], *, periods_per_year: int = 12, risk_free_rate: float = 0.0
) -> float | None:
    """Annualized Sortino ratio — same as Sharpe but only downside stddev.

    For samples where every return is non-negative, downside stddev is 0 and
    we return None (a single positive month doesn't establish a downside).
    """
    cleaned = [r for r in returns if r is not None]
    if len(cleaned) < 2:
        return None
    target = risk_free_rate / periods_per_year
    downside = [(r - target) ** 2 for r in cleaned if r < target]
    if not downside:
        return None
    import statistics
    mean = statistics.fmean(cleaned)
    downside_sd = (sum(downside) / len(cleaned)) ** 0.5
    if downside_sd == 0:
        return None
    excess = mean - target
    return round(excess / downside_sd * (periods_per_year ** 0.5), 4)


def _calmar_ratio(
    *, annualized_return: float | None, max_drawdown_pct_of_peak: float | None
) -> float | None:
    """Calmar = annualized return / max drawdown (as a positive fraction)."""
    if annualized_return is None or max_drawdown_pct_of_peak is None:
        return None
    if max_drawdown_pct_of_peak <= 0:
        return None
    return round(annualized_return / max_drawdown_pct_of_peak, 4)


def _contribution_analysis(
    curves: list[list[float]], weights: list[float], labels: list[str]
) -> dict[str, Any]:
    """Decompose a weighted-portfolio return + DD into per-strategy contributions.

    For each strategy with weight w and curve c:
      - return_contribution = w × (c[-1] - c[0]) / c[0]  (return × allocation)
      - max_dd_contribution = w × max_drawdown_of(c)

    These are *additive approximations*: assuming the combined curve is
    sum(w_i × c_i), the marginal contribution of strategy i is w_i × (its own
    return), which roughly sums to the combined return. DD contributions don't
    literally sum (DDs don't compose linearly), but the breakdown still gives
    a useful "who's responsible for most of the bleed" picture.
    """
    norm = _normalize_weights(weights)
    rows: list[dict[str, Any]] = []
    for c, w, label in zip(curves, norm, labels, strict=True):
        if not c or len(c) < 2 or c[0] == 0:
            rows.append(
                {
                    "label": label,
                    "weight": round(w, 6),
                    "individual_return": None,
                    "return_contribution": None,
                    "individual_max_dd": None,
                    "dd_contribution": None,
                }
            )
            continue
        individual_return = (c[-1] - c[0]) / abs(c[0])
        # Individual DD
        peak = c[0]
        max_dd = 0.0
        for v in c:
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
        rows.append(
            {
                "label": label,
                "weight": round(w, 6),
                "individual_return": round(individual_return, 6),
                "return_contribution": round(w * individual_return, 6),
                "individual_max_dd": round(max_dd, 6),
                "dd_contribution": round(w * max_dd, 6),
            }
        )
    # Sort by return contribution descending
    rows.sort(key=lambda r: r.get("return_contribution") or 0, reverse=True)
    return {
        "total_return_contribution_sum": round(
            sum(r["return_contribution"] for r in rows if r["return_contribution"] is not None),
            6,
        ),
        "total_dd_contribution_sum": round(
            sum(r["dd_contribution"] for r in rows if r["dd_contribution"] is not None),
            6,
        ),
        "by_strategy": rows,
    }


def _annualized_return_from_curve(
    curve: list[float], *, history_years: float | None
) -> float | None:
    """Approximate annualized return from start/end of an equity sparkline."""
    if not curve or len(curve) < 2 or not history_years or history_years <= 0:
        return None
    start = curve[0]
    end = curve[-1]
    if start == 0:
        return None
    total = (end - start) / abs(start)
    # Compound annual growth rate
    return round(((1 + total) ** (1 / history_years)) - 1, 6)


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Synthesize a portfolio equity curve by summing the embedded sparklines "
            "of the top-N strategies in a databank, then compute DD / hit-ratio / "
            "recovery stats on the combined curve. weight_by='uniform' or 'composite' "
            "(composite_score-weighted). Use as a cheap pre-flight before any retest. "
            "Read-only."
        )
    )
    async def portfolio_combined_equity(
        args: PortfolioCombinedEquityArgs, ctx: Context
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

            curves: list[list[float]] = []
            weights: list[float] = []
            picked: list[dict[str, Any]] = []
            for r in ranked:
                info = parse_sqx(Path(r["file"]))
                curve = _select_curve(info, args.curve)
                if len(curve) < 4:
                    continue
                w = 1.0
                if args.weight_by == "composite":
                    w = _composite_score(r) or 0.0
                curves.append(curve)
                weights.append(w)
                picked.append(
                    {
                        "rel": r["rel"],
                        "strategy_name": r.get("strategy_name"),
                        "raw_weight": w,
                    }
                )

            combined = _combine_curves(curves, weights)
            stats = _equity_drawdown_stats(combined)
            stats["hit_ratio"] = _hit_ratio(combined)
            normalized = _normalize_weights(weights)
            for p, w in zip(picked, normalized, strict=True):
                p["normalized_weight"] = round(w, 6)

            return {
                "ok": True,
                "project": project,
                "databank": databank,
                "rank_mode": args.rank_mode,
                "weight_by": args.weight_by,
                "curve": args.curve,
                "candidates_total": len(rows),
                "picked_count": len(picked),
                "unparseable_count": len(bad),
                "combined_curve_length": len(combined),
                "combined_first": combined[0] if combined else None,
                "combined_last": combined[-1] if combined else None,
                "combined_stats": stats,
                "picked": picked,
            }
        except (EngineError, ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Same idea as portfolio_combined_equity but the caller hands in an "
            "explicit list of (sqx_path, weight) pairs. Weights are normalized "
            "to sum 1. Use this to verify the allocations returned by "
            "portfolio_capital_allocation actually produce the equity shape "
            "you expect. Read-only."
        )
    )
    async def portfolio_combined_equity_explicit(
        args: PortfolioCombinedEquityExplicitArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            curves: list[list[float]] = []
            weights: list[float] = []
            picked: list[dict[str, Any]] = []
            for item in args.items:
                path = item.get("sqx_path")
                if not path:
                    continue
                p = resolve_safe_path(path, must_exist=True)
                if p.suffix.lower() != ".sqx":
                    continue
                info = parse_sqx(p)
                curve = _select_curve(info, args.curve)
                if len(curve) < 4:
                    continue
                w = float(item.get("weight", 1.0))
                curves.append(curve)
                weights.append(w)
                picked.append({"sqx_path": str(p), "raw_weight": w})

            combined = _combine_curves(curves, weights)
            stats = _equity_drawdown_stats(combined)
            stats["hit_ratio"] = _hit_ratio(combined)
            normalized = _normalize_weights(weights) if weights else []
            for pp, w in zip(picked, normalized, strict=True):
                pp["normalized_weight"] = round(w, 6)
            return {
                "ok": True,
                "curve": args.curve,
                "input_count": len(args.items),
                "picked_count": len(picked),
                "combined_curve_length": len(combined),
                "combined_stats": stats,
                "picked": picked,
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Decompose a portfolio's return + max-DD into per-strategy contributions. "
            "Takes an explicit list of {sqx_path, weight} pairs, normalizes weights, "
            "and for each strategy reports: its individual return, individual max DD, "
            "the weighted return contribution (w×return), and weighted DD contribution. "
            "Use after portfolio_capital_allocation to see *who's driving the numbers*. "
            "Read-only."
        )
    )
    async def portfolio_contribution(
        args: PortfolioContributionArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            curves: list[list[float]] = []
            weights: list[float] = []
            labels: list[str] = []
            for item in args.items:
                p_str = item.get("sqx_path")
                if not p_str:
                    continue
                p = resolve_safe_path(p_str, must_exist=True)
                if p.suffix.lower() != ".sqx":
                    continue
                info = parse_sqx(p)
                curve = _select_curve(info, args.curve)
                if not curve:
                    continue
                curves.append(curve)
                weights.append(float(item.get("weight", 1.0)))
                labels.append(str(p))
            decomp = _contribution_analysis(curves, weights, labels)
            return {
                "ok": True,
                "input_count": len(args.items),
                "included_count": len(curves),
                **decomp,
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Estimate per-month returns from the equity sparkline of a single .sqx. "
            "Assumes uniform per-sample spacing across history_years (which is "
            "approximate but useful for shape analysis). Read-only."
        )
    )
    async def strategy_monthly_returns_estimate(
        args: StrategyMonthlyReturnsArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected .sqx"}
            info = parse_sqx(p)
            metrics = derive_metrics(info)
            curve = _select_curve(info, "full")
            hist_years = metrics.get("history_years") or 1.0
            months = args.months_override or max(1, int(hist_years * 12))
            rows = _monthly_returns_estimate(curve, months=months)
            ret_vals = [r["return"] for r in rows if r["return"] is not None]
            best = max(ret_vals) if ret_vals else None
            worst = min(ret_vals) if ret_vals else None
            positive = sum(1 for r in ret_vals if r > 0)
            return {
                "ok": True,
                "sqx_path": str(p),
                "months_modeled": months,
                "history_years": hist_years,
                "positive_months": positive,
                "negative_months": len(ret_vals) - positive,
                "best_month_return": best,
                "worst_month_return": worst,
                "monthly_returns": rows,
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Risk-adjusted return metrics for a strategy: Sharpe, Sortino, and "
            "Calmar ratios from the equity sparkline. Returns are bucketed into "
            "approximately monthly periods (history_years × 12) and annualized "
            "with periods_per_year=12. risk_free_rate is annual decimal (e.g. "
            "0.04 = 4% RFR). Read-only."
        )
    )
    async def strategy_risk_adjusted_metrics(
        args: StrategyMonthlyReturnsArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected .sqx"}
            info = parse_sqx(p)
            metrics = derive_metrics(info)
            curve = _select_curve(info, "full")
            hist_years = metrics.get("history_years") or 1.0
            months = args.months_override or max(1, int(hist_years * 12))
            rows = _monthly_returns_estimate(curve, months=months)
            returns = [r["return"] for r in rows if r["return"] is not None]
            stats = _equity_drawdown_stats(curve)
            ann_return = _annualized_return_from_curve(
                curve, history_years=hist_years
            )
            sharpe = _sharpe_ratio(returns, periods_per_year=12)
            sortino = _sortino_ratio(returns, periods_per_year=12)
            calmar = _calmar_ratio(
                annualized_return=ann_return,
                max_drawdown_pct_of_peak=stats.get("max_drawdown_pct_of_peak"),
            )
            return {
                "ok": True,
                "sqx_path": str(p),
                "history_years": hist_years,
                "annualized_return": ann_return,
                "max_drawdown_pct_of_peak": stats.get("max_drawdown_pct_of_peak"),
                "monthly_sample_count": len(returns),
                "sharpe_ratio_annualized": sharpe,
                "sortino_ratio_annualized": sortino,
                "calmar_ratio": calmar,
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "PortfolioCombinedEquityArgs",
    "PortfolioCombinedEquityExplicitArgs",
    "PortfolioContributionArgs",
    "StrategyMonthlyReturnsArgs",
    "_annualized_return_from_curve",
    "_bucket_curve_by_count",
    "_calmar_ratio",
    "_combine_curves",
    "_contribution_analysis",
    "_monthly_returns_estimate",
    "_normalize_weights",
    "_sharpe_ratio",
    "_sortino_ratio",
    "register",
]

# Used in tests
_ = _curve_to_returns
