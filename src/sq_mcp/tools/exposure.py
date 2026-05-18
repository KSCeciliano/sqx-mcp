"""Live exposure tracking: open positions, net/gross exposure, concentration.

When multiple strategies trade simultaneously, the total exposure across
positions is what actually drives portfolio risk. These tools take a
list of open positions and report aggregate exposure metrics.

Tools:

- ``exposure_summary`` — total net exposure (long − short), gross
  exposure (long + short), per-symbol breakdown.
- ``exposure_concentration_hhi`` — Herfindahl-Hirschman concentration
  on per-symbol weights. >0.25 = concentrated, <0.10 = diversified.
- ``exposure_leverage_check`` — ratio of gross exposure to equity vs
  caller's max-leverage policy; flags breaches.
- ``exposure_correlation_risk`` — given pairwise correlation between
  symbols, compute the correlation-weighted effective exposure (Σ w_i
  · w_j · ρ_ij). High = positions move together; low = diversified.
- ``exposure_var_decomposition`` — split portfolio VaR by symbol
  contribution using variance shares and given correlations.

Pure Python.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)


class Position(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=64)
    side: Literal["long", "short"]
    notional: float = Field(..., gt=0.0)


class ExposureSummaryArgs(BaseModel):
    positions: list[Position] = Field(..., min_length=1, max_length=1000)


class LeverageCheckArgs(BaseModel):
    positions: list[Position] = Field(..., min_length=1, max_length=1000)
    account_equity: float = Field(..., gt=0.0)
    max_leverage: float = Field(..., gt=0.0, le=100.0)


class CorrelationRiskArgs(BaseModel):
    positions: list[Position] = Field(..., min_length=1, max_length=1000)
    correlation_matrix: dict[str, dict[str, float]] = Field(
        ..., description="Map of symbol → {symbol: ρ_ij}; default ρ=0 for missing pairs."
    )


class VarDecompositionArgs(BaseModel):
    positions: list[Position] = Field(..., min_length=1, max_length=1000)
    symbol_volatilities: dict[str, float] = Field(..., min_length=1, max_length=1000)
    correlation_matrix: dict[str, dict[str, float]] = Field(
        ..., description="Symbol pair correlations"
    )
    confidence: float = Field(0.95, gt=0.5, lt=1.0)


def _directional_bias(net: float, gross: float) -> str:
    if gross <= 0:
        return "no_exposure"
    ratio = net / gross
    if ratio > 0.5:
        return "strongly_long"
    if ratio > 0.2:
        return "moderately_long"
    if ratio >= -0.2:
        return "balanced"
    if ratio >= -0.5:
        return "moderately_short"
    return "strongly_short"


def _exposure_summary(positions: list[dict[str, Any]]) -> dict[str, Any]:
    by_symbol: dict[str, dict[str, float]] = {}
    long_total = 0.0
    short_total = 0.0
    for p in positions:
        sym = p["symbol"]
        notional = p["notional"]
        if not isinstance(notional, (int, float)) or math.isnan(notional) or math.isinf(notional):
            raise NumericValidationError(
                f"position[{sym}].notional must be finite"
            )
        signed = notional if p["side"] == "long" else -notional
        if sym not in by_symbol:
            by_symbol[sym] = {"net": 0.0, "long": 0.0, "short": 0.0}
        by_symbol[sym]["net"] += signed
        if p["side"] == "long":
            by_symbol[sym]["long"] += notional
            long_total += notional
        else:
            by_symbol[sym]["short"] += notional
            short_total += notional
    net = long_total - short_total
    gross = long_total + short_total
    return {
        "net_exposure": round(net, 6),
        "gross_exposure": round(gross, 6),
        "long_total": round(long_total, 6),
        "short_total": round(short_total, 6),
        "n_positions": len(positions),
        "n_symbols": len(by_symbol),
        "by_symbol": {
            k: {
                "net": round(v["net"], 6),
                "long": round(v["long"], 6),
                "short": round(v["short"], 6),
            }
            for k, v in by_symbol.items()
        },
        "directional_bias": (
            _directional_bias(net, gross) if gross > 0 else "no_exposure"
        ),
    }


def _exposure_hhi(positions: list[dict[str, Any]]) -> dict[str, Any]:
    by_symbol: dict[str, float] = {}
    for p in positions:
        by_symbol[p["symbol"]] = by_symbol.get(p["symbol"], 0.0) + p["notional"]
    total = sum(by_symbol.values())
    if total <= 0:
        return {"hhi": None, "note": "zero total exposure"}
    weights = {k: v / total for k, v in by_symbol.items()}
    raw_hhi = sum(w ** 2 for w in weights.values())
    n = len(weights)
    # Normalized HHI ∈ [0, 1]
    norm_hhi = (raw_hhi - 1.0 / n) / (1.0 - 1.0 / n) if n > 1 else 1.0
    return {
        "raw_hhi": round(raw_hhi, 6),
        "normalized_hhi": round(norm_hhi, 6),
        "weights": {k: round(w, 6) for k, w in weights.items()},
        "n_symbols": n,
        "verdict": (
            "very_concentrated" if norm_hhi > 0.5
            else "concentrated" if norm_hhi > 0.25
            else "moderately_diversified" if norm_hhi > 0.10
            else "diversified"
        ),
    }


def _leverage_check(
    positions: list[dict[str, Any]], equity: float, max_lev: float
) -> dict[str, Any]:
    s = _exposure_summary(positions)
    gross_lev = s["gross_exposure"] / equity if equity > 0 else None
    net_lev = abs(s["net_exposure"]) / equity if equity > 0 else None
    return {
        "gross_leverage": round(gross_lev, 4) if gross_lev is not None else None,
        "net_leverage": round(net_lev, 4) if net_lev is not None else None,
        "max_leverage": max_lev,
        "account_equity": equity,
        "gross_exposure": s["gross_exposure"],
        "net_exposure": s["net_exposure"],
        "breach": gross_lev is not None and gross_lev > max_lev,
        "headroom_pct": (
            round(100.0 * (max_lev - gross_lev) / max_lev, 4)
            if gross_lev is not None else None
        ),
        "verdict": (
            "critical_breach" if gross_lev and gross_lev > max_lev * 1.5
            else "breach" if gross_lev and gross_lev > max_lev
            else "near_limit" if gross_lev and gross_lev > max_lev * 0.9
            else "safe"
        ),
    }


def _get_correlation(
    matrix: dict[str, dict[str, float]], sym_a: str, sym_b: str
) -> float:
    """Look up ρ(sym_a, sym_b) symmetrically. Defaults to 1.0 if same symbol, 0.0 if missing."""
    if sym_a == sym_b:
        return 1.0
    if sym_a in matrix and sym_b in matrix[sym_a]:
        return matrix[sym_a][sym_b]
    if sym_b in matrix and sym_a in matrix[sym_b]:
        return matrix[sym_b][sym_a]
    return 0.0


def _correlation_risk(
    positions: list[dict[str, Any]],
    correlation_matrix: dict[str, dict[str, float]],
) -> dict[str, Any]:
    # Aggregate signed notionals per symbol
    by_symbol: dict[str, float] = {}
    for p in positions:
        sign = 1.0 if p["side"] == "long" else -1.0
        by_symbol[p["symbol"]] = by_symbol.get(p["symbol"], 0.0) + sign * p["notional"]
    total_abs = sum(abs(v) for v in by_symbol.values())
    if total_abs == 0:
        return {"effective_exposure": 0.0, "note": "zero gross exposure"}
    weights = {k: v / total_abs for k, v in by_symbol.items()}
    symbols = sorted(weights.keys())
    # Σ_ij w_i w_j ρ_ij
    eff = 0.0
    for i in symbols:
        for j in symbols:
            eff += weights[i] * weights[j] * _get_correlation(correlation_matrix, i, j)
    eff = max(0.0, eff) ** 0.5
    return {
        "effective_exposure_fraction": round(eff, 6),
        "naive_gross": round(total_abs, 6),
        "n_symbols": len(symbols),
        "diversification_benefit_pct": round(100.0 * (1.0 - eff), 4),
        "verdict": (
            "strong_diversification" if eff < 0.6
            else "moderate_diversification" if eff < 0.85
            else "weak_diversification" if eff < 0.98
            else "highly_correlated"
        ),
    }


def _var_decomposition(
    positions: list[dict[str, Any]],
    sym_vols: dict[str, float],
    corr: dict[str, dict[str, float]],
    confidence: float,
) -> dict[str, Any]:
    validate_finite_floats(list(sym_vols.values()), name="symbol_volatilities")
    by_symbol: dict[str, float] = {}
    for p in positions:
        sign = 1.0 if p["side"] == "long" else -1.0
        by_symbol[p["symbol"]] = by_symbol.get(p["symbol"], 0.0) + sign * p["notional"]
    symbols = sorted(by_symbol.keys())
    # Portfolio variance: Σ_ij w_i w_j σ_i σ_j ρ_ij
    port_var = 0.0
    contributions: list[tuple[str, float]] = []
    for i in symbols:
        w_i = by_symbol[i]
        sigma_i = sym_vols.get(i, 0.0)
        contrib_i = 0.0
        for j in symbols:
            w_j = by_symbol[j]
            sigma_j = sym_vols.get(j, 0.0)
            rho_ij = _get_correlation(corr, i, j)
            term = w_i * w_j * sigma_i * sigma_j * rho_ij
            port_var += term
            contrib_i += term
        contributions.append((i, contrib_i))
    port_var = max(0.0, port_var)
    port_std = math.sqrt(port_var)
    # z-score for VaR at confidence (approx)
    from sq_mcp.tools.tail_risk import _inv_norm_cdf
    z = _inv_norm_cdf(confidence)
    var_value = z * port_std
    return {
        "portfolio_var": round(var_value, 6),
        "portfolio_std": round(port_std, 6),
        "confidence": confidence,
        "z_score": round(z, 6),
        "contributions": [
            {
                "symbol": s,
                "variance_contribution": round(c, 8),
                "share_pct": round(100.0 * c / port_var, 4) if port_var > 0 else 0.0,
            }
            for s, c in contributions
        ],
        "n_symbols": len(symbols),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Summarize live exposure across a set of open positions. Reports "
            "net (long − short), gross (long + short), per-symbol breakdown, "
            "and directional bias verdict (strongly_long / moderately_long / "
            "balanced / moderately_short / strongly_short)."
        )
    )
    async def exposure_summary(args: ExposureSummaryArgs) -> dict:
        try:
            return {
                "ok": True,
                **_exposure_summary([p.model_dump() for p in args.positions]),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Herfindahl-Hirschman concentration index on per-symbol notional "
            "weights. Returns raw + normalized HHI ∈ [0, 1] + verdict "
            "(very_concentrated / concentrated / moderately_diversified / "
            "diversified)."
        )
    )
    async def exposure_concentration_hhi(args: ExposureSummaryArgs) -> dict:
        try:
            return {
                "ok": True,
                **_exposure_hhi([p.model_dump() for p in args.positions]),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Check gross and net leverage vs a max-leverage policy. Reports "
            "headroom % and a verdict (safe / near_limit / breach / "
            "critical_breach)."
        )
    )
    async def exposure_leverage_check(args: LeverageCheckArgs) -> dict:
        try:
            return {
                "ok": True,
                **_leverage_check(
                    [p.model_dump() for p in args.positions],
                    args.account_equity,
                    args.max_leverage,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Correlation-weighted effective exposure: how much of the gross "
            "exposure is correlated risk vs diversified. Verdict: "
            "strong_diversification (eff<0.6) / moderate (0.6-0.85) / weak "
            "(0.85-0.98) / highly_correlated (>0.98)."
        )
    )
    async def exposure_correlation_risk(args: CorrelationRiskArgs) -> dict:
        try:
            return {
                "ok": True,
                **_correlation_risk(
                    [p.model_dump() for p in args.positions],
                    args.correlation_matrix,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Decompose portfolio VaR by symbol contribution given per-symbol "
            "volatilities and a correlation matrix. Returns total VaR + each "
            "symbol's variance contribution and share %."
        )
    )
    async def exposure_var_decomposition(args: VarDecompositionArgs) -> dict:
        try:
            return {
                "ok": True,
                **_var_decomposition(
                    [p.model_dump() for p in args.positions],
                    args.symbol_volatilities,
                    args.correlation_matrix,
                    args.confidence,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "CorrelationRiskArgs",
    "ExposureSummaryArgs",
    "LeverageCheckArgs",
    "Position",
    "VarDecompositionArgs",
    "_correlation_risk",
    "_exposure_hhi",
    "_exposure_summary",
    "_get_correlation",
    "_leverage_check",
    "_var_decomposition",
    "register",
]
