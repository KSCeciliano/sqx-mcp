"""Monte Carlo simulation over equity curves and trade lists.

Bootstrap-based path sampling: given a list of historical returns (the
strategy's actual per-trade PnLs, or differences from an equity curve),
draw N synthetic paths by resampling with replacement. Use to estimate
the *distribution* of outcomes a strategy might produce — not just the
single historical path that the backtest happened to follow.

Tools:

- ``montecarlo_equity_paths`` — given a strategy's .sqx, derive trade-by-
  trade returns from the embedded equity sparkline (diffs), bootstrap N
  synthetic equity paths, and report distributional stats on terminal
  return and max drawdown across paths.
- ``montecarlo_drawdown_distribution`` — same input, but reports max-DD
  percentiles only (faster, lighter return).
- ``montecarlo_probability_of_drawdown`` — given a list of returns and a
  drawdown threshold (e.g. "50% drawdown"), reports the probability of
  hitting that drawdown across N simulated paths.
- ``montecarlo_synthetic_from_returns`` — bootstrap purely from an
  explicit return series (caller-supplied), useful when the source data
  is something other than an .sqx (e.g. mt5 backtest report).

All sampling is bootstrap (resample with replacement); seed is exposed
for reproducibility. Pure Python, no numpy.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
)
from sq_mcp.parsers.sqx import parse_sqx
from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)
from sq_mcp.tools.comparison import _curve_to_returns
from sq_mcp.tools.strategy_inspect import _select_curve

# ---- argument schemas ------------------------------------------------------


class MonteCarloSqxArgs(BaseModel):
    sqx_path: str
    n_paths: int = Field(1000, ge=100, le=20000)
    seed: int | None = None


class MonteCarloReturnsArgs(BaseModel):
    returns: list[float] = Field(..., min_length=10, max_length=100000)
    n_paths: int = Field(1000, ge=100, le=20000)
    n_steps: int | None = Field(None, ge=10, le=100000)
    seed: int | None = None


class MonteCarloProbDdArgs(BaseModel):
    returns: list[float] = Field(..., min_length=10, max_length=100000)
    drawdown_threshold_pct: float = Field(..., gt=0, le=100)
    n_paths: int = Field(1000, ge=100, le=20000)
    seed: int | None = None


# ---- helpers ---------------------------------------------------------------


def _percentile(sorted_xs: list[float], p: float) -> float:
    if not sorted_xs:
        return 0.0
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    idx = (len(sorted_xs) - 1) * (p / 100.0)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (idx - lo)


def _bootstrap_path(returns: list[float], n_steps: int, rng: random.Random) -> list[float]:
    """Resample with replacement and accumulate into an equity path."""
    equity = 1.0
    path: list[float] = []
    for _ in range(n_steps):
        r = rng.choice(returns)
        equity *= 1.0 + r
        path.append(equity)
    return path


def _max_drawdown(path: list[float]) -> float:
    """Max drawdown as a fraction of peak (0.0 → 1.0)."""
    if not path:
        return 0.0
    peak = path[0]
    max_dd = 0.0
    for v in path:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _run_simulation(
    returns: list[float], *, n_paths: int, n_steps: int, seed: int | None
) -> dict[str, Any]:
    validate_finite_floats(returns, name="returns", min_length=1)
    rng = random.Random(seed)
    terminal_returns: list[float] = []
    max_dds: list[float] = []
    for _ in range(n_paths):
        path = _bootstrap_path(returns, n_steps, rng)
        terminal_returns.append((path[-1] - 1.0) * 100.0)  # %
        max_dds.append(_max_drawdown(path) * 100.0)  # %

    terminal_returns.sort()
    max_dds.sort()
    return {
        "n_paths": n_paths,
        "n_steps": n_steps,
        "seed": seed,
        "terminal_return_pct": {
            "p5": round(_percentile(terminal_returns, 5), 4),
            "p25": round(_percentile(terminal_returns, 25), 4),
            "median": round(_percentile(terminal_returns, 50), 4),
            "p75": round(_percentile(terminal_returns, 75), 4),
            "p95": round(_percentile(terminal_returns, 95), 4),
            "mean": round(sum(terminal_returns) / len(terminal_returns), 4),
        },
        "max_drawdown_pct": {
            "p5": round(_percentile(max_dds, 5), 4),
            "p25": round(_percentile(max_dds, 25), 4),
            "median": round(_percentile(max_dds, 50), 4),
            "p75": round(_percentile(max_dds, 75), 4),
            "p95": round(_percentile(max_dds, 95), 4),
            "mean": round(sum(max_dds) / len(max_dds), 4),
            "worst": round(max(max_dds), 4),
        },
        "probability_of_loss_pct": round(
            100.0 * sum(1 for r in terminal_returns if r < 0) / len(terminal_returns), 2
        ),
    }


def _probability_of_drawdown(
    returns: list[float], *, threshold_pct: float, n_paths: int, n_steps: int, seed: int | None
) -> dict[str, Any]:
    validate_finite_floats(returns, name="returns", min_length=1)
    rng = random.Random(seed)
    threshold = threshold_pct / 100.0
    hits = 0
    worst_dds: list[float] = []
    for _ in range(n_paths):
        path = _bootstrap_path(returns, n_steps, rng)
        dd = _max_drawdown(path)
        worst_dds.append(dd * 100.0)
        if dd >= threshold:
            hits += 1
    worst_dds.sort()
    return {
        "n_paths": n_paths,
        "threshold_pct": threshold_pct,
        "probability_pct": round(100.0 * hits / n_paths, 4),
        "worst_drawdown_pct_observed": round(max(worst_dds), 4),
        "median_max_drawdown_pct": round(_percentile(worst_dds, 50), 4),
        "seed": seed,
    }


def _sqx_to_returns(sqx_path: Path) -> tuple[list[float], dict[str, Any]]:
    """Parse a .sqx, pull the embedded equity sparkline, convert to returns."""
    info = parse_sqx(sqx_path)
    curves = info.get("equity_curves") if isinstance(info, dict) else None
    curve = _select_curve(curves)
    if not curve or len(curve) < 10:
        return [], {"ok": False, "error": "no usable equity curve in .sqx"}
    returns = _curve_to_returns(curve)
    if not returns or len(returns) < 10:
        return [], {"ok": False, "error": "fewer than 10 samples after diff"}
    return returns, {"ok": True, "n_samples": len(returns)}


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Monte Carlo bootstrap over a strategy's equity curve. Parses the "
            ".sqx, extracts the embedded equity sparkline, converts to per-sample "
            "returns, and resamples with replacement to produce N synthetic "
            "paths. Reports percentile distribution of terminal returns and "
            "max-drawdown across paths. Read-only."
        )
    )
    async def montecarlo_equity_paths(args: MonteCarloSqxArgs) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path)
        except ValidationError as e:
            return {"ok": False, "error": str(e)}
        if not p.is_file():
            return {"ok": False, "error": f"file not found: {p}"}
        returns, meta = _sqx_to_returns(p)
        if not meta.get("ok"):
            return meta
        result = _run_simulation(
            returns, n_paths=args.n_paths, n_steps=len(returns), seed=args.seed
        )
        return {"ok": True, "source": str(p), "sample_size": meta["n_samples"], **result}

    @mcp.tool(
        description=(
            "Monte Carlo bootstrap over a strategy's equity curve, but reporting "
            "only max-drawdown percentiles (lighter return than "
            "montecarlo_equity_paths). Read-only."
        )
    )
    async def montecarlo_drawdown_distribution(args: MonteCarloSqxArgs) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path)
        except ValidationError as e:
            return {"ok": False, "error": str(e)}
        if not p.is_file():
            return {"ok": False, "error": f"file not found: {p}"}
        returns, meta = _sqx_to_returns(p)
        if not meta.get("ok"):
            return meta
        result = _run_simulation(
            returns, n_paths=args.n_paths, n_steps=len(returns), seed=args.seed
        )
        return {"ok": True, "source": str(p), "max_drawdown_pct": result["max_drawdown_pct"]}

    @mcp.tool(
        description=(
            "Probability of hitting drawdown_threshold_pct across N Monte Carlo "
            "paths. Caller supplies an explicit returns array (use "
            "montecarlo_equity_paths if you'd rather start from an .sqx). "
            "Read-only."
        )
    )
    async def montecarlo_probability_of_drawdown(args: MonteCarloProbDdArgs) -> dict:
        try:
            return {
                "ok": True,
                **_probability_of_drawdown(
                    args.returns,
                    threshold_pct=args.drawdown_threshold_pct,
                    n_paths=args.n_paths,
                    n_steps=len(args.returns),
                    seed=args.seed,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Monte Carlo bootstrap from an explicit per-trade returns array (any "
            "source, not just SQ X). Useful when the caller has an MT5 backtest "
            "report or another strategy backtester's results. Pure math — no "
            "engine call."
        )
    )
    async def montecarlo_synthetic_from_returns(args: MonteCarloReturnsArgs) -> dict:
        try:
            n_steps = args.n_steps or len(args.returns)
            result = _run_simulation(
                args.returns, n_paths=args.n_paths, n_steps=n_steps, seed=args.seed
            )
            return {"ok": True, "input_samples": len(args.returns), **result}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}
