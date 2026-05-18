"""Extra statistics: skewness, kurtosis, autocorrelation, runs test, IRR.

Tools:

- ``stats_skewness`` — sample skewness (Fisher-Pearson, bias-corrected).
- ``stats_kurtosis`` — sample excess kurtosis (Fisher, bias-corrected;
  Normal = 0).
- ``stats_jarque_bera`` — Jarque-Bera normality test statistic + p-value.
- ``stats_autocorrelation`` — sample autocorrelation at lag k, plus
  the Ljung-Box test for serial dependence.
- ``stats_runs_test`` — Wald-Wolfowitz runs test on the sign of returns.
- ``stats_summary`` — one-shot: mean, median, std, skew, kurtosis,
  min/max, quartiles.
- ``stats_irr`` — internal rate of return given a series of cashflows.
- ``stats_time_weighted_return`` — chained per-period returns.

Pure Python, no numpy.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import NumericValidationError, validate_finite_floats
from sq_mcp.tools.overfit_diag import _norm_cdf


class ReturnsArgs(BaseModel):
    returns: list[float] = Field(..., min_length=10, max_length=500_000)


class AutocorrArgs(BaseModel):
    series: list[float] = Field(..., min_length=10, max_length=500_000)
    lag: int = Field(1, ge=1, le=500)


class IRRArgs(BaseModel):
    cashflows: list[float] = Field(..., min_length=2, max_length=10_000)
    guess: float = Field(0.1, gt=-0.999, lt=10.0)
    max_iter: int = Field(200, ge=10, le=10_000)
    tol: float = Field(1e-8, gt=0.0, lt=1.0)


class PeriodicReturnsArgs(BaseModel):
    period_returns_pct: list[float] = Field(..., min_length=1, max_length=100_000)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std_sample(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _skewness(xs: list[float]) -> dict[str, Any]:
    validate_finite_floats(xs, name="values")
    n = len(xs)
    if n < 3:
        return {"skewness": None, "note": "need ≥3 samples"}
    m = _mean(xs)
    s = _std_sample(xs)
    if s == 0:
        return {"skewness": 0.0, "note": "zero variance"}
    g1 = sum((x - m) ** 3 for x in xs) / n / (s ** 3)
    g1_adj = g1 * math.sqrt(n * (n - 1)) / (n - 2)
    return {
        "skewness": round(g1_adj, 6),
        "n": n,
        "interpretation": (
            "positive_skew" if g1_adj > 0.5
            else "negative_skew" if g1_adj < -0.5
            else "approximately_symmetric"
        ),
    }


def _kurtosis(xs: list[float]) -> dict[str, Any]:
    validate_finite_floats(xs, name="values")
    n = len(xs)
    if n < 4:
        return {"excess_kurtosis": None, "note": "need ≥4 samples"}
    m = _mean(xs)
    s = _std_sample(xs)
    if s == 0:
        return {"excess_kurtosis": 0.0, "note": "zero variance"}
    g2 = sum((x - m) ** 4 for x in xs) / n / (s ** 4) - 3.0
    # bias-corrected adjusted version
    g2_adj = (n - 1) / ((n - 2) * (n - 3)) * ((n + 1) * g2 + 6.0)
    return {
        "excess_kurtosis": round(g2_adj, 6),
        "raw_kurtosis": round(g2 + 3.0, 6),
        "n": n,
        "interpretation": (
            "fat_tails" if g2_adj > 1.0
            else "thin_tails" if g2_adj < -1.0
            else "approximately_normal_tails"
        ),
    }


def _jarque_bera(xs: list[float]) -> dict[str, Any]:
    validate_finite_floats(xs, name="values")
    n = len(xs)
    if n < 10:
        return {"jb_statistic": None, "note": "need ≥10 samples"}
    s = _skewness(xs).get("skewness") or 0.0
    k = _kurtosis(xs).get("excess_kurtosis") or 0.0
    jb = (n / 6.0) * (s ** 2 + (k ** 2) / 4.0)
    # χ²(2) survival via exp(-jb/2) (closed form for df=2)
    p = math.exp(-jb / 2.0) if jb > 0 else 1.0
    return {
        "jb_statistic": round(jb, 6),
        "p_value": round(p, 6),
        "skewness": s,
        "excess_kurtosis": k,
        "verdict": (
            "normal" if p > 0.10
            else "borderline" if p > 0.05
            else "non_normal"
        ),
    }


def _autocorrelation(series: list[float], lag: int) -> dict[str, Any]:
    validate_finite_floats(series, name="series")
    n = len(series)
    if n <= lag:
        return {"autocorrelation": None, "note": "lag ≥ sample size"}
    m = _mean(series)
    denom = sum((x - m) ** 2 for x in series)
    if denom == 0:
        return {"autocorrelation": 0.0, "note": "zero variance"}
    numer = sum((series[i] - m) * (series[i - lag] - m) for i in range(lag, n))
    rho = numer / denom
    # Ljung-Box approximation for one lag
    q = n * (n + 2) * (rho ** 2) / (n - lag)
    p = math.exp(-q / 2.0) if q > 0 else 1.0  # χ²(1) survival approximation
    return {
        "autocorrelation": round(rho, 6),
        "lag": lag,
        "ljung_box_q": round(q, 6),
        "p_value": round(p, 6),
        "verdict": (
            "independent" if p > 0.10
            else "dependent" if p < 0.05
            else "borderline"
        ),
    }


def _runs_test(values: list[float]) -> dict[str, Any]:
    """Wald-Wolfowitz runs test on the sign of values."""
    validate_finite_floats(values, name="values")
    signs = [1 if v > 0 else -1 for v in values if v != 0]
    n = len(signs)
    if n < 10:
        return {"runs": None, "note": "need ≥10 non-zero samples"}
    n1 = sum(1 for s in signs if s > 0)
    n2 = n - n1
    if n1 == 0 or n2 == 0:
        return {"runs": None, "note": "all same sign — no runs"}
    runs = 1
    for i in range(1, n):
        if signs[i] != signs[i - 1]:
            runs += 1
    expected_runs = (2.0 * n1 * n2) / n + 1.0
    var = (2.0 * n1 * n2 * (2.0 * n1 * n2 - n)) / (n ** 2 * (n - 1))
    if var <= 0:
        return {"runs": runs, "note": "variance undefined"}
    z = (runs - expected_runs) / math.sqrt(var)
    p = 2.0 * (1.0 - _norm_cdf(abs(z)))
    return {
        "runs": runs,
        "expected_runs": round(expected_runs, 4),
        "z_score": round(z, 4),
        "p_value": round(p, 6),
        "n_positive": n1,
        "n_negative": n2,
        "verdict": (
            "independent" if p > 0.10
            else "dependent" if p < 0.05
            else "borderline"
        ),
    }


def _summary(xs: list[float]) -> dict[str, Any]:
    validate_finite_floats(xs, name="values")
    sorted_xs = sorted(xs)
    n = len(xs)

    def _pctl(p: float) -> float:
        idx = (n - 1) * p
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return sorted_xs[lo]
        return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (idx - lo)

    m = _mean(xs)
    s = _std_sample(xs)
    return {
        "n": n,
        "mean": round(m, 6),
        "std": round(s, 6),
        "min": round(sorted_xs[0], 6),
        "p25": round(_pctl(0.25), 6),
        "median": round(_pctl(0.50), 6),
        "p75": round(_pctl(0.75), 6),
        "max": round(sorted_xs[-1], 6),
        "skewness": _skewness(xs).get("skewness"),
        "excess_kurtosis": _kurtosis(xs).get("excess_kurtosis"),
    }


def _irr(cashflows: list[float], guess: float, max_iter: int, tol: float) -> dict[str, Any]:
    """Internal rate of return via Newton-Raphson on NPV."""
    validate_finite_floats(cashflows, name="cashflows")
    if not any(c > 0 for c in cashflows) or not any(c < 0 for c in cashflows):
        return {"irr": None, "note": "need both positive and negative cashflows"}
    r = guess
    for _ in range(max_iter):
        npv = sum(c / ((1.0 + r) ** t) for t, c in enumerate(cashflows))
        d_npv = sum(-t * c / ((1.0 + r) ** (t + 1)) for t, c in enumerate(cashflows))
        if d_npv == 0:
            break
        r_new = r - npv / d_npv
        if abs(r_new - r) < tol:
            return {
                "irr": round(r_new, 8),
                "irr_pct": round(r_new * 100.0, 6),
                "iterations": _ + 1,
                "converged": True,
            }
        r = r_new
    return {"irr": round(r, 8), "converged": False, "note": "Newton-Raphson did not converge"}


def _time_weighted_return(period_returns_pct: list[float]) -> dict[str, Any]:
    """Chain per-period returns: TWR = prod(1+r_i) - 1."""
    validate_finite_floats(period_returns_pct, name="period_returns_pct")
    product = 1.0
    for r in period_returns_pct:
        product *= 1.0 + r / 100.0
    twr_pct = (product - 1.0) * 100.0
    return {
        "twr_pct": round(twr_pct, 6),
        "n_periods": len(period_returns_pct),
        "annualized_pct": round(
            ((product ** (252.0 / max(len(period_returns_pct), 1))) - 1.0) * 100.0, 6
        ) if period_returns_pct else 0.0,
    }


def _safe(fn, *fn_args):  # noqa: ANN001, ANN002
    try:
        return {"ok": True, **fn(*fn_args)}
    except NumericValidationError as exc:
        return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Sample skewness (Fisher-Pearson, bias-corrected). Positive = right "
            "tail heavier; negative = left tail heavier. Useful for spotting "
            "asymmetric P&L distributions. Read-only."
        )
    )
    async def stats_skewness(args: ReturnsArgs) -> dict:
        return _safe(_skewness, args.returns)

    @mcp.tool(
        description=(
            "Sample excess kurtosis (bias-corrected). Normal = 0. Positive = "
            "fat tails (more extreme observations than Normal predicts); "
            "negative = thin tails."
        )
    )
    async def stats_kurtosis(args: ReturnsArgs) -> dict:
        return _safe(_kurtosis, args.returns)

    @mcp.tool(
        description=(
            "Jarque-Bera normality test on a return series. Combines skewness "
            "and kurtosis into a single χ² statistic. p > 0.10 = consistent "
            "with Normal; p < 0.05 = reject normality."
        )
    )
    async def stats_jarque_bera(args: ReturnsArgs) -> dict:
        return _safe(_jarque_bera, args.returns)

    @mcp.tool(
        description=(
            "Sample autocorrelation at the given lag, plus a single-lag "
            "Ljung-Box statistic. Use to detect serial dependence in returns — "
            "non-zero autocorrelation often indicates trend/momentum effects."
        )
    )
    async def stats_autocorrelation(args: AutocorrArgs) -> dict:
        return _safe(_autocorrelation, args.series, args.lag)

    @mcp.tool(
        description=(
            "Wald-Wolfowitz runs test on the sign of returns. p > 0.10 = "
            "consistent with independence; p < 0.05 = significant streaks or "
            "alternation in the sign pattern."
        )
    )
    async def stats_runs_test(args: ReturnsArgs) -> dict:
        return _safe(_runs_test, args.returns)

    @mcp.tool(
        description=(
            "One-shot descriptive statistics: mean, median, std, skew, "
            "kurtosis, min, p25, p75, max. Useful starting point before any "
            "deeper analysis. Read-only."
        )
    )
    async def stats_summary(args: ReturnsArgs) -> dict:
        return _safe(_summary, args.returns)

    @mcp.tool(
        description=(
            "Internal Rate of Return from a cashflow series via Newton-Raphson. "
            "Cashflows must include at least one positive and one negative. "
            "Returns the rate that makes NPV = 0. Read-only."
        )
    )
    async def stats_irr(args: IRRArgs) -> dict:
        return _safe(_irr, args.cashflows, args.guess, args.max_iter, args.tol)

    @mcp.tool(
        description=(
            "Time-Weighted Return: chain per-period returns (1+r1)(1+r2)…(1+rN) "
            "− 1. Use when the strategy has external cashflows you want to "
            "exclude from the performance calc."
        )
    )
    async def stats_time_weighted_return(args: PeriodicReturnsArgs) -> dict:
        return _safe(_time_weighted_return, args.period_returns_pct)


__all__ = [
    "AutocorrArgs",
    "IRRArgs",
    "PeriodicReturnsArgs",
    "ReturnsArgs",
    "_autocorrelation",
    "_irr",
    "_jarque_bera",
    "_kurtosis",
    "_runs_test",
    "_skewness",
    "_summary",
    "_time_weighted_return",
    "register",
]
