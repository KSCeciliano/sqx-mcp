"""Stress testing — perturb a strategy's assumptions and report degradation.

Real-world execution is messier than a backtest: trades slip, orders
get filled at worse prices, the broker occasionally misses bars, the
internet hiccups during a critical moment. These tools take an explicit
trade-PnL or equity series and apply *synthetic* perturbations to
estimate how the strategy holds up.

This is distinct from Monte Carlo (which resamples the existing
distribution): stress tests *modify* the distribution to model worse
conditions than were ever observed in the backtest.

Tools:

- ``stress_apply_slippage`` — degrade every trade by ``slip_pct``
  (e.g. 0.05 = 5%); report degraded metrics.
- ``stress_apply_skip_best`` — remove the top-N best trades and
  report degraded metrics; tests whether the strategy survives
  without the lucky outliers.
- ``stress_apply_random_skip`` — drop a random ``skip_fraction`` of
  trades (simulates connectivity loss); report degradation.
- ``stress_worst_case_dd`` — apply a synthetic worst-case streak
  scenario (N consecutive worst trades) and report the resulting
  drawdown.
- ``stress_combined_scenarios`` — run several stress scenarios at
  once and return a summary table.
"""

from __future__ import annotations

import random
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)

# ---- argument schemas ------------------------------------------------------


class TradesArgs(BaseModel):
    trades: list[float] = Field(..., min_length=10, max_length=100_000)


class SlippageArgs(BaseModel):
    trades: list[float] = Field(..., min_length=10, max_length=100_000)
    slip_pct: float = Field(..., gt=0, le=100)  # %


class SkipBestArgs(BaseModel):
    trades: list[float] = Field(..., min_length=10, max_length=100_000)
    n_best_to_remove: int = Field(..., ge=1, le=1000)


class RandomSkipArgs(BaseModel):
    trades: list[float] = Field(..., min_length=10, max_length=100_000)
    skip_fraction: float = Field(..., gt=0, lt=1)
    seed: int = 42


class WorstStreakArgs(BaseModel):
    trades: list[float] = Field(..., min_length=10, max_length=100_000)
    streak_length: int = Field(..., ge=1, le=1000)


class CombinedArgs(BaseModel):
    trades: list[float] = Field(..., min_length=10, max_length=100_000)
    slip_pct: float = Field(2.0, gt=0, le=100)
    n_best_to_remove: int = Field(5, ge=1, le=1000)
    skip_fraction: float = Field(0.05, gt=0, lt=1)
    streak_length: int = Field(10, ge=1, le=1000)
    seed: int = 42


# ---- helpers ---------------------------------------------------------------


def _metrics(trades: list[float]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "n_trades": 0,
            "net": 0.0,
            "max_drawdown_abs": 0.0,
            "win_rate": None,
            "average_trade": None,
        }
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum += t
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    wins = sum(1 for t in trades if t > 0)
    return {
        "n_trades": n,
        "net": round(cum, 6),
        "max_drawdown_abs": round(max_dd, 6),
        "win_rate": round(wins / n, 4),
        "average_trade": round(cum / n, 6),
    }


def _apply_slip(trades: list[float], slip_pct: float) -> list[float]:
    f = slip_pct / 100.0
    out = []
    for t in trades:
        # Slip is a haircut: positive trades lose some, losses get worse
        if t > 0:
            out.append(t * (1.0 - f))
        elif t < 0:
            out.append(t * (1.0 + f))
        else:
            out.append(t)
    return out


def _apply_skip_best(trades: list[float], n: int) -> list[float]:
    # Sort descending; keep all but the top n
    sorted_idx = sorted(range(len(trades)), key=lambda i: trades[i], reverse=True)
    skipped = set(sorted_idx[:n])
    return [t for i, t in enumerate(trades) if i not in skipped]


