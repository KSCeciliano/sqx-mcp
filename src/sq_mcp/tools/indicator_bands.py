"""Classic indicator bands: Bollinger, Keltner, Donchian, plus ATR-based stops.

Most price-action strategies are built on top of envelope indicators
that define dynamic support/resistance levels. This module gives
explicit-input implementations so the agent can compute them without
the engine.

Tools:

- ``indicator_bollinger`` — SMA ± N × stddev. Classic mean-reversion
  envelope.
- ``indicator_keltner`` — EMA ± N × ATR. Trend-following envelope that
  uses true range instead of close-stddev.
- ``indicator_donchian`` — highest high / lowest low over N bars.
  Used in Turtle-style trend systems.
- ``indicator_atr`` — Wilder's Average True Range.
- ``indicator_atr_stop`` — ATR-based trailing stop level given an
  entry price, direction, and ATR multiple.
- ``indicator_chandelier_stop`` — Chandelier exit: max(high, N) − k·ATR
  for longs, min(low, N) + k·ATR for shorts.

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


class CloseSeriesArgs(BaseModel):
    closes: list[float] = Field(..., min_length=2, max_length=500_000)
    window: int = Field(20, ge=2, le=500)
    n_stddev: float = Field(2.0, gt=0.0, le=10.0)


class HLCSeriesArgs(BaseModel):
    highs: list[float] = Field(..., min_length=2, max_length=500_000)
    lows: list[float] = Field(..., min_length=2, max_length=500_000)
    closes: list[float] = Field(..., min_length=2, max_length=500_000)
    window: int = Field(20, ge=2, le=500)
    n_atr: float = Field(2.0, gt=0.0, le=10.0)


class HLSeriesArgs(BaseModel):
    highs: list[float] = Field(..., min_length=2, max_length=500_000)
    lows: list[float] = Field(..., min_length=2, max_length=500_000)
    window: int = Field(20, ge=2, le=500)


class ATRArgs(BaseModel):
    highs: list[float] = Field(..., min_length=2, max_length=500_000)
    lows: list[float] = Field(..., min_length=2, max_length=500_000)
    closes: list[float] = Field(..., min_length=2, max_length=500_000)
    window: int = Field(14, ge=2, le=500)


class ATRStopArgs(BaseModel):
    entry_price: float = Field(..., gt=0.0)
    current_atr: float = Field(..., gt=0.0)
    side: Literal["long", "short"]
    n_atr: float = Field(2.0, gt=0.0, le=20.0)


class ChandelierArgs(BaseModel):
    highs: list[float] = Field(..., min_length=2, max_length=500_000)
    lows: list[float] = Field(..., min_length=2, max_length=500_000)
    closes: list[float] = Field(..., min_length=2, max_length=500_000)
    window: int = Field(22, ge=2, le=500)
    n_atr: float = Field(3.0, gt=0.0, le=20.0)


def _ema(xs: list[float], span: int) -> list[float | None]:
    """Exponential moving average with EMA_t = α·x_t + (1−α)·EMA_{t−1}."""
    if not xs:
        return []
    alpha = 2.0 / (span + 1)
    out: list[float | None] = [None] * (span - 1)
    if len(xs) < span:
        return [None] * len(xs)
    # Seed with SMA of the first `span` values
    sma_seed = sum(xs[:span]) / span
    out.append(sma_seed)
    for i in range(span, len(xs)):
        prev: float = out[-1] if out[-1] is not None else sma_seed  # type: ignore[assignment]
        out.append(alpha * xs[i] + (1 - alpha) * prev)
    return out


def _atr(highs: list[float], lows: list[float], closes: list[float], window: int) -> list[float | None]:
    """Wilder's smoothed ATR."""
    validate_finite_floats(highs, name="highs")
    validate_finite_floats(lows, name="lows")
    validate_finite_floats(closes, name="closes")
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return [None] * n
    trs: list[float] = []
    for i in range(n):
        if i == 0:
            tr = highs[0] - lows[0]
        else:
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        trs.append(tr)
    # Wilder smoothing: ATR_t = ((window-1)·ATR_{t-1} + TR_t) / window
    out: list[float | None] = [None] * (window - 1)
    if n < window:
        return [None] * n
    atr_seed = sum(trs[:window]) / window
    out.append(atr_seed)
    for i in range(window, n):
        prev: float = out[-1] if out[-1] is not None else atr_seed  # type: ignore[assignment]
        out.append(((window - 1) * prev + trs[i]) / window)
    return out


