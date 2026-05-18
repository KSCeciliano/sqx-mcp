"""Portfolio optimization: weight schemes and minimum-variance allocation.

Tools:

- ``portfolio_equal_weight`` — trivially 1/N weights for a list of
  strategies; returns risk and return summaries.
- ``portfolio_inverse_volatility`` — weights inversely proportional to
  each strategy's volatility (target equal risk contribution).
- ``portfolio_risk_parity`` — iterative risk-parity solver. Each
  strategy contributes equally to portfolio variance.
- ``portfolio_min_variance`` — pure-Python minimum-variance allocator
  using a simple coordinate-descent over the covariance matrix.
- ``portfolio_diversification_ratio`` — ratio of weighted vols to
  portfolio vol (>1.0 means diversification works).

Pure Python; no numpy. Inputs are per-strategy return series.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)


class ReturnsMatrixArgs(BaseModel):
    returns_by_strategy: dict[str, list[float]] = Field(
        ..., description="Map of strategy name → return series"
    )


class RiskParityArgs(ReturnsMatrixArgs):
    max_iter: int = Field(200, ge=10, le=10_000)
    tol: float = Field(1e-6, gt=0.0, lt=1.0)


class MinVarianceArgs(ReturnsMatrixArgs):
    max_iter: int = Field(200, ge=10, le=10_000)
    tol: float = Field(1e-6, gt=0.0, lt=1.0)


class DiversificationArgs(BaseModel):
    weights: dict[str, float] = Field(..., min_length=2, max_length=200)
    returns_by_strategy: dict[str, list[float]] = Field(..., min_length=2, max_length=200)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _covariance_matrix(
    returns: dict[str, list[float]],
) -> tuple[list[str], list[list[float]]]:
    for k, v in returns.items():
        validate_finite_floats(v, name=f"returns[{k!r}]")
    names = sorted(returns.keys())
    n = min(len(returns[k]) for k in names) if names else 0
    series = [returns[k][:n] for k in names]
    means = [_mean(s) for s in series]
    cov: list[list[float]] = [[0.0] * len(names) for _ in names]
    if n < 2:
        return names, cov
    for i in range(len(names)):
        for j in range(i, len(names)):
            c = sum(
                (series[i][t] - means[i]) * (series[j][t] - means[j])
                for t in range(n)
            ) / (n - 1)
            cov[i][j] = c
            cov[j][i] = c
    return names, cov


def _portfolio_variance(weights: list[float], cov: list[list[float]]) -> float:
    n = len(weights)
    s = 0.0
    for i in range(n):
        for j in range(n):
            s += weights[i] * weights[j] * cov[i][j]
    return s


def _equal_weight(returns: dict[str, list[float]]) -> dict[str, Any]:
    names = sorted(returns.keys())
    n = len(names)
    if n == 0:
        return {"weights": {}, "n": 0}
    w = 1.0 / n
    weights = dict.fromkeys(names, w)
    _, cov = _covariance_matrix(returns)
    var = _portfolio_variance([w] * n, cov)
    return {
        "weights": weights,
        "portfolio_variance": round(var, 8),
        "portfolio_std": round(math.sqrt(max(0.0, var)), 8),
        "n": n,
    }


def _inverse_vol(returns: dict[str, list[float]]) -> dict[str, Any]:
    for k, v in returns.items():
        validate_finite_floats(v, name=f"returns[{k!r}]")
    names = sorted(returns.keys())
    vols = {k: _std(returns[k]) for k in names}
    raw = {k: (1.0 / v) if v > 0 else 0.0 for k, v in vols.items()}
    total = sum(raw.values())
    if total == 0:
        return {"weights": {k: 0.0 for k in names}, "note": "all-zero vol"}
    weights = {k: raw[k] / total for k in names}
    _, cov = _covariance_matrix(returns)
    w_vec = [weights[k] for k in names]
    var = _portfolio_variance(w_vec, cov)
    return {
        "weights": weights,
        "vols": {k: round(v, 8) for k, v in vols.items()},
        "portfolio_variance": round(var, 8),
        "portfolio_std": round(math.sqrt(max(0.0, var)), 8),
        "n": len(names),
    }


def _risk_parity(
    returns: dict[str, list[float]], *, max_iter: int, tol: float
) -> dict[str, Any]:
    """Iterative risk parity solver: w_i ∝ 1 / risk_contribution_i."""
    names, cov = _covariance_matrix(returns)
    n = len(names)
    if n == 0:
        return {"weights": {}}
    w = [1.0 / n] * n
    for _ in range(max_iter):
        # Compute marginal contributions: cov @ w
        mrc = [sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]
        var = sum(w[i] * mrc[i] for i in range(n))
        if var <= 0:
            break
        risk_contrib = [w[i] * mrc[i] / var for i in range(n)]
        # Adjust weights to equalize risk contributions
        target = 1.0 / n
        new_w = [
            w[i] * (target / risk_contrib[i]) if risk_contrib[i] > 0 else w[i]
            for i in range(n)
        ]
        s = sum(new_w)
        if s <= 0:
            break
        new_w = [x / s for x in new_w]
        delta = max(abs(new_w[i] - w[i]) for i in range(n))
        w = new_w
        if delta < tol:
            break
    weights = {names[i]: round(w[i], 8) for i in range(n)}
    var = _portfolio_variance(w, cov)
    return {
        "weights": weights,
        "portfolio_variance": round(var, 8),
        "portfolio_std": round(math.sqrt(max(0.0, var)), 8),
        "n": n,
    }


def _min_variance(
    returns: dict[str, list[float]], *, max_iter: int, tol: float
) -> dict[str, Any]:
    """Coordinate descent minimum-variance with long-only, sum=1 constraint."""
    names, cov = _covariance_matrix(returns)
    n = len(names)
    if n == 0:
        return {"weights": {}}
    if n == 1:
        return {"weights": {names[0]: 1.0}}
    w = [1.0 / n] * n
    for _ in range(max_iter):
        old = list(w)
        for i in range(n):
            # Variance contribution of i with current weights:
            #   d/dw_i variance = 2 * sum_j cov_ij * w_j
            # Equalize via heuristic: lower w_i proportional to its marginal.
            marginal_i = sum(cov[i][j] * w[j] for j in range(n))
            avg_marginal = sum(
                w[j] * sum(cov[j][k] * w[k] for k in range(n)) for j in range(n)
            )
            # Push w_i down if it has above-avg variance contribution
            if avg_marginal > 0 and marginal_i > 0:
                w[i] = w[i] * (avg_marginal / (marginal_i * n))
        s = sum(w)
        if s <= 0:
            break
        w = [x / s for x in w]
        delta = max(abs(w[i] - old[i]) for i in range(n))
        if delta < tol:
            break
    weights = {names[i]: round(w[i], 8) for i in range(n)}
    var = _portfolio_variance(w, cov)
    return {
        "weights": weights,
        "portfolio_variance": round(var, 8),
        "portfolio_std": round(math.sqrt(max(0.0, var)), 8),
        "n": n,
    }


def _diversification_ratio(
    weights: dict[str, float], returns: dict[str, list[float]]
) -> dict[str, Any]:
    names = sorted(set(weights.keys()) & set(returns.keys()))
    if len(names) < 2:
        return {"diversification_ratio": None, "note": "need ≥2 matched strategies"}
    w_vec = [weights[k] for k in names]
    vols = [_std(returns[k]) for k in names]
    sum_w_vol = sum(w_vec[i] * vols[i] for i in range(len(names)))
    _, cov = _covariance_matrix({k: returns[k] for k in names})
    port_var = _portfolio_variance(w_vec, cov)
    port_vol = math.sqrt(max(0.0, port_var))
    if port_vol == 0:
        return {"diversification_ratio": None, "note": "zero portfolio variance"}
    dr = sum_w_vol / port_vol
    return {
        "diversification_ratio": round(dr, 6),
        "sum_weighted_vols": round(sum_w_vol, 8),
        "portfolio_vol": round(port_vol, 8),
        "interpretation": (
            "strong_diversification" if dr > 1.5
            else "moderate_diversification" if dr > 1.2
            else "weak_diversification"
        ),
        "n": len(names),
    }


def _safe(fn, *fn_args, **fn_kwargs):  # noqa: ANN001, ANN002, ANN003
    try:
        return {"ok": True, **fn(*fn_args, **fn_kwargs)}
    except NumericValidationError as exc:
        return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Equal-weight portfolio: 1/N for N strategies. Also reports the "
            "resulting portfolio variance and std. Use as a baseline when "
            "comparing optimizers. Read-only."
        )
    )
    async def portfolio_equal_weight(args: ReturnsMatrixArgs) -> dict:
        return _safe(_equal_weight, args.returns_by_strategy)

    @mcp.tool(
        description=(
            "Inverse-volatility weights: w_i ∝ 1/σ_i. Aims to equalize each "
            "strategy's risk contribution. Simpler proxy for risk parity when "
            "covariance is hard to estimate."
        )
    )
    async def portfolio_inverse_volatility(args: ReturnsMatrixArgs) -> dict:
        return _safe(_inverse_vol, args.returns_by_strategy)

    @mcp.tool(
        description=(
            "Iterative risk-parity allocation: each strategy contributes "
            "equally to the portfolio's total variance. Uses the covariance "
            "matrix. Converges in ~10–50 iterations for typical portfolios."
        )
    )
    async def portfolio_risk_parity(args: RiskParityArgs) -> dict:
        return _safe(
            _risk_parity,
            args.returns_by_strategy,
            max_iter=args.max_iter,
            tol=args.tol,
        )

    @mcp.tool(
        description=(
            "Minimum-variance allocation via coordinate descent (long-only, "
            "sum-to-1 constraint). Useful when you want the lowest-risk "
            "portfolio regardless of expected return."
        )
    )
    async def portfolio_min_variance(args: MinVarianceArgs) -> dict:
        return _safe(
            _min_variance,
            args.returns_by_strategy,
            max_iter=args.max_iter,
            tol=args.tol,
        )

    @mcp.tool(
        description=(
            "Diversification ratio = Σ(w_i · σ_i) / σ_portfolio. >1.5 = strong "
            "diversification; ~1.0 = no benefit (perfectly correlated). "
            "Read-only."
        )
    )
    async def portfolio_diversification_ratio(args: DiversificationArgs) -> dict:
        return _safe(
            _diversification_ratio, args.weights, args.returns_by_strategy
        )


__all__ = [
    "DiversificationArgs",
    "MinVarianceArgs",
    "ReturnsMatrixArgs",
    "RiskParityArgs",
    "_covariance_matrix",
    "_diversification_ratio",
    "_equal_weight",
    "_inverse_vol",
    "_min_variance",
    "_portfolio_variance",
    "_risk_parity",
    "register",
]
