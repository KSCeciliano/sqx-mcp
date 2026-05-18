"""Information-driven bar construction (Lopez de Prado, AFML Ch.2).

Time bars (1m, 5m, 1h, …) oversample quiet periods and undersample busy
periods. Alternative bar definitions sample at constant *information*
instead of constant time:

- **Tick bars**: one bar per N ticks. Captures order-arrival rate.
- **Volume bars**: one bar per V units traded. Captures liquidity.
- **Dollar bars**: one bar per D dollar volume. Captures capital flow.
  Most robust to changes in price level (a $100 bar is the same idea
  whether BTC is at $20k or $80k).

Tools:

- ``bar_construct_tick`` — group consecutive ticks into bars of N ticks
  each. Emits OHLC + volume + timestamp.
- ``bar_construct_volume`` — group ticks into bars of V cumulative
  volume each.
- ``bar_construct_dollar`` — group ticks into bars of D cumulative
  dollar volume each. Most stable across price regimes.
- ``bar_construct_imbalance`` — emit a bar whenever the cumulative
  signed-tick-volume imbalance exceeds a threshold (information bars).

Pure Python. Caller supplies ticks as (timestamp_epoch, price, volume,
optional aggressor_side) tuples.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)


class TickBarArgs(BaseModel):
    timestamps_epoch: list[int] = Field(..., min_length=2, max_length=1_000_000)
    prices: list[float] = Field(..., min_length=2, max_length=1_000_000)
    volumes: list[float] = Field(..., min_length=2, max_length=1_000_000)
    ticks_per_bar: int = Field(..., ge=2, le=1_000_000)


class VolumeBarArgs(BaseModel):
    timestamps_epoch: list[int] = Field(..., min_length=2, max_length=1_000_000)
    prices: list[float] = Field(..., min_length=2, max_length=1_000_000)
    volumes: list[float] = Field(..., min_length=2, max_length=1_000_000)
    volume_per_bar: float = Field(..., gt=0.0)


class DollarBarArgs(BaseModel):
    timestamps_epoch: list[int] = Field(..., min_length=2, max_length=1_000_000)
    prices: list[float] = Field(..., min_length=2, max_length=1_000_000)
    volumes: list[float] = Field(..., min_length=2, max_length=1_000_000)
    dollar_per_bar: float = Field(..., gt=0.0)


class ImbalanceBarArgs(BaseModel):
    timestamps_epoch: list[int] = Field(..., min_length=2, max_length=1_000_000)
    prices: list[float] = Field(..., min_length=2, max_length=1_000_000)
    volumes: list[float] = Field(..., min_length=2, max_length=1_000_000)
    aggressor_side: list[Literal["buy", "sell"]] = Field(
        ..., min_length=2, max_length=1_000_000
    )
    imbalance_threshold: float = Field(..., gt=0.0)


def _validate_tick_series(
    ts: list[int], prices: list[float], volumes: list[float]
) -> int:
    validate_finite_floats(prices, name="prices")
    validate_finite_floats(volumes, name="volumes")
    return min(len(ts), len(prices), len(volumes))


def _emit_bar(
    open_t: int, close_t: int, prices: list[float], volumes: list[float],
    lo: int, hi: int,
) -> dict[str, Any]:
    bar_prices = prices[lo : hi + 1]
    bar_volumes = volumes[lo : hi + 1]
    return {
        "open_epoch": open_t,
        "close_epoch": close_t,
        "n_ticks": hi - lo + 1,
        "open": round(bar_prices[0], 8),
        "high": round(max(bar_prices), 8),
        "low": round(min(bar_prices), 8),
        "close": round(bar_prices[-1], 8),
        "volume": round(sum(bar_volumes), 8),
    }


def _tick_bars(
    ts: list[int], prices: list[float], volumes: list[float], n_per: int
) -> dict[str, Any]:
    n = _validate_tick_series(ts, prices, volumes)
    bars: list[dict[str, Any]] = []
    for start in range(0, n, n_per):
        end = min(start + n_per - 1, n - 1)
        if end - start < 1:
            continue
        bars.append(_emit_bar(ts[start], ts[end], prices, volumes, start, end))
    return {
        "bars": bars,
        "n_bars": len(bars),
        "ticks_per_bar": n_per,
        "ticks_consumed": min(n, len(bars) * n_per),
    }


def _volume_bars(
    ts: list[int], prices: list[float], volumes: list[float], vol_per: float
) -> dict[str, Any]:
    n = _validate_tick_series(ts, prices, volumes)
    bars: list[dict[str, Any]] = []
    cum = 0.0
    start_idx = 0
    for i in range(n):
        cum += volumes[i]
        if cum >= vol_per:
            bars.append(_emit_bar(ts[start_idx], ts[i], prices, volumes, start_idx, i))
            start_idx = i + 1
            cum = 0.0
    return {
        "bars": bars,
        "n_bars": len(bars),
        "volume_per_bar": vol_per,
        "leftover_volume": round(cum, 8),
    }


def _dollar_bars(
    ts: list[int], prices: list[float], volumes: list[float], dollar_per: float
) -> dict[str, Any]:
    n = _validate_tick_series(ts, prices, volumes)
    bars: list[dict[str, Any]] = []
    cum_dollars = 0.0
    start_idx = 0
    for i in range(n):
        cum_dollars += prices[i] * volumes[i]
        if cum_dollars >= dollar_per:
            bars.append(_emit_bar(ts[start_idx], ts[i], prices, volumes, start_idx, i))
            start_idx = i + 1
            cum_dollars = 0.0
    return {
        "bars": bars,
        "n_bars": len(bars),
        "dollar_per_bar": dollar_per,
        "leftover_dollars": round(cum_dollars, 6),
    }


def _imbalance_bars(
    ts: list[int],
    prices: list[float],
    volumes: list[float],
    sides: list[str],
    threshold: float,
) -> dict[str, Any]:
    n = min(
        _validate_tick_series(ts, prices, volumes),
        len(sides),
    )
    bars: list[dict[str, Any]] = []
    cum_signed = 0.0
    start_idx = 0
    for i in range(n):
        signed = volumes[i] if sides[i] == "buy" else -volumes[i]
        cum_signed += signed
        if abs(cum_signed) >= threshold:
            bar = _emit_bar(ts[start_idx], ts[i], prices, volumes, start_idx, i)
            bar["imbalance_direction"] = "buy" if cum_signed > 0 else "sell"
            bar["imbalance_value"] = round(cum_signed, 8)
            bars.append(bar)
            start_idx = i + 1
            cum_signed = 0.0
    return {
        "bars": bars,
        "n_bars": len(bars),
        "imbalance_threshold": threshold,
        "leftover_imbalance": round(cum_signed, 8),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Tick bars: group consecutive ticks into bars of N ticks each. "
            "Returns OHLC+volume+timestamp per bar. Useful when "
            "order-arrival rate matters more than wall-clock time."
        )
    )
    async def bar_construct_tick(args: TickBarArgs) -> dict:
        try:
            return {
                "ok": True,
                **_tick_bars(
                    args.timestamps_epoch, args.prices, args.volumes,
                    args.ticks_per_bar,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Volume bars: emit a bar every time cumulative traded volume "
            "exceeds volume_per_bar. Captures liquidity-driven sampling — "
            "busier periods produce more bars."
        )
    )
    async def bar_construct_volume(args: VolumeBarArgs) -> dict:
        try:
            return {
                "ok": True,
                **_volume_bars(
                    args.timestamps_epoch, args.prices, args.volumes,
                    args.volume_per_bar,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Dollar bars: emit a bar every time cumulative price·volume "
            "exceeds dollar_per_bar. Most stable across price regimes — a "
            "$1M bar is the same idea whether BTC is at $20k or $80k. "
            "Recommended for crypto."
        )
    )
    async def bar_construct_dollar(args: DollarBarArgs) -> dict:
        try:
            return {
                "ok": True,
                **_dollar_bars(
                    args.timestamps_epoch, args.prices, args.volumes,
                    args.dollar_per_bar,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Imbalance bars: emit a bar whenever cumulative signed-volume "
            "imbalance (buy − sell) exceeds threshold. Bar reports the "
            "imbalance direction and magnitude. AFML Ch.2 'information bars'."
        )
    )
    async def bar_construct_imbalance(args: ImbalanceBarArgs) -> dict:
        try:
            return {
                "ok": True,
                **_imbalance_bars(
                    args.timestamps_epoch, args.prices, args.volumes,
                    args.aggressor_side, args.imbalance_threshold,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "DollarBarArgs",
    "ImbalanceBarArgs",
    "TickBarArgs",
    "VolumeBarArgs",
    "_dollar_bars",
    "_imbalance_bars",
    "_tick_bars",
    "_volume_bars",
    "register",
]