def _bollinger(closes: list[float], window: int, n_std: float) -> dict[str, Any]:
    validate_finite_floats(closes, name="closes")
    n = len(closes)
    upper: list[float | None] = [None] * (window - 1)
    middle: list[float | None] = [None] * (window - 1)
    lower: list[float | None] = [None] * (window - 1)
    for t in range(window - 1, n):
        win = closes[t - window + 1 : t + 1]
        mean = sum(win) / window
        var = sum((x - mean) ** 2 for x in win) / window
        std = math.sqrt(var)
        middle.append(round(mean, 8))
        upper.append(round(mean + n_std * std, 8))
        lower.append(round(mean - n_std * std, 8))
    return {
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "window": window,
        "n_stddev": n_std,
        "current": {
            "middle": middle[-1],
            "upper": upper[-1],
            "lower": lower[-1],
            "close": closes[-1],
            "position_in_band": (
                round((closes[-1] - lower[-1]) / (upper[-1] - lower[-1]), 4)
                if upper[-1] is not None and lower[-1] is not None and upper[-1] != lower[-1]
                else None
            ),
        },
    }


def _keltner(
    highs: list[float], lows: list[float], closes: list[float],
    window: int, n_atr: float,
) -> dict[str, Any]:
    ema = _ema(closes, window)
    atr = _atr(highs, lows, closes, window)
    n = min(len(ema), len(atr))
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i in range(n):
        if ema[i] is None or atr[i] is None:
            upper.append(None)
            lower.append(None)
        else:
            upper.append(round(ema[i] + n_atr * atr[i], 8))
            lower.append(round(ema[i] - n_atr * atr[i], 8))
    return {
        "middle": [round(x, 8) if x is not None else None for x in ema],
        "upper": upper,
        "lower": lower,
        "atr": [round(a, 8) if a is not None else None for a in atr],
        "window": window,
        "n_atr": n_atr,
        "current": {
            "middle": ema[-1],
            "upper": upper[-1],
            "lower": lower[-1],
            "close": closes[-1] if closes else None,
        },
    }


def _donchian(highs: list[float], lows: list[float], window: int) -> dict[str, Any]:
    validate_finite_floats(highs, name="highs")
    validate_finite_floats(lows, name="lows")
    n = min(len(highs), len(lows))
    upper: list[float | None] = [None] * (window - 1)
    lower: list[float | None] = [None] * (window - 1)
    for t in range(window - 1, n):
        upper.append(round(max(highs[t - window + 1 : t + 1]), 8))
        lower.append(round(min(lows[t - window + 1 : t + 1]), 8))
    middle = [
        round((u + low) / 2, 8) if u is not None and low is not None else None
        for u, low in zip(upper, lower, strict=False)
    ]
    return {
        "upper": upper,
        "lower": lower,
        "middle": middle,
        "window": window,
        "current": {
            "upper": upper[-1],
            "lower": lower[-1],
            "middle": middle[-1],
        },
    }


def _atr_stop(entry: float, atr: float, side: str, n: float) -> dict[str, Any]:
    distance = n * atr
    if side == "long":
        stop = entry - distance
    else:
        stop = entry + distance
    return {
        "stop_price": round(stop, 8),
        "distance_abs": round(distance, 8),
        "distance_pct": round(distance / entry * 100.0, 4) if entry > 0 else None,
        "side": side,
        "n_atr": n,
        "atr": atr,
        "entry": entry,
    }


