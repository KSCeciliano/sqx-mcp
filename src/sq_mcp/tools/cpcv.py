"""Combinatorial Purged Cross-Validation (CPCV).

Standard walk-forward gives one OOS path: train [0..k], test [k..k+N],
roll. CPCV (Lopez de Prado, AFML Ch.7) generalizes to: pick N groups,
choose k of them as test groups, train on the remaining N-k. Repeat for
every combination. With proper purging (remove training labels that
overlap test indices) and embargo (skip a buffer after each test group),
this produces ``C(N, k)`` distinct OOS paths instead of one.

Tools:

- ``cpcv_path_count`` — return how many OOS paths CPCV will generate for
  given (N, k). Pure combinatorics.
- ``cpcv_generate_splits`` — emit the full schedule of (train_indices,
  test_indices) tuples after purging and embargoing.
- ``cpcv_summarize_paths`` — given an N×k Sharpe matrix (one Sharpe per
  test group per path), summarize: distribution of OOS Sharpes, fraction
  of paths better than a benchmark, worst-path performance.

Pure Python. No sklearn. Suitable for evaluating strategies that produce
per-fold OOS metrics.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    percentile,
    validate_finite_floats,
)


class PathCountArgs(BaseModel):
    n_groups: int = Field(..., ge=2, le=100)
    test_groups: int = Field(..., ge=1, le=50)


class GenerateSplitsArgs(BaseModel):
    n_samples: int = Field(..., ge=10, le=10_000_000)
    n_groups: int = Field(..., ge=2, le=100)
    test_groups: int = Field(..., ge=1, le=50)
    embargo_pct: float = Field(0.01, ge=0.0, le=0.5)


class SummarizePathsArgs(BaseModel):
    oos_sharpes: list[float] = Field(..., min_length=2, max_length=100_000)
    benchmark_sharpe: float = 0.0


def _path_count(n: int, k: int) -> dict[str, Any]:
    if k >= n:
        return {"n_paths": 0, "n_test_groups_per_path": 0, "note": "k must be < n"}
    # Number of paths through the CPCV: total combos × (k/n)
    total_combos = 1
    for i in range(k):
        total_combos = total_combos * (n - i) // (i + 1)
    # Each path picks k groups out of n. The number of distinct (train, test)
    # tuples = total_combos. Each produces k OOS sub-paths (one per test group).
    return {
        "n_paths": total_combos,
        "n_test_groups_per_path": k,
        "total_oos_observations": total_combos * k,
        "n_groups": n,
        "test_groups": k,
    }


def _group_boundaries(n_samples: int, n_groups: int) -> list[tuple[int, int]]:
    base = n_samples // n_groups
    extra = n_samples % n_groups
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(n_groups):
        size = base + (1 if i < extra else 0)
        out.append((start, start + size))
        start += size
    return out


def _generate_splits(
    n_samples: int, n_groups: int, test_groups: int, embargo_pct: float
) -> dict[str, Any]:
    bounds = _group_boundaries(n_samples, n_groups)
    embargo = int(embargo_pct * n_samples)
    splits: list[dict[str, Any]] = []
    for combo in combinations(range(n_groups), test_groups):
        test_idx: list[int] = []
        for g in combo:
            lo, hi = bounds[g]
            test_idx.extend(range(lo, hi))
        # Build forbidden ranges (test ranges + embargo to the right of each test group)
        forbidden: set[int] = set(test_idx)
        for g in combo:
            _lo, hi = bounds[g]
            for j in range(hi, min(hi + embargo, n_samples)):
                forbidden.add(j)
        train_idx = [i for i in range(n_samples) if i not in forbidden]
        splits.append({
            "test_groups": list(combo),
            "n_train": len(train_idx),
            "n_test": len(test_idx),
            "embargo_applied": embargo,
            "train_indices": train_idx,
            "test_indices": test_idx,
        })
    return {
        "n_paths": len(splits),
        "splits": splits,
        "group_boundaries": [{"group": i, "start": b[0], "end": b[1]} for i, b in enumerate(bounds)],
        "embargo_size": embargo,
    }


def _summarize_paths(
    oos_sharpes: list[float], benchmark: float
) -> dict[str, Any]:
    validate_finite_floats(oos_sharpes, name="oos_sharpes")
    sorted_s = sorted(oos_sharpes)
    n = len(sorted_s)
    better = sum(1 for s in sorted_s if s > benchmark)
    mean = sum(sorted_s) / n
    var = sum((s - mean) ** 2 for s in sorted_s) / max(1, n - 1)
    std = var ** 0.5
    return {
        "n_paths": n,
        "mean_oos_sharpe": round(mean, 6),
        "std_oos_sharpe": round(std, 6),
        "p5": round(percentile(sorted_s, 5), 6),
        "p25": round(percentile(sorted_s, 25), 6),
        "median": round(percentile(sorted_s, 50), 6),
        "p75": round(percentile(sorted_s, 75), 6),
        "p95": round(percentile(sorted_s, 95), 6),
        "min": round(sorted_s[0], 6),
        "max": round(sorted_s[-1], 6),
        "frac_better_than_benchmark": round(better / n, 4),
        "benchmark_sharpe": benchmark,
        "verdict": (
            "robust" if (better / n) >= 0.9 and mean - 2 * std > benchmark
            else "promising" if (better / n) >= 0.7
            else "borderline" if (better / n) >= 0.5
            else "fragile"
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Compute how many distinct out-of-sample paths Combinatorial "
            "Purged Cross-Validation generates for given (n_groups, "
            "test_groups). Pure combinatorics — no data needed."
        )
    )
    async def cpcv_path_count(args: PathCountArgs) -> dict:
        return {"ok": True, **_path_count(args.n_groups, args.test_groups)}

    @mcp.tool(
        description=(
            "Generate the full schedule of (train, test) index splits for "
            "Combinatorial Purged Cross-Validation. Embargo (default 1% of "
            "samples) is applied after each test group to prevent leakage. "
            "Suitable as input to any backtester that accepts index splits."
        )
    )
    async def cpcv_generate_splits(args: GenerateSplitsArgs) -> dict:
        return {
            "ok": True,
            **_generate_splits(
                args.n_samples, args.n_groups, args.test_groups, args.embargo_pct
            ),
        }

    @mcp.tool(
        description=(
            "Summarize a distribution of OOS Sharpe ratios (one per CPCV "
            "path) into percentiles, mean ± std, fraction beating a "
            "benchmark, and a verdict (robust / promising / borderline / "
            "fragile). Use after running cpcv_generate_splits + a backtester."
        )
    )
    async def cpcv_summarize_paths(args: SummarizePathsArgs) -> dict:
        try:
            return {
                "ok": True,
                **_summarize_paths(args.oos_sharpes, args.benchmark_sharpe),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "GenerateSplitsArgs",
    "PathCountArgs",
    "SummarizePathsArgs",
    "_generate_splits",
    "_group_boundaries",
    "_path_count",
    "_summarize_paths",
    "register",
]
