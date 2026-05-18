"""System Parameter Permutation (SPP) — QuantAnalyzer's flagship robustness test.

SPP perturbs each strategy parameter by a small amount (±x%) over many
random combinations and reports the *distribution* of resulting metrics.
A robust strategy should still be profitable across most permutations.
A fragile strategy collapses when its parameters are nudged.

This module operates on caller-supplied *results* — the caller has run
the strategy with each parameter combination and pipes the metric series
in. The module does the distribution analysis.

Tools:

- ``spp_permutation_grid`` — generate the set of parameter combinations
  to evaluate, given a base parameter dict and a percent perturbation.
- ``spp_summarize_distribution`` — given an array of per-permutation
  metrics (e.g. Sharpes), report distributional stats + a fragility
  verdict.
- ``spp_compare_baseline`` — compare the baseline-strategy metric
  against the SPP distribution; report the percentile rank of the
  baseline and whether it sits in the top quartile.
- ``spp_recommend_params`` — given a parameter→metric mapping, find the
  parameter combination at the median metric (the "robust default" —
  what's likely to generalize, not the in-sample best).

Pure Python. No sklearn.
"""

from __future__ import annotations

import math
import random
from itertools import product
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    percentile,
    validate_finite_floats,
)


class SppGridArgs(BaseModel):
    base_params: dict[str, float] = Field(..., min_length=1, max_length=50)
    perturbation_pct: float = Field(10.0, gt=0.0, le=50.0)
    n_steps_per_param: int = Field(3, ge=2, le=10)
    n_random_samples: int | None = Field(None, ge=10, le=10_000)
    seed: int | None = None


class SppDistributionArgs(BaseModel):
    metrics: list[float] = Field(..., min_length=10, max_length=100_000)
    baseline_metric: float | None = None


class SppCompareArgs(BaseModel):
    baseline_metric: float
    distribution: list[float] = Field(..., min_length=10, max_length=100_000)


class SppRecommendArgs(BaseModel):
    permutations: list[dict[str, Any]] = Field(
        ...,
        min_length=10,
        max_length=10_000,
        description="Each entry: {'params': {...}, 'metric': float}",
    )


def _permutation_grid(
    base_params: dict[str, float],
    perturbation_pct: float,
    n_steps_per_param: int,
    n_random_samples: int | None,
    seed: int | None,
) -> dict[str, Any]:
    """Generate parameter combinations. If n_random_samples is set, draw a
    random subset (Latin-hypercube-like); otherwise enumerate the full grid.
    """
    pct = perturbation_pct / 100.0
    # Build per-parameter candidate values: linspace(low, high, n_steps)
    per_param: dict[str, list[float]] = {}
    for k, v in base_params.items():
        low = v * (1.0 - pct)
        high = v * (1.0 + pct)
        if n_steps_per_param == 1:
            per_param[k] = [v]
        else:
            step = (high - low) / (n_steps_per_param - 1)
            per_param[k] = [round(low + i * step, 8) for i in range(n_steps_per_param)]
    keys = sorted(per_param.keys())
    full_grid_size = 1
    for k in keys:
        full_grid_size *= len(per_param[k])
    combos: list[dict[str, float]] = []
    if n_random_samples is None or n_random_samples >= full_grid_size:
        for combo in product(*(per_param[k] for k in keys)):
            combos.append(dict(zip(keys, combo, strict=False)))
    else:
        rng = random.Random(seed)
        seen: set[tuple] = set()
        while len(combos) < n_random_samples and len(seen) < full_grid_size:
            picked = tuple(rng.choice(per_param[k]) for k in keys)
            if picked in seen:
                continue
            seen.add(picked)
            combos.append(dict(zip(keys, picked, strict=False)))
    return {
        "combinations": combos,
        "n_combinations": len(combos),
        "full_grid_size": full_grid_size,
        "params_perturbed": keys,
        "perturbation_pct": perturbation_pct,
    }


