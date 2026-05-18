"""Performance attribution — split portfolio return by source.

Given a portfolio whose return came from several strategies, this module
breaks down where that return came from: by strategy, by month, by
regime (calm vs volatile months). The math is intentionally simple and
defensive — these are pre-flight checks before showing a stakeholder
where the money came from.

Tools:

- ``attribution_by_strategy`` — given a list of (strategy_name, weight,
  total_return_pct) tuples, compute each strategy's *contribution* to
  the portfolio total return = weight × return. Report sum of
  contributions plus per-strategy %-of-total.
- ``attribution_by_month_from_curves`` — given equity sparklines for N
  strategies, segment each curve into M equal-length "month" buckets,
  estimate per-bucket returns, and report which months were strongest
  for each strategy.
- ``attribution_regime_split`` — divide months into "calm" and
  "volatile" based on a return-std threshold; report each strategy's
  return in each regime. Use to spot strategies that only work in one
  regime.
- ``attribution_concentration_index`` — Herfindahl-style concentration
  of return contributions. 100% means a single strategy did all the
  work; near 0% means perfectly distributed.

All math is pure Python and read-only.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# ---- argument schemas ------------------------------------------------------


class StrategyContribution(BaseModel):
    name: str
    weight: float = Field(..., gt=0)
    total_return_pct: float


class AttributionByStrategyArgs(BaseModel):
    strategies: list[StrategyContribution] = Field(..., min_length=1, max_length=200)


class StrategyCurve(BaseModel):
    name: str
    equity_curve: list[float] = Field(..., min_length=10, max_length=10000)
    weight: float = Field(1.0, gt=0)


class AttributionByMonthArgs(BaseModel):
    strategies: list[StrategyCurve] = Field(..., min_length=1, max_length=50)
    n_buckets: int = Field(12, ge=2, le=120)


class AttributionRegimeArgs(BaseModel):
    strategies: list[StrategyCurve] = Field(..., min_length=1, max_length=50)
    n_buckets: int = Field(12, ge=2, le=120)
    volatility_threshold_std: float = Field(0.02, gt=0)


class AttributionHHIArgs(BaseModel):
    contributions: list[float] = Field(..., min_length=2, max_length=500)


# ---- helpers ---------------------------------------------------------------


def _bucket_returns(curve: list[float], n_buckets: int) -> list[float]:
    """Split the curve into n_buckets equal-length segments and return per-bucket %.

    Each bucket return = (last - first) / first × 100.
    """
    if len(curve) < n_buckets or n_buckets < 1:
        return []
    chunk_size = len(curve) // n_buckets
    out: list[float] = []
    for b in range(n_buckets):
        lo = b * chunk_size
        hi = (b + 1) * chunk_size if b < n_buckets - 1 else len(curve)
        sub = curve[lo:hi]
        if not sub or sub[0] == 0:
            out.append(0.0)
            continue
        ret = (sub[-1] - sub[0]) / abs(sub[0]) * 100.0
        out.append(ret)
    return out


def _attribute_by_strategy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Each strategy's contribution = weight × return. Returns contributions + %-of-total."""
    contribs = []
    total = 0.0
    for r in rows:
        c = float(r["weight"]) * float(r["total_return_pct"])
        contribs.append({"name": r["name"], "weight": r["weight"], "return_pct": r["total_return_pct"], "contribution_pct": round(c, 4)})
        total += c
    if total == 0:
        for c in contribs:
            c["share_of_total_pct"] = 0.0
    else:
        for c in contribs:
            c["share_of_total_pct"] = round(c["contribution_pct"] / total * 100.0, 4)
    contribs.sort(key=lambda x: abs(x["contribution_pct"]), reverse=True)
    return {
        "total_portfolio_return_pct": round(total, 4),
        "contributions": contribs,
        "n_strategies": len(rows),
    }


def _attribute_by_month(rows: list[dict[str, Any]], n_buckets: int) -> dict[str, Any]:
    per_strategy = []
    bucket_totals = [0.0] * n_buckets
    for r in rows:
        bucket_returns = _bucket_returns(r["equity_curve"], n_buckets)
        weight = r["weight"]
        strat_row = {
            "name": r["name"],
            "weight": weight,
            "bucket_returns_pct": [round(b, 4) for b in bucket_returns],
            "best_bucket": (
                int(max(range(len(bucket_returns)), key=lambda i: bucket_returns[i]))
                if bucket_returns
                else None
            ),
            "worst_bucket": (
                int(min(range(len(bucket_returns)), key=lambda i: bucket_returns[i]))
                if bucket_returns
                else None
            ),
        }
        per_strategy.append(strat_row)
        for i, br in enumerate(bucket_returns):
            bucket_totals[i] += br * weight

    # Identify portfolio-level best/worst buckets
    best_idx = max(range(n_buckets), key=lambda i: bucket_totals[i])
    worst_idx = min(range(n_buckets), key=lambda i: bucket_totals[i])
    return {
        "n_buckets": n_buckets,
        "per_strategy": per_strategy,
        "portfolio_bucket_returns_pct": [round(b, 4) for b in bucket_totals],
        "portfolio_best_bucket_idx": best_idx,
        "portfolio_worst_bucket_idx": worst_idx,
    }


