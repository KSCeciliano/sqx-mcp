"""Benchmark comparison — strategy returns vs buy-and-hold / market.

A strategy that "made 30% in 2024" isn't impressive if BTCUSDT spot
made 120% over the same window. These tools normalize strategy
performance against a benchmark you supply (a buy-and-hold curve, an
equal-weight index, or another strategy).

Key outputs: excess return, alpha, beta, correlation, tracking error,
win-rate-vs-benchmark, time underperforming.

Tools:

- ``benchmark_compare_curves`` — full side-by-side: total return,
  CAGR, max DD, Sharpe, plus excess return vs benchmark.
- ``benchmark_alpha_beta`` — OLS regression of strategy returns on
  benchmark returns; alpha (intercept) and beta (slope).
- ``benchmark_excess_return_series`` — return strategy − benchmark
  per period as a list, plus summary stats.
- ``benchmark_outperformance_periods`` — count periods where
  strategy ≥ benchmark; longest outperformance streak.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._common import safe_error_payload
from sq_mcp.tools._numerics import NumericValidationError, validate_finite_floats

# ---- argument schemas ------------------------------------------------------


class BenchmarkCurvesArgs(BaseModel):
    strategy_curve: list[float] = Field(..., min_length=10, max_length=100_000)
    benchmark_curve: list[float] = Field(..., min_length=10, max_length=100_000)
    periods_per_year: int = Field(252, ge=1, le=525_600)


class AlphaBetaArgs(BaseModel):
    strategy_returns: list[float] = Field(..., min_length=10, max_length=100_000)
    benchmark_returns: list[float] = Field(..., min_length=10, max_length=100_000)


class ExcessReturnArgs(BaseModel):
    strategy_returns: list[float] = Field(..., min_length=10, max_length=100_000)
    benchmark_returns: list[float] = Field(..., min_length=10, max_length=100_000)


class OutperformanceArgs(BaseModel):
    strategy_returns: list[float] = Field(..., min_length=10, max_length=100_000)
    benchmark_returns: list[float] = Field(..., min_length=10, max_length=100_000)


# ---- helpers ---------------------------------------------------------------


def _curve_to_returns(curve: list[float]) -> list[float]:
    out = []
    for i in range(1, len(curve)):
        prev = curve[i - 1]
        if prev == 0:
            out.append(0.0)
        else:
            out.append((curve[i] - prev) / abs(prev))
    return out


def _max_dd(curve: list[float]) -> float:
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _sharpe_from_returns(returns: list[float], periods_per_year: int) -> float | None:
    s = _std(returns)
    if s == 0:
        return None
    return (_mean(returns) / s) * math.sqrt(periods_per_year)


def _compare_curves(args: BenchmarkCurvesArgs) -> dict[str, Any]:
    validate_finite_floats(args.strategy_curve, name="strategy_curve")
    validate_finite_floats(args.benchmark_curve, name="benchmark_curve")
    s_curve = args.strategy_curve
    b_curve = args.benchmark_curve

    # Truncate to same length
    n = min(len(s_curve), len(b_curve))
    s_curve = s_curve[:n]
    b_curve = b_curve[:n]

    s_returns = _curve_to_returns(s_curve)
    b_returns = _curve_to_returns(b_curve)

    s_total = (s_curve[-1] - s_curve[0]) / abs(s_curve[0]) * 100 if s_curve[0] else 0.0
    b_total = (b_curve[-1] - b_curve[0]) / abs(b_curve[0]) * 100 if b_curve[0] else 0.0

    s_dd = _max_dd(s_curve) * 100
    b_dd = _max_dd(b_curve) * 100

    s_sharpe = _sharpe_from_returns(s_returns, args.periods_per_year)
    b_sharpe = _sharpe_from_returns(b_returns, args.periods_per_year)

    excess_total = s_total - b_total
    return {
        "n_samples": n,
        "strategy": {
            "total_return_pct": round(s_total, 4),
            "max_drawdown_pct": round(s_dd, 4),
            "sharpe": round(s_sharpe, 4) if s_sharpe is not None else None,
        },
        "benchmark": {
            "total_return_pct": round(b_total, 4),
            "max_drawdown_pct": round(b_dd, 4),
            "sharpe": round(b_sharpe, 4) if b_sharpe is not None else None,
        },
        "excess_return_pct": round(excess_total, 4),
        "verdict": (
            "outperforms_benchmark"
            if excess_total > 0
            else "matches_benchmark"
            if abs(excess_total) < 1.0
            else "underperforms_benchmark"
        ),
    }


def _alpha_beta(
    strategy_returns: list[float], benchmark_returns: list[float]
) -> dict[str, Any]:
    validate_finite_floats(strategy_returns, name="strategy_returns")
    validate_finite_floats(benchmark_returns, name="benchmark_returns")
    n = min(len(strategy_returns), len(benchmark_returns))
    s = strategy_returns[:n]
    b = benchmark_returns[:n]
    s_mean = _mean(s)
    b_mean = _mean(b)
    cov = sum((s[i] - s_mean) * (b[i] - b_mean) for i in range(n)) / max(1, n - 1)
    var_b = sum((bi - b_mean) ** 2 for bi in b) / max(1, n - 1)
    if var_b == 0:
        return {"alpha": None, "beta": None, "note": "benchmark variance is zero"}
    beta = cov / var_b
    alpha = s_mean - beta * b_mean
    # Correlation
    var_s = sum((si - s_mean) ** 2 for si in s) / max(1, n - 1)
    if var_s == 0:
        corr = None
    else:
        corr = cov / math.sqrt(var_s * var_b)
    return {
        "alpha": round(alpha, 6),
        "beta": round(beta, 6),
        "correlation": round(corr, 4) if corr is not None else None,
        "n_periods": n,
    }


def _excess_returns(
    strategy_returns: list[float], benchmark_returns: list[float]
) -> dict[str, Any]:
    validate_finite_floats(strategy_returns, name="strategy_returns")
    validate_finite_floats(benchmark_returns, name="benchmark_returns")
    n = min(len(strategy_returns), len(benchmark_returns))
    s = strategy_returns[:n]
    b = benchmark_returns[:n]
    excess = [si - bi for si, bi in zip(s, b, strict=False)]
    return {
        "n_periods": n,
        "mean_excess_per_period": round(_mean(excess), 6),
        "std_excess_per_period": round(_std(excess), 6),
        "min_excess": round(min(excess), 6) if excess else None,
        "max_excess": round(max(excess), 6) if excess else None,
        "excess_returns": [round(e, 6) for e in excess][:200],  # cap response size
    }


def _outperformance_streaks(
    strategy_returns: list[float], benchmark_returns: list[float]
) -> dict[str, Any]:
    validate_finite_floats(strategy_returns, name="strategy_returns")
    validate_finite_floats(benchmark_returns, name="benchmark_returns")
    n = min(len(strategy_returns), len(benchmark_returns))
    wins = 0
    longest_outperf_streak = 0
    longest_underperf_streak = 0
    cur_outperf = 0
    cur_underperf = 0
    for i in range(n):
        if strategy_returns[i] > benchmark_returns[i]:
            wins += 1
            cur_outperf += 1
            cur_underperf = 0
            longest_outperf_streak = max(longest_outperf_streak, cur_outperf)
        elif strategy_returns[i] < benchmark_returns[i]:
            cur_outperf = 0
            cur_underperf += 1
            longest_underperf_streak = max(longest_underperf_streak, cur_underperf)
        else:
            cur_outperf = 0
            cur_underperf = 0
    return {
        "n_periods": n,
        "win_rate_vs_benchmark": round(wins / n, 4) if n > 0 else None,
        "longest_outperformance_streak": longest_outperf_streak,
        "longest_underperformance_streak": longest_underperf_streak,
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Side-by-side curve comparison: strategy vs benchmark total return, "
            "max DD, Sharpe; reports excess return and a verdict (outperforms / "
            "matches / underperforms). Curves are truncated to common length."
        )
    )
    async def benchmark_compare_curves(args: BenchmarkCurvesArgs) -> dict:
        try:
            return {"ok": True, **_compare_curves(args)}
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Alpha (intercept) and beta (slope) of strategy returns regressed on "
            "benchmark returns. Plus correlation. Beta>1 = strategy moves more "
            "than the benchmark; alpha>0 = excess return after controlling for "
            "beta."
        )
    )
    async def benchmark_alpha_beta(args: AlphaBetaArgs) -> dict:
        try:
            return {
                "ok": True,
                **_alpha_beta(args.strategy_returns, args.benchmark_returns),
            }
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Return strategy minus benchmark per period as a list (capped to "
            "first 200), plus mean/std/min/max of the excess series."
        )
    )
    async def benchmark_excess_return_series(args: ExcessReturnArgs) -> dict:
        try:
            return {
                "ok": True,
                **_excess_returns(args.strategy_returns, args.benchmark_returns),
            }
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Per-period wins vs benchmark + longest outperformance / "
            "underperformance streaks. Use to detect strategies that win "
            "rarely but big vs those that win often by a little."
        )
    )
    async def benchmark_outperformance_periods(args: OutperformanceArgs) -> dict:
        try:
            return {
                "ok": True,
                **_outperformance_streaks(args.strategy_returns, args.benchmark_returns),
            }
        except NumericValidationError as exc:
            return safe_error_payload(exc)
