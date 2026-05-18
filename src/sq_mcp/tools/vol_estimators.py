"""High-frequency volatility estimators (OHLC-based).

Close-to-close volatility uses one data point per bar. Better estimators
incorporate the full OHLC envelope, exploiting the additional information
in intra-bar excursion:

- **Parkinson (1980)**: uses (high - low). ~5x more efficient than C2C.
- **Garman-Klass (1980)**: adds open and close. ~7x more efficient.
- **Rogers-Satchell (1991)**: like GK but drift-independent.
- **Yang-Zhang (2000)**: combines overnight and intraday components,
  drift-independent, ~14x more efficient than C2C.

All estimators return *annualized* volatility (caller supplies
periods_per_year).

Tools:

- ``vol_close_to_close`` — baseline log-return stddev.
- ``vol_parkinson`` — Parkinson estimator from highs/lows.
- ``vol_garman_klass`` — Garman-Klass from OHLC.
- ``vol_rogers_satchell`` — Rogers-Satchell from OHLC.
- ``vol_yang_zhang`` — Yang-Zhang from OHLC (with overnight returns).
- ``vol_estimator_comparison`` — run all five on the same bars and
  return them side by side.

Pure Python.
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


class CloseSeriesArgs(BaseModel):
    closes: list[float] = Field(..., min_length=10, max_length=500_000)
    periods_per_year: int = Field(252, ge=1, le=525_600)


class HighLowSeriesArgs(BaseModel):
    highs: list[float] = Field(..., min_length=10, max_length=500_000)
    lows: list[float] = Field(..., min_length=10, max_length=500_000)
    periods_per_year: int = Field(252, ge=1, le=525_600)


class OHLCSeriesArgs(BaseModel):
    opens: list[float] = Field(..., min_length=10, max_length=500_000)
    highs: list[float] = Field(..., min_length=10, max_length=500_000)
    lows: list[float] = Field(..., min_length=10, max_length=500_000)
    closes: list[float] = Field(..., min_length=10, max_length=500_000)
    periods_per_year: int = Field(252, ge=1, le=525_600)


def _validate_ohlc(*series: list[float]) -> int:
    for i, s in enumerate(series):
        validate_finite_floats(s, name=f"series[{i}]")
    n = min(len(s) for s in series)
    return n


def _log_return(a: float, b: float) -> float:
    """log(a / b). Returns 0 if either is non-positive."""
    if a <= 0 or b <= 0:
        return 0.0
    return math.log(a / b)


def _annualize(period_var: float, periods_per_year: int) -> float:
    return math.sqrt(max(0.0, period_var) * periods_per_year)


def _vol_close_to_close(closes: list[float], periods_per_year: int) -> dict[str, Any]:
    validate_finite_floats(closes, name="closes")
    n = len(closes)
    if n < 2:
        return {"annualized_vol": None}
    returns = [_log_return(closes[i], closes[i - 1]) for i in range(1, n)]
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / max(1, len(returns) - 1)
    return {
        "annualized_vol": round(_annualize(var, periods_per_year), 6),
        "period_vol": round(math.sqrt(var), 6),
        "n": len(returns),
        "method": "close_to_close",
    }


def _vol_parkinson(highs: list[float], lows: list[float], periods_per_year: int) -> dict[str, Any]:
    n = _validate_ohlc(highs, lows)
    if n < 2:
        return {"annualized_vol": None}
    coeff = 1.0 / (4.0 * math.log(2.0))
    var_sum = 0.0
    for i in range(n):
        if highs[i] <= 0 or lows[i] <= 0:
            continue
        r = math.log(highs[i] / lows[i])
        var_sum += r * r
    var = coeff * var_sum / n
    return {
        "annualized_vol": round(_annualize(var, periods_per_year), 6),
        "period_vol": round(math.sqrt(var), 6),
        "n": n,
        "method": "parkinson",
    }


def _vol_garman_klass(
    opens: list[float], highs: list[float], lows: list[float],
    closes: list[float], periods_per_year: int,
) -> dict[str, Any]:
    n = _validate_ohlc(opens, highs, lows, closes)
    if n < 2:
        return {"annualized_vol": None}
    var_sum = 0.0
    for i in range(n):
        if min(opens[i], highs[i], lows[i], closes[i]) <= 0:
            continue
        log_hl = math.log(highs[i] / lows[i])
        log_co = math.log(closes[i] / opens[i])
        var_sum += 0.5 * log_hl ** 2 - (2.0 * math.log(2.0) - 1.0) * log_co ** 2
    var = var_sum / n
    return {
        "annualized_vol": round(_annualize(var, periods_per_year), 6),
        "period_vol": round(math.sqrt(max(0.0, var)), 6),
        "n": n,
        "method": "garman_klass",
    }


def _vol_rogers_satchell(
    opens: list[float], highs: list[float], lows: list[float],
    closes: list[float], periods_per_year: int,
) -> dict[str, Any]:
    n = _validate_ohlc(opens, highs, lows, closes)
    if n < 2:
        return {"annualized_vol": None}
    var_sum = 0.0
    for i in range(n):
        if min(opens[i], highs[i], lows[i], closes[i]) <= 0:
            continue
        log_ho = math.log(highs[i] / opens[i])
        log_hc = math.log(highs[i] / closes[i])
        log_lo = math.log(lows[i] / opens[i])
        log_lc = math.log(lows[i] / closes[i])
        var_sum += log_ho * log_hc + log_lo * log_lc
    var = var_sum / n
    return {
        "annualized_vol": round(_annualize(var, periods_per_year), 6),
        "period_vol": round(math.sqrt(max(0.0, var)), 6),
        "n": n,
        "method": "rogers_satchell",
    }


def _vol_yang_zhang(
    opens: list[float], highs: list[float], lows: list[float],
    closes: list[float], periods_per_year: int,
) -> dict[str, Any]:
    n = _validate_ohlc(opens, highs, lows, closes)
    if n < 3:
        return {"annualized_vol": None}
    # Overnight returns: log(open_t / close_{t-1})
    overnight: list[float] = []
    open_to_close: list[float] = []
    rs_terms: list[float] = []
    for i in range(1, n):
        if min(opens[i], highs[i], lows[i], closes[i], closes[i - 1]) <= 0:
            continue
        overnight.append(math.log(opens[i] / closes[i - 1]))
        open_to_close.append(math.log(closes[i] / opens[i]))
        log_ho = math.log(highs[i] / opens[i])
        log_hc = math.log(highs[i] / closes[i])
        log_lo = math.log(lows[i] / opens[i])
        log_lc = math.log(lows[i] / closes[i])
        rs_terms.append(log_ho * log_hc + log_lo * log_lc)
    m = len(overnight)
    if m < 2:
        return {"annualized_vol": None}
    on_mean = sum(overnight) / m
    oc_mean = sum(open_to_close) / m
    var_overnight = sum((r - on_mean) ** 2 for r in overnight) / (m - 1)
    var_oc = sum((r - oc_mean) ** 2 for r in open_to_close) / (m - 1)
    rs_var = sum(rs_terms) / m
    k = 0.34 / (1.34 + (m + 1) / max(1, m - 1))
    var = var_overnight + k * var_oc + (1.0 - k) * rs_var
    return {
        "annualized_vol": round(_annualize(var, periods_per_year), 6),
        "period_vol": round(math.sqrt(max(0.0, var)), 6),
        "n": m,
        "method": "yang_zhang",
    }


def _comparison(
    opens: list[float], highs: list[float], lows: list[float],
    closes: list[float], periods_per_year: int,
) -> dict[str, Any]:
    return {
        "close_to_close": _vol_close_to_close(closes, periods_per_year),
        "parkinson": _vol_parkinson(highs, lows, periods_per_year),
        "garman_klass": _vol_garman_klass(opens, highs, lows, closes, periods_per_year),
        "rogers_satchell": _vol_rogers_satchell(opens, highs, lows, closes, periods_per_year),
        "yang_zhang": _vol_yang_zhang(opens, highs, lows, closes, periods_per_year),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Close-to-close log-return volatility (annualized). Baseline "
            "estimator — uses only one data point per bar. Read-only."
        )
    )
    async def vol_close_to_close(args: CloseSeriesArgs) -> dict:
        try:
            return {"ok": True, **_vol_close_to_close(args.closes, args.periods_per_year)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Parkinson (1980) volatility from (high, low). Uses intraday "
            "range; ~5x more efficient than close-to-close for the same "
            "sample size."
        )
    )
    async def vol_parkinson(args: HighLowSeriesArgs) -> dict:
        try:
            return {
                "ok": True,
                **_vol_parkinson(args.highs, args.lows, args.periods_per_year),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Garman-Klass (1980) volatility from OHLC. ~7x more efficient "
            "than close-to-close. Assumes zero drift; use rogers_satchell or "
            "yang_zhang if drift is significant."
        )
    )
    async def vol_garman_klass(args: OHLCSeriesArgs) -> dict:
        try:
            return {
                "ok": True,
                **_vol_garman_klass(
                    args.opens, args.highs, args.lows, args.closes,
                    args.periods_per_year,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Rogers-Satchell (1991) volatility from OHLC. Drift-independent "
            "— preferred over Garman-Klass when the asset has a non-zero "
            "trend."
        )
    )
    async def vol_rogers_satchell(args: OHLCSeriesArgs) -> dict:
        try:
            return {
                "ok": True,
                **_vol_rogers_satchell(
                    args.opens, args.highs, args.lows, args.closes,
                    args.periods_per_year,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Yang-Zhang (2000) volatility from OHLC + overnight returns. "
            "Drift-independent, ~14x more efficient than close-to-close — "
            "the recommended high-frequency estimator."
        )
    )
    async def vol_yang_zhang(args: OHLCSeriesArgs) -> dict:
        try:
            return {
                "ok": True,
                **_vol_yang_zhang(
                    args.opens, args.highs, args.lows, args.closes,
                    args.periods_per_year,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Run all five volatility estimators (close-to-close, Parkinson, "
            "Garman-Klass, Rogers-Satchell, Yang-Zhang) on the same OHLC "
            "data. Returns them side-by-side for comparison."
        )
    )
    async def vol_estimator_comparison(args: OHLCSeriesArgs) -> dict:
        try:
            return {
                "ok": True,
                **_comparison(
                    args.opens, args.highs, args.lows, args.closes,
                    args.periods_per_year,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "CloseSeriesArgs",
    "HighLowSeriesArgs",
    "OHLCSeriesArgs",
    "_comparison",
    "_vol_close_to_close",
    "_vol_garman_klass",
    "_vol_parkinson",
    "_vol_rogers_satchell",
    "_vol_yang_zhang",
    "register",
]
