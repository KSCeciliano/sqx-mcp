"""VWAP / TWAP execution metrics and slippage estimation.

Volume-Weighted Average Price (VWAP) and Time-Weighted Average Price
(TWAP) are benchmark execution prices. A trade's slippage vs VWAP
quantifies how well the entry/exit was timed.

Tools:

- ``vwap_compute`` — VWAP from (price, volume) tuples.
- ``twap_compute`` — TWAP from (price, time-weight) tuples.
- ``vwap_slippage`` — given a trade fill (price + size) and a benchmark
  VWAP, report the slippage in absolute and bps terms.
- ``vwap_participation_rate`` — given the trader's volume and total
  market volume, compute participation %. >10% suggests market impact.
- ``vwap_per_session`` — split a tick stream into named sessions
  (asia/europe/us) and compute VWAP per session.

Pure Python.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)


class VwapArgs(BaseModel):
    prices: list[float] = Field(..., min_length=1, max_length=500_000)
    volumes: list[float] = Field(..., min_length=1, max_length=500_000)


class TwapArgs(BaseModel):
    prices: list[float] = Field(..., min_length=1, max_length=500_000)
    weights: list[float] = Field(..., min_length=1, max_length=500_000)


class SlippageArgs(BaseModel):
    fill_price: float = Field(..., gt=0.0)
    benchmark_vwap: float = Field(..., gt=0.0)
    side: Literal["buy", "sell"] = "buy"
    fill_size: float = Field(1.0, gt=0.0)


class ParticipationArgs(BaseModel):
    own_volume: float = Field(..., ge=0.0)
    market_volume: float = Field(..., gt=0.0)


class VwapPerSessionArgs(BaseModel):
    timestamps_hour: list[int] = Field(..., min_length=2, max_length=500_000)
    prices: list[float] = Field(..., min_length=2, max_length=500_000)
    volumes: list[float] = Field(..., min_length=2, max_length=500_000)
    sessions: dict[str, list[int]] = Field(
        ..., description="Map of session name → list of hours [start, end_excl]"
    )


def _vwap(prices: list[float], volumes: list[float]) -> dict[str, Any]:
    validate_finite_floats(prices, name="prices")
    validate_finite_floats(volumes, name="volumes")
    n = min(len(prices), len(volumes))
    total_volume = 0.0
    total_pv = 0.0
    for i in range(n):
        if volumes[i] < 0:
            continue
        total_pv += prices[i] * volumes[i]
        total_volume += volumes[i]
    if total_volume <= 0:
        return {"vwap": None, "note": "zero total volume"}
    return {
        "vwap": round(total_pv / total_volume, 8),
        "total_volume": round(total_volume, 8),
        "total_dollar_volume": round(total_pv, 6),
        "n_samples": n,
    }


def _twap(prices: list[float], weights: list[float]) -> dict[str, Any]:
    validate_finite_floats(prices, name="prices")
    validate_finite_floats(weights, name="weights")
    n = min(len(prices), len(weights))
    total_w = sum(max(0.0, w) for w in weights[:n])
    if total_w <= 0:
        return {"twap": None, "note": "zero total weight"}
    total_pw = sum(prices[i] * max(0.0, weights[i]) for i in range(n))
    return {
        "twap": round(total_pw / total_w, 8),
        "total_weight": round(total_w, 8),
        "n_samples": n,
    }


def _slippage(
    fill: float, benchmark: float, side: str, size: float
) -> dict[str, Any]:
    if side == "buy":
        slippage_abs = fill - benchmark
    else:
        slippage_abs = benchmark - fill
    slippage_bps = (slippage_abs / benchmark) * 10_000.0 if benchmark > 0 else None
    return {
        "fill_price": fill,
        "benchmark_vwap": benchmark,
        "side": side,
        "fill_size": size,
        "slippage_abs": round(slippage_abs, 8),
        "slippage_bps": round(slippage_bps, 4) if slippage_bps is not None else None,
        "slippage_cost": round(slippage_abs * size, 6),
        "verdict": (
            "excellent" if slippage_bps is not None and slippage_bps < -5
            else "good" if slippage_bps is not None and slippage_bps < 0
            else "acceptable" if slippage_bps is not None and slippage_bps < 10
            else "poor"
        ),
    }


def _participation(own: float, market: float) -> dict[str, Any]:
    if market <= 0:
        return {"participation_pct": None}
    pct = 100.0 * own / market
    return {
        "participation_pct": round(pct, 4),
        "own_volume": own,
        "market_volume": market,
        "verdict": (
            "negligible_impact" if pct < 1.0
            else "low_impact" if pct < 5.0
            else "moderate_impact" if pct < 10.0
            else "high_impact" if pct < 25.0
            else "dominant_player"
        ),
    }


def _hour_in_range(h: int, lo: int, hi: int) -> bool:
    if lo == hi:
        return False
    if lo < hi:
        return lo <= h < hi
    return h >= lo or h < hi


def _vwap_per_session(
    hours: list[int],
    prices: list[float],
    volumes: list[float],
    sessions: dict[str, list[int]],
) -> dict[str, Any]:
    validate_finite_floats(prices, name="prices")
    validate_finite_floats(volumes, name="volumes")
    n = min(len(hours), len(prices), len(volumes))
    out: dict[str, dict[str, Any]] = {}
    for name, span in sessions.items():
        if len(span) < 2:
            continue
        lo, hi = int(span[0]), int(span[1])
        bucket_p: list[float] = []
        bucket_v: list[float] = []
        for i in range(n):
            if _hour_in_range(hours[i], lo, hi):
                bucket_p.append(prices[i])
                bucket_v.append(volumes[i])
        if not bucket_p:
            out[name] = {"vwap": None, "n_samples": 0}
        else:
            r = _vwap(bucket_p, bucket_v)
            out[name] = r
    return {"by_session": out, "n_sessions": len(out)}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Volume-Weighted Average Price from parallel (prices, volumes) "
            "arrays. Σ(p·v) / Σv. Returns None if total volume is zero."
        )
    )
    async def vwap_compute(args: VwapArgs) -> dict:
        try:
            return {"ok": True, **_vwap(args.prices, args.volumes)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Time-Weighted Average Price: weighted mean of prices with "
            "caller-supplied weights (typically time-spans between samples). "
            "Use when volume is unavailable or not relevant."
        )
    )
    async def twap_compute(args: TwapArgs) -> dict:
        try:
            return {"ok": True, **_twap(args.prices, args.weights)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Slippage of a fill price against a benchmark VWAP. Buy slippage "
            "= fill − benchmark (positive = paid more); sell slippage = "
            "benchmark − fill (positive = received more). Reports bps + "
            "verdict (excellent / good / acceptable / poor)."
        )
    )
    async def vwap_slippage(args: SlippageArgs) -> dict:
        return {
            "ok": True,
            **_slippage(args.fill_price, args.benchmark_vwap, args.side, args.fill_size),
        }

    @mcp.tool(
        description=(
            "Participation rate = own_volume / market_volume × 100. Verdict: "
            "negligible_impact (<1%), low_impact (1-5%), moderate_impact "
            "(5-10%), high_impact (10-25%), dominant_player (>25%). High "
            "participation invites market impact."
        )
    )
    async def vwap_participation_rate(args: ParticipationArgs) -> dict:
        return {"ok": True, **_participation(args.own_volume, args.market_volume)}

    @mcp.tool(
        description=(
            "Split a tick stream by session (asia / europe / us, or custom) "
            "given parallel hour arrays, and compute VWAP per session. "
            "Sessions wrapping midnight (e.g. asia 22-08) are supported."
        )
    )
    async def vwap_per_session(args: VwapPerSessionArgs) -> dict:
        try:
            return {
                "ok": True,
                **_vwap_per_session(
                    args.timestamps_hour, args.prices, args.volumes, args.sessions,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "ParticipationArgs",
    "SlippageArgs",
    "TwapArgs",
    "VwapArgs",
    "VwapPerSessionArgs",
    "_participation",
    "_slippage",
    "_twap",
    "_vwap",
    "_vwap_per_session",
    "register",
]
