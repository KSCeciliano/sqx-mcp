"""Hansen's Superior Predictive Ability (SPA) test and White's Reality Check.

When you pick the best of N candidate strategies, the "best" is biased
upward by data-snooping. Standard significance tests don't correct for
this. Hansen's SPA (2005) and White's Reality Check (2000) use the
stationary bootstrap to compute a multiple-testing-corrected p-value for
the null "no strategy beats the benchmark."

Tools:

- ``spa_test_reality_check`` — White's simpler variant: bootstrap the
  max excess return across strategies, p-value = fraction of bootstrap
  maxes exceeding the observed max.
- ``spa_test_hansen`` — Hansen's studentized SPA: improves on Reality
  Check by studentizing each strategy's excess return; less sensitive
  to dispersion across irrelevant strategies.
- ``spa_test_consistency`` — sanity check: report the per-strategy
  bootstrap distribution alongside the SPA p-value.

Inputs: a matrix of (strategy_returns × time) plus a benchmark return
series of the same length. All pure Python.
"""

from __future__ import annotations

import math
import random
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)


class SpaArgs(BaseModel):
    strategy_returns: dict[str, list[float]] = Field(
        ..., description="Map of strategy name → return series (all same length)"
    )
    benchmark_returns: list[float] = Field(..., min_length=10, max_length=200_000)
    block_length: float = Field(5.0, gt=1.0, le=1000.0)
    n_bootstrap: int = Field(1000, ge=100, le=50_000)
    seed: int | None = None


def _validate_matrix(
    strategies: dict[str, list[float]], benchmark: list[float]
) -> tuple[list[str], list[list[float]], int]:
    if not strategies:
        raise NumericValidationError("strategy_returns is empty")
    validate_finite_floats(benchmark, name="benchmark_returns")
    names = sorted(strategies.keys())
    n = len(benchmark)
    matrix: list[list[float]] = []
    for k in names:
        s = strategies[k]
        validate_finite_floats(s, name=f"strategy_returns[{k!r}]")
        if len(s) != n:
            raise NumericValidationError(
                f"strategy {k!r} length {len(s)} != benchmark length {n}"
            )
        matrix.append(s)
    return names, matrix, n


def _excess(matrix: list[list[float]], benchmark: list[float]) -> list[list[float]]:
    return [
        [matrix[i][t] - benchmark[t] for t in range(len(benchmark))]
        for i in range(len(matrix))
    ]


def _block_indices(n: int, block_length: float, rng: random.Random) -> list[int]:
    """Draw n indices via Politis-Romano stationary bootstrap."""
    p = 1.0 / block_length
    out: list[int] = []
    idx = rng.randrange(n)
    for _ in range(n):
        out.append(idx)
        if rng.random() < p:
            idx = rng.randrange(n)
        else:
            idx = (idx + 1) % n
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _reality_check(
    strategies: dict[str, list[float]],
    benchmark: list[float],
    block_length: float,
    n_boot: int,
    seed: int | None,
) -> dict[str, Any]:
    names, matrix, n = _validate_matrix(strategies, benchmark)
    excess = _excess(matrix, benchmark)
    observed_means = [_mean(e) for e in excess]
    observed_max = max(observed_means)
    rng = random.Random(seed)
    boot_maxes: list[float] = []
    for _ in range(n_boot):
        idx = _block_indices(n, block_length, rng)
        # For each strategy, compute the bootstrap mean of (excess - observed_mean)
        # to center under the null. Then take the max across strategies.
        means_under_null = [
            sum(excess[i][t] - observed_means[i] for t in idx) / n
            for i in range(len(excess))
        ]
        boot_maxes.append(max(means_under_null))
    boot_maxes.sort()
    # p-value: fraction of bootstrap maxes >= observed max
    p_value = sum(1 for v in boot_maxes if v >= observed_max) / n_boot
    return {
        "test": "white_reality_check",
        "n_strategies": len(names),
        "observed_max_excess_return": round(observed_max, 6),
        "p_value": round(p_value, 6),
        "verdict": (
            "significant" if p_value < 0.05
            else "marginal" if p_value < 0.10
            else "not_significant"
        ),
        "best_strategy": names[observed_means.index(observed_max)],
        "n_bootstrap": n_boot,
        "block_length": block_length,
        "interpretation": (
            "After correcting for multiple-testing, the best strategy's "
            "excess return is statistically significant — it likely beats "
            "the benchmark for real."
        ) if p_value < 0.05 else (
            "After correcting for multiple-testing, even the best strategy "
            "is consistent with data-snooping luck — do not assume it beats "
            "the benchmark."
        ),
    }