def _apply_random_skip(trades: list[float], fraction: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    out = []
    for t in trades:
        if rng.random() < fraction:
            continue
        out.append(t)
    return out


def _apply_worst_streak(trades: list[float], streak_length: int) -> list[float]:
    """Pick the streak_length worst trades; inject them consecutively at the start.

    This produces an upper-bound on drawdown the strategy might have *seen* if
    its worst N losses happened to land in a row.
    """
    sorted_asc = sorted(trades)
    worst = sorted_asc[:streak_length]
    remaining = trades.copy()
    # Build series: worst first, then the rest (worst already removed)
    for w in worst:
        try:
            remaining.remove(w)
        except ValueError:
            pass
    return list(worst) + remaining


def _summarize(original: dict[str, Any], degraded: dict[str, Any]) -> dict[str, Any]:
    net_orig = original.get("net", 0.0) or 0.0
    net_deg = degraded.get("net", 0.0) or 0.0
    return {
        "original": original,
        "degraded": degraded,
        "net_reduction": round(net_orig - net_deg, 6),
        "net_reduction_pct": (
            round(100 * (net_orig - net_deg) / abs(net_orig), 4)
            if net_orig
            else None
        ),
        "survives": net_deg > 0,
    }


def _combined(args: CombinedArgs) -> dict[str, Any]:
    original = _metrics(args.trades)
    scenarios = {}
    scenarios["slippage"] = _summarize(
        original, _metrics(_apply_slip(args.trades, args.slip_pct))
    )
    scenarios["skip_best"] = _summarize(
        original, _metrics(_apply_skip_best(args.trades, args.n_best_to_remove))
    )
    scenarios["random_skip"] = _summarize(
        original,
        _metrics(_apply_random_skip(args.trades, args.skip_fraction, args.seed)),
    )
    scenarios["worst_streak"] = _summarize(
        original, _metrics(_apply_worst_streak(args.trades, args.streak_length))
    )
    # Verdict: how many scenarios kill the strategy?
    dead = sum(1 for s in scenarios.values() if not s["survives"])
    verdict = (
        "fragile" if dead >= 3 else "moderate" if dead >= 1 else "robust"
    )
    return {
        "original_metrics": original,
        "scenarios": scenarios,
        "n_scenarios_killed": dead,
        "verdict": verdict,
    }


# ---- MCP registration ------------------------------------------------------


def _validate_trades_or_error(trades: list[float]) -> dict[str, Any] | None:
    try:
        validate_finite_floats(trades, name="trades")
    except NumericValidationError as exc:
        return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}
    return None


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Apply a slippage haircut: positive trades lose slip_pct, losses "
            "get larger by slip_pct. Report degraded vs original metrics. "
            "Realistic crypto/forex slippage is 1-5%."
        )
    )
    async def stress_apply_slippage(args: SlippageArgs) -> dict:
        err = _validate_trades_or_error(args.trades)
        if err:
            return err
        original = _metrics(args.trades)
        degraded = _metrics(_apply_slip(args.trades, args.slip_pct))
        return {"ok": True, "slip_pct": args.slip_pct, **_summarize(original, degraded)}

    @mcp.tool(
        description=(
            "Remove the top-N best trades and re-compute metrics. Tests whether "
            "the strategy survives without the lucky outliers. If the strategy "
            "dies after removing 1-2 trades, it's brittle."
        )
    )
    async def stress_apply_skip_best(args: SkipBestArgs) -> dict:
        err = _validate_trades_or_error(args.trades)
        if err:
            return err
        original = _metrics(args.trades)
        degraded = _metrics(_apply_skip_best(args.trades, args.n_best_to_remove))
        return {
            "ok": True,
            "n_removed": args.n_best_to_remove,
            **_summarize(original, degraded),
        }

    @mcp.tool(
        description=(
            "Drop a random fraction of trades (simulates connectivity loss). "
            "Seed is exposed for reproducibility."
        )
    )
    async def stress_apply_random_skip(args: RandomSkipArgs) -> dict:
        err = _validate_trades_or_error(args.trades)
        if err:
            return err
        original = _metrics(args.trades)
        degraded = _metrics(
            _apply_random_skip(args.trades, args.skip_fraction, args.seed)
        )
        return {
            "ok": True,
            "skip_fraction": args.skip_fraction,
            "seed": args.seed,
            **_summarize(original, degraded),
        }

    @mcp.tool(
        description=(
            "Synthetic worst-case streak: take the streak_length worst trades and "
            "inject them consecutively at the start. Reports the resulting "
            "drawdown — an upper bound on what the strategy might have seen."
        )
    )
    async def stress_worst_case_dd(args: WorstStreakArgs) -> dict:
        err = _validate_trades_or_error(args.trades)
        if err:
            return err
        original = _metrics(args.trades)
        degraded = _metrics(_apply_worst_streak(args.trades, args.streak_length))
        return {
            "ok": True,
            "streak_length": args.streak_length,
            **_summarize(original, degraded),
            "original_max_dd": original.get("max_drawdown_abs"),
            "stress_max_dd": degraded.get("max_drawdown_abs"),
        }

    @mcp.tool(
        description=(
            "Run all stress scenarios (slippage, skip-best, random-skip, worst-"
            "streak) and report a combined verdict: robust / moderate / fragile "
            "based on how many scenarios kill the strategy. Read-only."
        )
    )
    async def stress_combined_scenarios(args: CombinedArgs) -> dict:
        err = _validate_trades_or_error(args.trades)
        if err:
            return err
        return {"ok": True, **_combined(args)}
