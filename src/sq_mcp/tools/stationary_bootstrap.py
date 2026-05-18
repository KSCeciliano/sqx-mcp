"""Politis-Romano stationary bootstrap for autocorrelated time series.

Standard bootstrap resamples i.i.d. observations. For financial returns
this destroys serial correlation (positive returns clustering, volatility
clustering). The stationary bootstrap (Politis & Romano 1994) instead
resamples geometric-distributed blocks: each draw extends with
probability (1 - 1/L) and restarts with probability 1/L, where L is the
expected block length.

Tools:

- ``stationary_bootstrap_resample`` — draw a single resampled series of
  length n_steps using the block structure.
- ``stationary_bootstrap_paths`` — draw B paths and return summary stats
  on the bootstrap distribution of a chosen aggregator (sum, mean,
  Sharpe).
- ``stationary_bootstrap_confidence`` — bootstrap confidence interval
  for a chosen statistic on a single time series.

Pure Python. Suitable as the input distribution for SPA / Reality Check
multiple-testing corrections.
"""

from __future__ import annotations

import math
import random
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    percentile,
    validate_finite_floats,
)


class ResampleArgs(BaseModel):
    series: list[float] = Field(..., min_length=10, max_length=500_000)
    block_length: float = Field(..., gt=1.0, le=10_000.0)
    n_steps: int | None = None
    seed: int | None = None


class PathsArgs(BaseModel):
    series: list[float] = Field(..., min_length=10, max_length=500_000)
    block_length: float = Field(..., gt=1.0, le=10_000.0)
    n_paths: int = Field(1000, ge=10, le=50_000)
    statistic: Literal["sum", "mean", "sharpe"] = "mean"
    seed: int | None = None


class ConfidenceArgs(BaseModel):
    series: list[float] = Field(..., min_length=10, max_length=500_000)
    block_length: float = Field(..., gt=1.0, le=10_000.0)
    n_paths: int = Field(1000, ge=10, le=50_000)
    statistic: Literal["sum", "mean", "sharpe", "median"] = "mean"
    confidence: float = Field(0.95, gt=0.5, lt=1.0)
    seed: int | None = None


def _resample_one(
    series: list[float], block_length: float, n_steps: int, rng: random.Random
) -> list[float]:
    n = len(series)
    p = 1.0 / block_length  # prob of restart
    out: list[float] = []
    if n == 0:
        return out
    idx = rng.randrange(n)
    for _ in range(n_steps):
        out.append(series[idx])
        if rng.random() < p:
            idx = rng.randrange(n)
        else:
            idx = (idx + 1) % n
    return out


def _statistic(series: list[float], stat: str) -> float:
    if not series:
        return 0.0
    if stat == "sum":
        return sum(series)
    if stat == "mean":
        return sum(series) / len(series)
    if stat == "median":
        s = sorted(series)
        n = len(s)
        return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])
    if stat == "sharpe":
        if len(series) < 2:
            return 0.0
        m = sum(series) / len(series)
        var = sum((x - m) ** 2 for x in series) / (len(series) - 1)
        if var <= 0:
            return 0.0
        return m / math.sqrt(var)
    return 0.0


def _resample_series(
    series: list[float], block_length: float, n_steps: int | None, seed: int | None
) -> dict[str, Any]:
    validate_finite_floats(series, name="series")
    rng = random.Random(seed)
    target_len = n_steps if n_steps is not None else len(series)
    sample = _resample_one(series, block_length, target_len, rng)
    return {
        "resampled": sample,
        "n": len(sample),
        "block_length": block_length,
        "seed": seed,
    }


def _paths(
    series: list[float],
    block_length: float,
    n_paths: int,
    statistic: str,
    seed: int | None,
) -> dict[str, Any]:
    validate_finite_floats(series, name="series")
    rng = random.Random(seed)
    stats: list[float] = []
    target_len = len(series)
    for _ in range(n_paths):
        sample = _resample_one(series, block_length, target_len, rng)
        stats.append(_statistic(sample, statistic))
    stats.sort()
    observed = _statistic(series, statistic)
    return {
        "statistic": statistic,
        "observed": round(observed, 6),
        "bootstrap_mean": round(sum(stats) / n_paths, 6),
        "bootstrap_std": round(
            math.sqrt(
                sum((s - sum(stats) / n_paths) ** 2 for s in stats) / max(1, n_paths - 1)
            ),
            6,
        ),
        "p5": round(percentile(stats, 5), 6),
        "p25": round(percentile(stats, 25), 6),
        "median": round(percentile(stats, 50), 6),
        "p75": round(percentile(stats, 75), 6),
        "p95": round(percentile(stats, 95), 6),
        "n_paths": n_paths,
        "block_length": block_length,
        "fraction_better_than_observed": round(
            sum(1 for s in stats if s >= observed) / n_paths, 4
        ),
    }


def _confidence(
    series: list[float],
    block_length: float,
    n_paths: int,
    statistic: str,
    confidence: float,
    seed: int | None,
) -> dict[str, Any]:
    validate_finite_floats(series, name="series")
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(n_paths):
        sample = _resample_one(series, block_length, len(series), rng)
        stats.append(_statistic(sample, statistic))
    stats.sort()
    alpha = (1.0 - confidence) / 2.0
    lo = percentile(stats, alpha * 100.0)
    hi = percentile(stats, (1.0 - alpha) * 100.0)
    return {
        "statistic": statistic,
        "observed": round(_statistic(series, statistic), 6),
        "confidence": confidence,
        "ci_lower": round(lo, 6),
        "ci_upper": round(hi, 6),
        "ci_width": round(hi - lo, 6),
        "n_paths": n_paths,
        "block_length": block_length,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Politis-Romano stationary bootstrap: draw a single resampled "
            "series by chaining geometric-length blocks (block_length is the "
            "expected block size; each step continues w.p. 1-1/L or restarts "
            "w.p. 1/L). Preserves short-range autocorrelation. Read-only."
        )
    )
    async def stationary_bootstrap_resample(args: ResampleArgs) -> dict:
        try:
            return {
                "ok": True,
                **_resample_series(
                    args.series, args.block_length, args.n_steps, args.seed
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Draw n_paths stationary-bootstrap paths and report the "
            "distribution of a chosen aggregator (sum / mean / sharpe). "
            "Returns percentiles + the fraction of paths whose statistic "
            "meets or exceeds the observed value. Read-only."
        )
    )
    async def stationary_bootstrap_paths(args: PathsArgs) -> dict:
        try:
            return {
                "ok": True,
                **_paths(
                    args.series,
                    args.block_length,
                    args.n_paths,
                    args.statistic,
                    args.seed,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Stationary-bootstrap confidence interval for a chosen statistic "
            "(sum / mean / sharpe / median) at the given confidence level "
            "(default 0.95). Returns CI bounds + width."
        )
    )
    async def stationary_bootstrap_confidence(args: ConfidenceArgs) -> dict:
        try:
            return {
                "ok": True,
                **_confidence(
                    args.series,
                    args.block_length,
                    args.n_paths,
                    args.statistic,
                    args.confidence,
                    args.seed,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "ConfidenceArgs",
    "PathsArgs",
    "ResampleArgs",
    "_confidence",
    "_paths",
    "_resample_one",
    "_resample_series",
    "_statistic",
    "register",
]
