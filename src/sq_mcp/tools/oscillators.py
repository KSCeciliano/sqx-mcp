"""Classic momentum oscillators: RSI, MACD, Stochastic, CCI, Williams %R.

Companion to ``indicator_bands``. Oscillators are bounded indicators
that quantify momentum or overbought/oversold conditions, in contrast
to bands which give dynamic price levels.

Tools:

- ``oscillator_rsi`` — Wilder's Relative Strength Index (default 14).
- ``oscillator_macd`` — Moving Average Convergence Divergence (12/26/9).
- ``oscillator_stochastic`` — %K and %D Stochastic oscillator (14, 3).
- ``oscillator_cci`` — Commodity Channel Index (default 20).
- ``oscillator_williams_r`` — Williams %R (default 14).

Pure Python.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)
from sq_mcp.tools.indicator_bands import _ema


class RSIArgs(BaseModel):
    closes: list[float] = Field(..., min_length=2, max_length=500_000)
    window: int = Field(14, ge=2, le=500)


class MACDArgs(BaseModel):
    closes: list[float] = Field(..., min_length=2, max_length=500_000)
    fast_period: int = Field(12, ge=2, le=200)
    slow_period: int = Field(26, ge=2, le=500)
    signal_period: int = Field(9, ge=2, le=200)


class StochasticArgs(BaseModel):
    highs: list[float] = Field(..., min_length=2, max_length=500_000)
    lows: list[float] = Field(..., min_length=2, max_length=500_000)
    closes: list[float] = Field(..., min_length=2, max_length=500_000)
    k_period: int = Field(14, ge=2, le=500)
    d_period: int = Field(3, ge=2, le=200)


class CCIArgs(BaseModel):
    highs: list[float] = Field(..., min_length=2, max_length=500_000)
    lows: list[float] = Field(..., min_length=2, max_length=500_000)
    closes: list[float] = Field(..., min_length=2, max_length=500_000)
    window: int = Field(20, ge=2, le=500)


class WilliamsRArgs(BaseModel):
    highs: list[float] = Field(..., min_length=2, max_length=500_000)
    lows: list[float] = Field(..., min_length=2, max_length=500_000)
    closes: list[float] = Field(..., min_length=2, max_length=500_000)
    window: int = Field(14, ge=2, le=500)


def _rsi(closes: list[float], window: int) -> dict[str, Any]:
    validate_finite_floats(closes, name="closes")
    n = len(closes)
    if n < window + 1:
        return {"rsi": [None] * n, "current": None}
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    out: list[float | None] = [None] * window
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    if avg_loss == 0:
        out.append(100.0)
    else:
        rs = avg_gain / avg_loss
        out.append(round(100.0 - 100.0 / (1.0 + rs), 4))
    for i in range(window, n - 1):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(round(100.0 - 100.0 / (1.0 + rs), 4))
    current = out[-1]
    return {
        "rsi": out,
        "current": current,
        "window": window,
        "signal": (
            "overbought" if current is not None and current > 70
            else "oversold" if current is not None and current < 30
            else "neutral"
        ),
    }


def _macd(
    closes: list[float], fast: int, slow: int, signal: int
) -> dict[str, Any]:
    validate_finite_floats(closes, name="closes")
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    n = len(closes)
    macd: list[float | None] = []
    for i in range(n):
        if ema_fast[i] is None or ema_slow[i] is None:
            macd.append(None)
        else:
            macd.append(ema_fast[i] - ema_slow[i])
    valid_macd = [m for m in macd if m is not None]
    signal_line = _ema(valid_macd, signal) if len(valid_macd) >= signal else [None] * len(valid_macd)
    # Pad signal back to match macd length
    pad = len(macd) - len(signal_line)
    signal_full: list[float | None] = [None] * pad + list(signal_line)
    histogram = [
        macd[i] - signal_full[i]
        if macd[i] is not None and signal_full[i] is not None
        else None
        for i in range(n)
    ]
    return {
        "macd": [round(m, 6) if m is not None else None for m in macd],
        "signal": [round(s, 6) if s is not None else None for s in signal_full],
        "histogram": [round(h, 6) if h is not None else None for h in histogram],
        "current_macd": round(macd[-1], 6) if macd[-1] is not None else None,
        "current_signal": (
            round(signal_full[-1], 6) if signal_full[-1] is not None else None
        ),
        "current_histogram": (
            round(histogram[-1], 6) if histogram[-1] is not None else None
        ),
        "fast_period": fast,
        "slow_period": slow,
        "signal_period": signal,
    }


def _stochastic(
    highs: list[float], lows: list[float], closes: list[float],
    k_period: int, d_period: int,
) -> dict[str, Any]:
    validate_finite_floats(highs, name="highs")
    validate_finite_floats(lows, name="lows")
    validate_finite_floats(closes, name="closes")
    n = min(len(highs), len(lows), len(closes))
    k_values: list[float | None] = [None] * (k_period - 1)
    for t in range(k_period - 1, n):
        hh = max(highs[t - k_period + 1 : t + 1])
        ll = min(lows[t - k_period + 1 : t + 1])
        if hh == ll:
            k_values.append(50.0)
        else:
            k_values.append(round(100.0 * (closes[t] - ll) / (hh - ll), 4))
    valid_k = [k for k in k_values if k is not None]
    d_raw = []
    for t in range(d_period - 1, len(valid_k)):
        window = valid_k[t - d_period + 1 : t + 1]
        d_raw.append(round(sum(window) / d_period, 4))
    pad = len(k_values) - len(d_raw)
    d_values: list[float | None] = [None] * pad + list(d_raw)
    current_k = k_values[-1]
    return {
        "k": k_values,
        "d": d_values,
        "current_k": current_k,
        "current_d": d_values[-1] if d_values and d_values[-1] is not None else None,
        "signal": (
            "overbought" if current_k is not None and current_k > 80
            else "oversold" if current_k is not None and current_k < 20
            else "neutral"
        ),
        "k_period": k_period,
        "d_period": d_period,
    }


def _cci(
    highs: list[float], lows: list[float], closes: list[float], window: int
) -> dict[str, Any]:
    validate_finite_floats(highs, name="highs")
    validate_finite_floats(lows, name="lows")
    validate_finite_floats(closes, name="closes")
    n = min(len(highs), len(lows), len(closes))
    typical = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
    out: list[float | None] = [None] * (window - 1)
    for t in range(window - 1, n):
        win = typical[t - window + 1 : t + 1]
        mean = sum(win) / window
        mad = sum(abs(x - mean) for x in win) / window
        if mad == 0:
            out.append(0.0)
        else:
            out.append(round((typical[t] - mean) / (0.015 * mad), 4))
    current = out[-1]
    return {
        "cci": out,
        "current": current,
        "window": window,
        "signal": (
            "strong_overbought" if current is not None and current > 200
            else "overbought" if current is not None and current > 100
            else "oversold" if current is not None and current < -100
            else "strong_oversold" if current is not None and current < -200
            else "neutral"
        ),
    }


def _williams_r(
    highs: list[float], lows: list[float], closes: list[float], window: int
) -> dict[str, Any]:
    validate_finite_floats(highs, name="highs")
    validate_finite_floats(lows, name="lows")
    validate_finite_floats(closes, name="closes")
    n = min(len(highs), len(lows), len(closes))
    out: list[float | None] = [None] * (window - 1)
    for t in range(window - 1, n):
        hh = max(highs[t - window + 1 : t + 1])
        ll = min(lows[t - window + 1 : t + 1])
        if hh == ll:
            out.append(-50.0)
        else:
            out.append(round(-100.0 * (hh - closes[t]) / (hh - ll), 4))
    current = out[-1]
    return {
        "williams_r": out,
        "current": current,
        "window": window,
        "signal": (
            "overbought" if current is not None and current > -20
            else "oversold" if current is not None and current < -80
            else "neutral"
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Wilder's Relative Strength Index. Bounded [0, 100]; >70 = "
            "overbought, <30 = oversold. Uses smoothed average gain / loss "
            "recursion."
        )
    )
    async def oscillator_rsi(args: RSIArgs) -> dict:
        try:
            return {"ok": True, **_rsi(args.closes, args.window)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "MACD = EMA(fast) − EMA(slow), with EMA(signal_period) signal "
            "line and (MACD − signal) histogram. Default 12/26/9 "
            "configuration. Returns the three series + current values."
        )
    )
    async def oscillator_macd(args: MACDArgs) -> dict:
        try:
            return {
                "ok": True,
                **_macd(args.closes, args.fast_period, args.slow_period, args.signal_period),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Stochastic oscillator: %K = (close − low_N) / (high_N − low_N) "
            "× 100, %D = SMA(%K, d_period). >80 = overbought, <20 = "
            "oversold. Returns both series + current values."
        )
    )
    async def oscillator_stochastic(args: StochasticArgs) -> dict:
        try:
            return {
                "ok": True,
                **_stochastic(
                    args.highs, args.lows, args.closes, args.k_period, args.d_period,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Commodity Channel Index: (typical_price − SMA(typical, N)) / "
            "(0.015 × mean absolute deviation). Unbounded; >100 = "
            "overbought, <−100 = oversold."
        )
    )
    async def oscillator_cci(args: CCIArgs) -> dict:
        try:
            return {
                "ok": True,
                **_cci(args.highs, args.lows, args.closes, args.window),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Williams %R: −100 · (high_N − close) / (high_N − low_N). "
            "Bounded [−100, 0]; >−20 = overbought, <−80 = oversold. "
            "Inverted Stochastic %K essentially."
        )
    )
    async def oscillator_williams_r(args: WilliamsRArgs) -> dict:
        try:
            return {
                "ok": True,
                **_williams_r(args.highs, args.lows, args.closes, args.window),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "CCIArgs",
    "MACDArgs",
    "RSIArgs",
    "StochasticArgs",
    "WilliamsRArgs",
    "_cci",
    "_macd",
    "_rsi",
    "_stochastic",
    "_williams_r",
    "register",
]