def _summarize_distribution(
    metrics: list[float], baseline: float | None
) -> dict[str, Any]:
    validate_finite_floats(metrics, name="metrics")
    sorted_m = sorted(metrics)
    n = len(sorted_m)
    mean = sum(sorted_m) / n
    var = sum((x - mean) ** 2 for x in sorted_m) / max(1, n - 1)
    std = math.sqrt(var)
    profitable = sum(1 for m in sorted_m if m > 0)
    # Fragility: spread of metrics relative to the mean
    cv = (std / abs(mean)) if mean != 0 else None
    result: dict[str, Any] = {
        "n_permutations": n,
        "mean": round(mean, 6),
        "std": round(std, 6),
        "min": round(sorted_m[0], 6),
        "p5": round(percentile(sorted_m, 5), 6),
        "p25": round(percentile(sorted_m, 25), 6),
        "median": round(percentile(sorted_m, 50), 6),
        "p75": round(percentile(sorted_m, 75), 6),
        "p95": round(percentile(sorted_m, 95), 6),
        "max": round(sorted_m[-1], 6),
        "profitable_share_pct": round(100.0 * profitable / n, 4),
        "coefficient_of_variation": round(cv, 4) if cv is not None else None,
        "verdict": (
            "very_robust" if profitable / n >= 0.95 and (cv is None or cv < 0.3)
            else "robust" if profitable / n >= 0.8 and (cv is None or cv < 0.5)
            else "moderately_fragile" if profitable / n >= 0.5
            else "fragile"
        ),
    }
    if baseline is not None:
        result["baseline_metric"] = baseline
        result["baseline_percentile"] = round(
            100.0 * sum(1 for m in sorted_m if m <= baseline) / n, 4
        )
        result["baseline_above_median"] = baseline > result["median"]
    return result


def _compare_baseline(baseline: float, distribution: list[float]) -> dict[str, Any]:
    validate_finite_floats(distribution, name="distribution")
    sorted_d = sorted(distribution)
    n = len(sorted_d)
    rank = sum(1 for d in sorted_d if d <= baseline)
    pct = 100.0 * rank / n
    return {
        "baseline_metric": baseline,
        "percentile_rank": round(pct, 4),
        "above_median": baseline > sorted_d[n // 2],
        "in_top_quartile": pct >= 75.0,
        "in_top_decile": pct >= 90.0,
        "interpretation": (
            "baseline is suspiciously high (likely in-sample overfit)"
            if pct >= 95.0
            else "baseline is genuinely strong"
            if pct >= 75.0
            else "baseline is middle-of-the-pack"
            if pct >= 25.0
            else "baseline is at the bottom — investigate"
        ),
    }


def _recommend_params(
    permutations: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics = [p["metric"] for p in permutations]
    validate_finite_floats(metrics, name="metrics")
    n = len(permutations)
    sorted_by_m = sorted(permutations, key=lambda p: p["metric"])
    median_perm = sorted_by_m[n // 2]
    best_perm = sorted_by_m[-1]
    worst_perm = sorted_by_m[0]
    return {
        "n_permutations": n,
        "recommended_params": median_perm["params"],
        "recommended_metric": median_perm["metric"],
        "best_in_sample_params": best_perm["params"],
        "best_in_sample_metric": best_perm["metric"],
        "worst_params": worst_perm["params"],
        "worst_metric": worst_perm["metric"],
        "explanation": (
            "The recommended parameters are at the median of the perturbed "
            "distribution — the 'typical' performance you'd expect "
            "out-of-sample. The in-sample best is shown for comparison but "
            "should not be deployed without further validation."
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Generate the parameter-permutation grid for System Parameter "
            "Permutation testing. Each parameter is perturbed ±perturbation_pct "
            "in n_steps_per_param values. Returns the full grid, or a random "
            "sample if n_random_samples is set."
        )
    )
    async def spp_permutation_grid(args: SppGridArgs) -> dict:
        return {
            "ok": True,
            **_permutation_grid(
                args.base_params,
                args.perturbation_pct,
                args.n_steps_per_param,
                args.n_random_samples,
                args.seed,
            ),
        }

    @mcp.tool(
        description=(
            "Summarize a distribution of per-permutation metrics from an SPP "
            "run. Reports percentiles + profitable_share_pct + coefficient_of_"
            "variation + verdict (very_robust / robust / moderately_fragile / "
            "fragile). Optionally compares against a baseline."
        )
    )
    async def spp_summarize_distribution(args: SppDistributionArgs) -> dict:
        try:
            return {
                "ok": True,
                **_summarize_distribution(args.metrics, args.baseline_metric),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Compare a baseline metric against the SPP distribution: report "
            "the percentile rank + whether it sits in the top quartile/decile "
            "+ a verdict. Use to detect in-sample overfit (baseline at the "
            "95th+ percentile of perturbations is suspicious)."
        )
    )
    async def spp_compare_baseline(args: SppCompareArgs) -> dict:
        try:
            return {
                "ok": True,
                **_compare_baseline(args.baseline_metric, args.distribution),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Given a list of (params, metric) entries from an SPP run, "
            "recommend the parameters at the median metric — the typical "
            "out-of-sample performance instead of the in-sample best. "
            "Reports best/worst params alongside for comparison."
        )
    )
    async def spp_recommend_params(args: SppRecommendArgs) -> dict:
        try:
            return {"ok": True, **_recommend_params(args.permutations)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "SppCompareArgs",
    "SppDistributionArgs",
    "SppGridArgs",
    "SppRecommendArgs",
    "_compare_baseline",
    "_permutation_grid",
    "_recommend_params",
    "_summarize_distribution",
    "register",
]