def _chandelier(
    highs: list[float], lows: list[float], closes: list[float],
    window: int, n_atr: float,
) -> dict[str, Any]:
    atr = _atr(highs, lows, closes, window)
    n = min(len(highs), len(lows), len(closes), len(atr))
    long_stop: list[float | None] = []
    short_stop: list[float | None] = []
    for t in range(n):
        if t < window - 1 or atr[t] is None:
            long_stop.append(None)
            short_stop.append(None)
            continue
        hh = max(highs[t - window + 1 : t + 1])
        ll = min(lows[t - window + 1 : t + 1])
        long_stop.append(round(hh - n_atr * atr[t], 8))
        short_stop.append(round(ll + n_atr * atr[t], 8))
    return {
        "long_stop": long_stop,
        "short_stop": short_stop,
        "atr": [round(a, 8) if a is not None else None for a in atr],
        "window": window,
        "n_atr": n_atr,
        "current": {
            "long_stop": long_stop[-1],
            "short_stop": short_stop[-1],
        },
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Bollinger bands: SMA ± N · stddev over a rolling window. "
            "Returns middle/upper/lower series + a current snapshot with "
            "position_in_band ∈ [0, 1] (0 = at lower band, 1 = at upper "
            "band). Classic mean-reversion envelope."
        )
    )
    async def indicator_bollinger(args: CloseSeriesArgs) -> dict:
        try:
            return {"ok": True, **_bollinger(args.closes, args.window, args.n_stddev)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Keltner channel: EMA ± N · ATR. Trend-following envelope that "
            "uses true range instead of close-stddev. Reacts faster than "
            "Bollinger when volatility expands."
        )
    )
    async def indicator_keltner(args: HLCSeriesArgs) -> dict:
        try:
            return {
                "ok": True,
                **_keltner(args.highs, args.lows, args.closes, args.window, args.n_atr),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Donchian channel: rolling N-bar highest high and lowest low. "
            "Used in classic Turtle trend systems — breakouts of the upper "
            "channel signal long entry."
        )
    )
    async def indicator_donchian(args: HLSeriesArgs) -> dict:
        try:
            return {"ok": True, **_donchian(args.highs, args.lows, args.window)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Wilder's Average True Range (ATR) with the canonical "
            "smoothing recursion: ATR_t = ((window-1)·ATR_{t-1} + TR_t) / "
            "window. Foundation for ATR-based stops and Keltner bands."
        )
    )
    async def indicator_atr(args: ATRArgs) -> dict:
        try:
            atr = _atr(args.highs, args.lows, args.closes, args.window)
            return {
                "ok": True,
                "atr": [round(a, 8) if a is not None else None for a in atr],
                "current": atr[-1] if atr else None,
                "window": args.window,
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "ATR-based stop level for a single trade: stop = entry ± "
            "n_atr · ATR (minus for long, plus for short). Returns the "
            "stop price + distance in absolute and percent terms."
        )
    )
    async def indicator_atr_stop(args: ATRStopArgs) -> dict:
        return {
            "ok": True,
            **_atr_stop(args.entry_price, args.current_atr, args.side, args.n_atr),
        }

    @mcp.tool(
        description=(
            "Chandelier exit: trailing-stop variant. Long stop = "
            "max_high_N − k·ATR; short stop = min_low_N + k·ATR. Standard "
            "k = 3, window = 22. Less whipsaw than fixed-distance ATR stop."
        )
    )
    async def indicator_chandelier_stop(args: ChandelierArgs) -> dict:
        try:
            return {
                "ok": True,
                **_chandelier(
                    args.highs, args.lows, args.closes, args.window, args.n_atr,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "ATRArgs",
    "ATRStopArgs",
    "ChandelierArgs",
    "CloseSeriesArgs",
    "HLCSeriesArgs",
    "HLSeriesArgs",
    "_atr",
    "_atr_stop",
    "_bollinger",
    "_chandelier",
    "_donchian",
    "_ema",
    "_keltner",
    "register",
]