def _hansen_spa(
    strategies: dict[str, list[float]],
    benchmark: list[float],
    block_length: float,
    n_boot: int,
    seed: int | None,
) -> dict[str, Any]:
    names, matrix, n = _validate_matrix(strategies, benchmark)
    excess = _excess(matrix, benchmark)
    observed_means = [_mean(e) for e in excess]
    # Per-strategy stddev under the null (sample stddev of excess)
    stds = []
    for e in excess:
        m = _mean(e)
        var = sum((x - m) ** 2 for x in e) / max(1, n - 1)
        stds.append(math.sqrt(max(var, 1e-30)))
    # Studentized statistics
    observed_stats = [
        observed_means[i] / stds[i] * math.sqrt(n) for i in range(len(observed_means))
    ]
    observed_max_stat = max(observed_stats)
    rng = random.Random(seed)
    boot_max_stats: list[float] = []
    for _ in range(n_boot):
        idx = _block_indices(n, block_length, rng)
        boot_stats = []
        for i in range(len(excess)):
            boot_mean = sum(excess[i][t] - observed_means[i] for t in idx) / n
            boot_stats.append(boot_mean / stds[i] * math.sqrt(n))
        boot_max_stats.append(max(boot_stats))
    boot_max_stats.sort()
    p_value = sum(1 for v in boot_max_stats if v >= observed_max_stat) / n_boot
    return {
        "test": "hansen_spa",
        "n_strategies": len(names),
        "observed_max_studentized_stat": round(observed_max_stat, 6),
        "observed_means": dict(zip(names, [round(m, 6) for m in observed_means], strict=False)),
        "p_value": round(p_value, 6),
        "verdict": (
            "significant" if p_value < 0.05
            else "marginal" if p_value < 0.10
            else "not_significant"
        ),
        "best_strategy": names[observed_stats.index(observed_max_stat)],
        "n_bootstrap": n_boot,
        "block_length": block_length,
    }


def _spa_consistency(
    strategies: dict[str, list[float]],
    benchmark: list[float],
    block_length: float,
    n_boot: int,
    seed: int | None,
) -> dict[str, Any]:
    names, matrix, n = _validate_matrix(strategies, benchmark)
    excess = _excess(matrix, benchmark)
    observed = [_mean(e) for e in excess]
    rng = random.Random(seed)
    # Per-strategy bootstrap distribution: how often each strategy is "best"
    win_counts = [0] * len(names)
    for _ in range(n_boot):
        idx = _block_indices(n, block_length, rng)
        means = [sum(excess[i][t] for t in idx) / n for i in range(len(excess))]
        winner = means.index(max(means))
        win_counts[winner] += 1
    return {
        "test": "spa_consistency",
        "strategies": [
            {
                "name": names[i],
                "observed_mean_excess": round(observed[i], 6),
                "win_share_pct": round(100.0 * win_counts[i] / n_boot, 4),
            }
            for i in range(len(names))
        ],
        "n_bootstrap": n_boot,
        "most_consistent_winner": names[win_counts.index(max(win_counts))],
        "interpretation": (
            "Win share = how often each strategy wins the bootstrap. A "
            "consistent winner (>50%) is robustly best; if the win share is "
            "spread thin, no strategy stands out."
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "White's Reality Check: bootstrap-corrected p-value for the null "
            "'no strategy beats the benchmark.' Uses the stationary bootstrap "
            "to handle autocorrelated returns. Correct for data-snooping when "
            "picking the best of N strategies."
        )
    )
    async def spa_test_reality_check(args: SpaArgs) -> dict:
        try:
            return {
                "ok": True,
                **_reality_check(
                    args.strategy_returns,
                    args.benchmark_returns,
                    args.block_length,
                    args.n_bootstrap,
                    args.seed,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Hansen's SPA test (2005): improved Reality Check that "
            "studentizes each strategy's excess return. Less sensitive than "
            "the basic Reality Check to dispersion across irrelevant "
            "strategies."
        )
    )
    async def spa_test_hansen(args: SpaArgs) -> dict:
        try:
            return {
                "ok": True,
                **_hansen_spa(
                    args.strategy_returns,
                    args.benchmark_returns,
                    args.block_length,
                    args.n_bootstrap,
                    args.seed,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Bootstrap consistency report: for each strategy, what fraction "
            "of bootstrap samples is it the best performer? Use as a sanity "
            "check alongside spa_test_reality_check — a 'best' strategy with "
            "low win share is suspicious."
        )
    )
    async def spa_test_consistency(args: SpaArgs) -> dict:
        try:
            return {
                "ok": True,
                **_spa_consistency(
                    args.strategy_returns,
                    args.benchmark_returns,
                    args.block_length,
                    args.n_bootstrap,
                    args.seed,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "SpaArgs",
    "_block_indices",
    "_excess",
    "_hansen_spa",
    "_reality_check",
    "_spa_consistency",
    "register",
]
