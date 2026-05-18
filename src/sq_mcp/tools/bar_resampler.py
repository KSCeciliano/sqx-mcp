"""Bar resampling between timeframes.

Convert between bar granularities (e.g. M1 → M5, M5 → H1) by aggregating
OHLCV in fixed-interval buckets. Handy when you have one timeframe on
disk and need a coarser one for a backtest without re-importing data.

Tools:

- ``bar_resample_to_interval`` — resample OHLCV bars to a new bar
  interval (seconds). Standard OHLC aggregation: first open, max high,
  min low, last close, sum volume.
- ``bar_align_to_session`` — align bars to a session start (e.g. UTC
  midnight, NY open). Drops partial bars at the boundary.
- ``bar_convert_timeframe`` — convenience: M5, M15, H1, H4, D1 strings
  mapped to seconds.

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

_TF_TO_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14_400,
    "D1": 86_400, "W1": 604_800,
}


class ResampleArgs(BaseModel):
    timestamps_epoch: list[int] = Field(..., min_length=2, max_length=1_000_000)
    opens: list[float] = Field(..., min_length=2, max_length=1_000_000)
    highs: list[float] = Field(..., min_length=2, max_length=1_000_000)
    lows: list[float] = Field(..., min_length=2, max_length=1_000_000)
    closes: list[float] = Field(..., min_length=2, max_length=1_000_000)
    volumes: list[float] = Field(..., min_length=2, max_length=1_000_000)
    target_interval_seconds: int = Field(..., ge=60, le=604_800)


class AlignSessionArgs(BaseModel):
    timestamps_epoch: list[int] = Field(..., min_length=2, max_length=1_000_000)
    opens: list[float] = Field(..., min_length=2, max_length=1_000_000)
    highs: list[float] = Field(..., min_length=2, max_length=1_000_000)
    lows: list[float] = Field(..., min_length=2, max_length=1_000_000)
    closes: list[float] = Field(..., min_length=2, max_length=1_000_000)
    volumes: list[float] = Field(..., min_length=2, max_length=1_000_000)
    session_start_hour_utc: int = Field(0, ge=0, le=23)


class TfConvertArgs(BaseModel):
    from_tf: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
    to_tf: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]


def _resample(
    ts: list[int], o: list[float], h: list[float], low: list[float],
    c: list[float], v: list[float], interval: int,
) -> dict[str, Any]:
    for series, name in (
        (o, "opens"), (h, "highs"), (low, "lows"), (c, "closes"), (v, "volumes"),
    ):
        validate_finite_floats(series, name=name)
    n = min(len(ts), len(o), len(h), len(low), len(c), len(v))
    if n < 2:
        return {"bars": [], "n_bars": 0}
    bars: list[dict[str, Any]] = []
    cur_bucket = ts[0] - (ts[0] % interval)
    bucket_o = o[0]
    bucket_h = h[0]
    bucket_l = low[0]
    bucket_c = c[0]
    bucket_v = v[0]
    bucket_ticks = 1
    for i in range(1, n):
        bucket = ts[i] - (ts[i] % interval)
        if bucket != cur_bucket:
            bars.append({
                "epoch": cur_bucket,
                "open": round(bucket_o, 8),
                "high": round(bucket_h, 8),
                "low": round(bucket_l, 8),
                "close": round(bucket_c, 8),
                "volume": round(bucket_v, 8),
                "n_source_bars": bucket_ticks,
            })
            cur_bucket = bucket
            bucket_o = o[i]
            bucket_h = h[i]
            bucket_l = low[i]
            bucket_c = c[i]
            bucket_v = v[i]
            bucket_ticks = 1
        else:
            if h[i] > bucket_h:
                bucket_h = h[i]
            if low[i] < bucket_l:
                bucket_l = low[i]
            bucket_c = c[i]
            bucket_v += v[i]
            bucket_ticks += 1
    bars.append({
        "epoch": cur_bucket,
        "open": round(bucket_o, 8),
        "high": round(bucket_h, 8),
        "low": round(bucket_l, 8),
        "close": round(bucket_c, 8),
        "volume": round(bucket_v, 8),
        "n_source_bars": bucket_ticks,
    })
    return {
        "bars": bars,
        "n_bars": len(bars),
        "n_input": n,
        "target_interval_seconds": interval,
        "compression_ratio": round(n / len(bars), 2) if bars else None,
    }


def _align_session(
    ts: list[int], o: list[float], h: list[float], low: list[float],
    c: list[float], v: list[float], session_start_hour: int,
) -> dict[str, Any]:
    """Group into daily bars starting at session_start_hour UTC. Drops the
    first partial bar (anything before the first session_start).
    """
    for series, name in (
        (o, "opens"), (h, "highs"), (low, "lows"), (c, "closes"), (v, "volumes"),
    ):
        validate_finite_floats(series, name=name)
    n = min(len(ts), len(o), len(h), len(low), len(c), len(v))
    if n < 2:
        return {"bars": [], "n_bars": 0}
    session_offset_seconds = session_start_hour * 3600
    bars: list[dict[str, Any]] = []
    cur_day: int | None = None
    bucket_o: float = 0.0
    bucket_h = 0.0
    bucket_l = 0.0
    bucket_c = 0.0
    bucket_v = 0.0
    bucket_ticks = 0
    n_partial_skipped = 0
    for i in range(n):
        # Day-of-epoch starting at session_start_hour
        adj_epoch = ts[i] - session_offset_seconds
        day_idx = adj_epoch // 86_400
        if cur_day is None:
            cur_day = day_idx
            # Skip the partial first bar if ts[0] doesn't land on a session boundary
            if ts[i] % 86_400 != session_offset_seconds % 86_400:
                n_partial_skipped += 1
                continue
        if day_idx != cur_day:
            bars.append({
                "session_start_epoch": cur_day * 86_400 + session_offset_seconds,
                "open": round(bucket_o, 8),
                "high": round(bucket_h, 8),
                "low": round(bucket_l, 8),
                "close": round(bucket_c, 8),
                "volume": round(bucket_v, 8),
                "n_source_bars": bucket_ticks,
            })
            cur_day = day_idx
            bucket_o = o[i]
            bucket_h = h[i]
            bucket_l = low[i]
            bucket_c = c[i]
            bucket_v = v[i]
            bucket_ticks = 1
        else:
            if bucket_ticks == 0:
                bucket_o = o[i]
                bucket_h = h[i]
                bucket_l = low[i]
            else:
                if h[i] > bucket_h:
                    bucket_h = h[i]
                if low[i] < bucket_l:
                    bucket_l = low[i]
            bucket_c = c[i]
            bucket_v += v[i]
            bucket_ticks += 1
    if bucket_ticks > 0 and cur_day is not None:
        bars.append({
            "session_start_epoch": cur_day * 86_400 + session_offset_seconds,
            "open": round(bucket_o, 8),
            "high": round(bucket_h, 8),
            "low": round(bucket_l, 8),
            "close": round(bucket_c, 8),
            "volume": round(bucket_v, 8),
            "n_source_bars": bucket_ticks,
        })
    return {
        "bars": bars,
        "n_bars": len(bars),
        "session_start_hour_utc": session_start_hour,
        "n_partial_skipped": n_partial_skipped,
    }


def _convert_tf(from_tf: str, to_tf: str) -> dict[str, Any]:
    if from_tf not in _TF_TO_SECONDS or to_tf not in _TF_TO_SECONDS:
        return {"ok": False, "error": f"unknown timeframe; valid: {sorted(_TF_TO_SECONDS)}"}
    from_s = _TF_TO_SECONDS[from_tf]
    to_s = _TF_TO_SECONDS[to_tf]
    if to_s % from_s != 0:
        return {
            "ok": False,
            "error": f"target {to_tf} is not an integer multiple of {from_tf}",
            "from_seconds": from_s,
            "to_seconds": to_s,
        }
    ratio = to_s // from_s
    return {
        "ok": True,
        "from_tf": from_tf,
        "to_tf": to_tf,
        "from_seconds": from_s,
        "to_seconds": to_s,
        "bars_per_target_bar": ratio,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Resample OHLCV bars to a new interval (in seconds). Standard "
            "aggregation: open=first, high=max, low=min, close=last, volume=sum. "
            "Buckets are aligned to interval boundaries (epoch %% interval)."
        )
    )
    async def bar_resample_to_interval(args: ResampleArgs) -> dict:
        try:
            return {
                "ok": True,
                **_resample(
                    args.timestamps_epoch, args.opens, args.highs, args.lows,
                    args.closes, args.volumes, args.target_interval_seconds,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Align intraday bars to daily session boundaries starting at "
            "session_start_hour_utc (default 0). Returns one bar per session "
            "with full OHLCV. Drops the first partial bar if it doesn't land "
            "on a session boundary."
        )
    )
    async def bar_align_to_session(args: AlignSessionArgs) -> dict:
        try:
            return {
                "ok": True,
                **_align_session(
                    args.timestamps_epoch, args.opens, args.highs, args.lows,
                    args.closes, args.volumes, args.session_start_hour_utc,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Convert between conventional timeframe strings (M1 / M5 / M15 / "
            "M30 / H1 / H4 / D1 / W1). Returns the bars_per_target_bar "
            "compression ratio. Fails if the target is not an integer "
            "multiple of the source."
        )
    )
    async def bar_convert_timeframe(args: TfConvertArgs) -> dict:
        return _convert_tf(args.from_tf, args.to_tf)


__all__ = [
    "AlignSessionArgs",
    "ResampleArgs",
    "TfConvertArgs",
    "_align_session",
    "_convert_tf",
    "_resample",
    "register",
]
