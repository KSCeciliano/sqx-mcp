"""Pivot points: Classic, Fibonacci, Camarilla, Woodie, DeMark.

Pivot points are price levels derived from the previous bar's H/L/C
(and sometimes O), commonly used by intraday traders as support /
resistance levels. Each "family" derives the levels differently:

- **Classic** (5 levels: PP, S1, S2, R1, R2): the textbook recipe.
- **Fibonacci** (5 levels): replaces classic's distances with 38.2% /
  61.8% / 100% of the previous range.
- **Camarilla** (8 levels: 4 supports + 4 resistances): uses C ± k·range
  for narrow-band intraday trading.
- **Woodie** (5 levels): weights close at 2× when computing PP.
- **DeMark** (3 levels: PP, S1, R1): uses a different recipe depending
  on the close vs open direction.

Tools:

- ``pivots_classic`` / ``pivots_fibonacci`` / ``pivots_camarilla`` /
  ``pivots_woodie`` / ``pivots_demark`` — one tool per family.
- ``pivots_compare_all`` — run all five on the same bar and return them
  side-by-side.

Pure functions over (high, low, close, open). Read-only.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp.tools._numerics import is_finite


class HLCArgs(BaseModel):
    high: float = Field(..., gt=0.0)
    low: float = Field(..., gt=0.0)
    close: float = Field(..., gt=0.0)

    @field_validator("high", "low", "close")
    @classmethod
    def _v_finite(cls, v: float) -> float:
        if not is_finite(v):
            raise ValueError("must be a finite number")
        return v


class OHLCArgs(HLCArgs):
    open: float = Field(..., gt=0.0)

    @field_validator("open")
    @classmethod
    def _v_open_finite(cls, v: float) -> float:
        if not is_finite(v):
            raise ValueError("must be a finite number")
        return v


def _classic(h: float, low: float, c: float) -> dict[str, float]:
    pp = (h + low + c) / 3.0
    r1 = 2 * pp - low
    s1 = 2 * pp - h
    r2 = pp + (h - low)
    s2 = pp - (h - low)
    return {
        "PP": round(pp, 8),
        "R1": round(r1, 8),
        "S1": round(s1, 8),
        "R2": round(r2, 8),
        "S2": round(s2, 8),
    }


def _fibonacci(h: float, low: float, c: float) -> dict[str, float]:
    pp = (h + low + c) / 3.0
    rng = h - low
    return {
        "PP": round(pp, 8),
        "R1": round(pp + 0.382 * rng, 8),
        "R2": round(pp + 0.618 * rng, 8),
        "R3": round(pp + 1.000 * rng, 8),
        "S1": round(pp - 0.382 * rng, 8),
        "S2": round(pp - 0.618 * rng, 8),
        "S3": round(pp - 1.000 * rng, 8),
    }


def _camarilla(h: float, low: float, c: float) -> dict[str, float]:
    rng = h - low
    return {
        "R4": round(c + rng * 1.1 / 2.0, 8),
        "R3": round(c + rng * 1.1 / 4.0, 8),
        "R2": round(c + rng * 1.1 / 6.0, 8),
        "R1": round(c + rng * 1.1 / 12.0, 8),
        "S1": round(c - rng * 1.1 / 12.0, 8),
        "S2": round(c - rng * 1.1 / 6.0, 8),
        "S3": round(c - rng * 1.1 / 4.0, 8),
        "S4": round(c - rng * 1.1 / 2.0, 8),
    }


def _woodie(h: float, low: float, c: float) -> dict[str, float]:
    pp = (h + low + 2 * c) / 4.0
    r1 = 2 * pp - low
    s1 = 2 * pp - h
    r2 = pp + (h - low)
    s2 = pp - (h - low)
    return {
        "PP": round(pp, 8),
        "R1": round(r1, 8),
        "S1": round(s1, 8),
        "R2": round(r2, 8),
        "S2": round(s2, 8),
    }


def _demark(o: float, h: float, low: float, c: float) -> dict[str, float]:
    if c < o:
        x = h + 2 * low + c
    elif c > o:
        x = 2 * h + low + c
    else:
        x = h + low + 2 * c
    pp = x / 4.0
    r1 = x / 2.0 - low
    s1 = x / 2.0 - h
    return {
        "PP": round(pp, 8),
        "R1": round(r1, 8),
        "S1": round(s1, 8),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Classic pivot points: PP = (H+L+C)/3, R1/S1 from PP±(PP-L)/(H-PP), "
            "R2/S2 from PP±range. Five levels total. Most widely used variant."
        )
    )
    async def pivots_classic(args: HLCArgs) -> dict:
        return {"ok": True, **_classic(args.high, args.low, args.close)}

    @mcp.tool(
        description=(
            "Fibonacci pivot points: levels at 38.2% / 61.8% / 100% of the "
            "previous range from PP. Seven levels (PP + 3R + 3S)."
        )
    )
    async def pivots_fibonacci(args: HLCArgs) -> dict:
        return {"ok": True, **_fibonacci(args.high, args.low, args.close)}

    @mcp.tool(
        description=(
            "Camarilla pivot points: eight levels (4R + 4S) clustered near the "
            "previous close, with multipliers 1.1/12, 1.1/6, 1.1/4, 1.1/2. "
            "Designed for intraday range-bound trading."
        )
    )
    async def pivots_camarilla(args: HLCArgs) -> dict:
        return {"ok": True, **_camarilla(args.high, args.low, args.close)}

    @mcp.tool(
        description=(
            "Woodie pivot points: PP = (H+L+2C)/4 — weights close at 2×. "
            "R1/S1/R2/S2 as in classic. Use when the closing price matters "
            "more than the open."
        )
    )
    async def pivots_woodie(args: HLCArgs) -> dict:
        return {"ok": True, **_woodie(args.high, args.low, args.close)}

    @mcp.tool(
        description=(
            "DeMark pivot points: recipe depends on close vs open direction "
            "(bullish / bearish / inside). Returns only PP, R1, S1 — fewer "
            "levels but more directional context. Requires OHLC."
        )
    )
    async def pivots_demark(args: OHLCArgs) -> dict:
        return {
            "ok": True,
            **_demark(args.open, args.high, args.low, args.close),
        }

    @mcp.tool(
        description=(
            "Run all five pivot variants on the same bar (requires OHLC). "
            "Returns Classic / Fibonacci / Camarilla / Woodie / DeMark "
            "side-by-side for comparison."
        )
    )
    async def pivots_compare_all(args: OHLCArgs) -> dict:
        return {
            "ok": True,
            "classic": _classic(args.high, args.low, args.close),
            "fibonacci": _fibonacci(args.high, args.low, args.close),
            "camarilla": _camarilla(args.high, args.low, args.close),
            "woodie": _woodie(args.high, args.low, args.close),
            "demark": _demark(args.open, args.high, args.low, args.close),
        }


__all__ = [
    "HLCArgs",
    "OHLCArgs",
    "_camarilla",
    "_classic",
    "_demark",
    "_fibonacci",
    "_woodie",
    "register",
]