def _classify_regime(bucket_returns: list[float], threshold: float) -> list[str]:
    """Per-bucket regime label: 'volatile' if |return| > threshold, else 'calm'.

    Note: threshold here is in *percentage-point* terms (caller passes
    standard deviation × 100 effectively, or just a numeric threshold).
    """
    return ["volatile" if abs(b) > threshold else "calm" for b in bucket_returns]


def _attribute_regime(rows: list[dict[str, Any]], n_buckets: int, threshold: float) -> dict[str, Any]:
    # First compute portfolio buckets to classify regimes
    bucket_totals = [0.0] * n_buckets
    per_curve_buckets: list[list[float]] = []
    for r in rows:
        br = _bucket_returns(r["equity_curve"], n_buckets)
        per_curve_buckets.append(br)
        for i, v in enumerate(br):
            bucket_totals[i] += v * r["weight"]

    regimes = _classify_regime(bucket_totals, threshold * 100.0)
    n_calm = regimes.count("calm")
    n_vol = regimes.count("volatile")

    per_strategy = []
    for r, br in zip(rows, per_curve_buckets, strict=True):
        calm_returns = [v for v, reg in zip(br, regimes, strict=True) if reg == "calm"]
        vol_returns = [v for v, reg in zip(br, regimes, strict=True) if reg == "volatile"]
        per_strategy.append(
            {
                "name": r["name"],
                "calm_return_mean_pct": (
                    round(sum(calm_returns) / len(calm_returns), 4) if calm_returns else None
                ),
                "volatile_return_mean_pct": (
                    round(sum(vol_returns) / len(vol_returns), 4) if vol_returns else None
                ),
                "calm_total_pct": round(sum(calm_returns), 4) if calm_returns else 0.0,
                "volatile_total_pct": round(sum(vol_returns), 4) if vol_returns else 0.0,
            }
        )

    return {
        "n_buckets": n_buckets,
        "threshold_pct": round(threshold * 100.0, 4),
        "regime_per_bucket": regimes,
        "n_calm_buckets": n_calm,
        "n_volatile_buckets": n_vol,
        "per_strategy": per_strategy,
    }


def _herfindahl_contribution(contributions: list[float]) -> dict[str, Any]:
    """HHI on |contribution| as fraction of total |contribution|."""
    abs_contribs = [abs(c) for c in contributions]
    total = sum(abs_contribs)
    if total == 0:
        return {"hhi": 0.0, "hhi_pct": 0.0, "verdict": "no return at all"}
    shares = [c / total for c in abs_contribs]
    raw_hhi = sum(s * s for s in shares)
    # Normalize for n: ranges from 1/n (perfectly distributed) to 1 (single source)
    n = len(shares)
    norm_hhi = (raw_hhi - 1.0 / n) / (1.0 - 1.0 / n) if n > 1 else 0.0
    pct = norm_hhi * 100.0
    verdict = (
        "highly concentrated (single strategy dominates)"
        if pct > 70
        else "moderately concentrated"
        if pct > 30
        else "well distributed"
    )
    return {
        "hhi_raw": round(raw_hhi, 6),
        "hhi_normalized": round(norm_hhi, 6),
        "hhi_pct": round(pct, 2),
        "top_share_pct": round(max(shares) * 100.0, 2),
        "verdict": verdict,
        "n_sources": n,
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Attribute total portfolio return to each strategy: contribution = "
            "weight × return. Returns per-strategy contributions, share of total, "
            "and the portfolio total. Use as a starting point for any breakdown "
            "report. Pure math."
        )
    )
    async def attribution_by_strategy(args: AttributionByStrategyArgs) -> dict:
        rows = [s.model_dump() for s in args.strategies]
        return {"ok": True, **_attribute_by_strategy(rows)}

    @mcp.tool(
        description=(
            "Bucket each strategy's equity curve into n_buckets equal-length "
            "segments (typically 'months') and report per-bucket returns. Also "
            "computes weighted portfolio bucket returns and identifies best/"
            "worst buckets. Pure math."
        )
    )
    async def attribution_by_month_from_curves(args: AttributionByMonthArgs) -> dict:
        rows = [s.model_dump() for s in args.strategies]
        return {"ok": True, **_attribute_by_month(rows, args.n_buckets)}

    @mcp.tool(
        description=(
            "Split buckets into calm vs volatile (|portfolio return| > "
            "volatility_threshold_std). Report each strategy's mean and total "
            "return in each regime. Use to spot strategies that only work in "
            "one regime."
        )
    )
    async def attribution_regime_split(args: AttributionRegimeArgs) -> dict:
        rows = [s.model_dump() for s in args.strategies]
        return {
            "ok": True,
            **_attribute_regime(rows, args.n_buckets, args.volatility_threshold_std),
        }

    @mcp.tool(
        description=(
            "Herfindahl-Hirschman index on |contribution|. 100 = single strategy "
            "did all the work; 0 = perfectly distributed across sources. Returns "
            "raw, normalized, percentage, and a plain-English verdict."
        )
    )
    async def attribution_concentration_index(args: AttributionHHIArgs) -> dict:
        return {"ok": True, **_herfindahl_contribution(args.contributions)}
