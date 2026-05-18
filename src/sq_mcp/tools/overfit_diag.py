"""Overfitting diagnostics: Deflated Sharpe, PBO, Haircut Sharpe.

Tools that try to penalize Sharpe ratios for the number of trials run
(implicit multiple testing). The math is from Bailey & Lopez de Prado.

Tools:

- ``overfit_deflated_sharpe`` — DSR estimates the probability that the
  observed Sharpe is genuinely above a threshold given N trials, skewness
  and kurtosis of returns.
- ``overfit_haircut_sharpe`` — A simpler haircut: subtract a penalty
  proportional to sqrt(log(N_trials) / T_periods) from the observed
  Sharpe.
- ``overfit_pbo_from_oos_pairs`` — Probability of Backtest Overfitting
  from pairs of (in-sample Sharpe, out-of-sample Sharpe). PBO = fraction
  of strategies where the in-sample best became out-of-sample
  below-median.
- ``overfit_min_track_record_length`` — Minimum number of observations
  needed to claim a strategy's Sharpe is statistically above a benchmark.

Read-only. Pure math.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp.tools._numerics import is_finite
from sq_mcp.tools.tail_risk import _inv_norm_cdf


class DeflatedSharpeArgs(BaseModel):
    sharpe_observed: float
    n_trials: int = Field(..., ge=1, le=1_000_000)
    n_observations: int = Field(..., ge=10, le=1_000_000)
    skewness: float = 0.0
    kurtosis: float = 3.0  # excess kurtosis of 0 = Normal; raw kurtosis of 3 = Normal
    benchmark_sharpe: float = 0.0

    @field_validator("sharpe_observed", "skewness", "kurtosis", "benchmark_sharpe")
    @classmethod
    def _v_finite(cls, v: float) -> float:
        if not is_finite(v):
            raise ValueError("must be a finite number (no NaN / Inf)")
        return v


class HaircutSharpeArgs(BaseModel):
    sharpe_observed: float
    n_trials: int = Field(..., ge=1, le=1_000_000)
    n_observations: int = Field(..., ge=10, le=1_000_000)


class PBOPair(BaseModel):
    is_sharpe: float
    oos_sharpe: float

    @field_validator("is_sharpe", "oos_sharpe")
    @classmethod
    def _v_finite(cls, v: float) -> float:
        if not is_finite(v):
            raise ValueError("must be a finite number (no NaN / Inf)")
        return v


class PBOArgs(BaseModel):
    pairs: list[PBOPair] = Field(..., min_length=4, max_length=10_000)


class MinTrackRecordArgs(BaseModel):
    sharpe_observed: float
    benchmark_sharpe: float = 0.0
    skewness: float = 0.0
    kurtosis: float = 3.0
    confidence: float = Field(0.95, gt=0.5, lt=1.0)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _euler_mascheroni() -> float:
    return 0.5772156649015329


def _expected_max_sharpe(n_trials: int) -> float:
    """Expected value of the maximum of N i.i.d. standard normals."""
    n = max(2, n_trials)
    gamma = _euler_mascheroni()
    return (1.0 - gamma) * _inv_norm_cdf(1.0 - 1.0 / n) + gamma * _inv_norm_cdf(
        1.0 - 1.0 / (n * math.e)
    )


def _dsr(
    sharpe_observed: float,
    *,
    n_trials: int,
    n_observations: int,
    skewness: float,
    kurtosis: float,
    benchmark_sharpe: float,
) -> dict[str, Any]:
    """Deflated Sharpe Ratio probability (Bailey/Lopez de Prado)."""
    if n_observations < 10:
        return {"dsr_probability": None, "note": "need ≥10 observations"}
    sr0 = _expected_max_sharpe(n_trials)
    # Variance of sample Sharpe estimator (per Mertens 2002)
    var_sr = (
        1.0
        - skewness * sharpe_observed
        + (kurtosis - 1.0) / 4.0 * sharpe_observed ** 2
    ) / (n_observations - 1)
    if var_sr <= 0:
        return {"dsr_probability": None, "note": "Sharpe variance non-positive"}
    z = (sharpe_observed - benchmark_sharpe - sr0) / math.sqrt(var_sr)
    prob = _norm_cdf(z)
    return {
        "dsr_probability": round(prob, 6),
        "deflation_threshold": round(sr0, 6),
        "z_score": round(z, 6),
        "n_trials": n_trials,
        "n_observations": n_observations,
        "interpretation": (
            "very robust" if prob > 0.95
            else "robust" if prob > 0.80
            else "borderline" if prob > 0.60
            else "likely overfit"
        ),
    }


def _haircut_sharpe(
    sharpe_observed: float, *, n_trials: int, n_observations: int
) -> dict[str, Any]:
    """Simpler heuristic haircut for multiple-testing penalty."""
    penalty = math.sqrt(math.log(max(1, n_trials)) / n_observations)
    haircut = sharpe_observed - penalty
    return {
        "sharpe_observed": round(sharpe_observed, 6),
        "haircut_amount": round(penalty, 6),
        "sharpe_haircut": round(haircut, 6),
        "n_trials": n_trials,
        "n_observations": n_observations,
        "verdict": (
            "robust" if haircut > 1.0
            else "acceptable" if haircut > 0.5
            else "marginal" if haircut > 0
            else "negative_after_haircut"
        ),
    }


def _pbo(pairs: list[dict[str, float]]) -> dict[str, Any]:
    """Probability of Backtest Overfitting from (IS, OOS) Sharpe pairs."""
    n = len(pairs)
    if n < 4:
        return {"pbo": None, "note": "need ≥4 (IS, OOS) pairs"}
    sorted_pairs = sorted(pairs, key=lambda p: p["is_sharpe"], reverse=True)
    # OOS performance of the in-sample best
    top_oos = sorted_pairs[0]["oos_sharpe"]
    oos_values = sorted([p["oos_sharpe"] for p in pairs])
    median_oos = oos_values[n // 2]
    # PBO ≈ fraction of times the in-sample best is below the OOS median
    # Estimate via paired comparison
    overfit_count = 0
    for p in pairs:
        if p["is_sharpe"] >= sorted_pairs[0]["is_sharpe"] - 1e-9:
            if p["oos_sharpe"] < median_oos:
                overfit_count += 1
    # For a single-best comparison we just check the top one
    is_overfit = top_oos < median_oos
    pbo_score = float(is_overfit)
    return {
        "pbo": pbo_score,
        "top_is_sharpe": sorted_pairs[0]["is_sharpe"],
        "top_oos_sharpe": top_oos,
        "median_oos_sharpe": median_oos,
        "is_overfit": is_overfit,
        "n_strategies": n,
        "interpretation": "overfit" if is_overfit else "consistent",
    }


def _min_track_record(
    sharpe_observed: float,
    *,
    benchmark_sharpe: float,
    skewness: float,
    kurtosis: float,
    confidence: float,
) -> dict[str, Any]:
    """Minimum sample size to claim Sharpe > benchmark at the given confidence."""
    z = _inv_norm_cdf(confidence)
    diff = sharpe_observed - benchmark_sharpe
    if diff <= 0:
        return {
            "min_track_record_length": None,
            "note": "observed Sharpe not above benchmark — cannot claim",
        }
    var_factor = 1.0 - skewness * sharpe_observed + (kurtosis - 1.0) / 4.0 * sharpe_observed ** 2
    if var_factor <= 0:
        return {"min_track_record_length": None, "note": "variance term non-positive"}
    min_t = 1.0 + var_factor * (z / diff) ** 2
    return {
        "min_track_record_length": math.ceil(min_t),
        "sharpe_observed": sharpe_observed,
        "benchmark_sharpe": benchmark_sharpe,
        "confidence": confidence,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Deflated Sharpe Ratio probability (Bailey/Lopez de Prado). Estimates "
            "the probability that the observed Sharpe is genuinely above a "
            "benchmark given N trials, sample size, and return skew/kurtosis. "
            ">0.95 = very robust; <0.60 = likely overfit. Read-only."
        )
    )
    async def overfit_deflated_sharpe(args: DeflatedSharpeArgs) -> dict:
        return {
            "ok": True,
            **_dsr(
                args.sharpe_observed,
                n_trials=args.n_trials,
                n_observations=args.n_observations,
                skewness=args.skewness,
                kurtosis=args.kurtosis,
                benchmark_sharpe=args.benchmark_sharpe,
            ),
        }

    @mcp.tool(
        description=(
            "Simpler heuristic Sharpe haircut: subtract sqrt(log(N_trials) / "
            "N_observations) from the observed Sharpe. Use when you don't have "
            "skew/kurt — DSR is the rigorous version. Read-only."
        )
    )
    async def overfit_haircut_sharpe(args: HaircutSharpeArgs) -> dict:
        return {
            "ok": True,
            **_haircut_sharpe(
                args.sharpe_observed,
                n_trials=args.n_trials,
                n_observations=args.n_observations,
            ),
        }

    @mcp.tool(
        description=(
            "Probability of Backtest Overfitting from a list of (in-sample, "
            "out-of-sample) Sharpe pairs. The in-sample best is overfit if its "
            "OOS performance falls below the OOS median. Read-only."
        )
    )
    async def overfit_pbo_from_oos_pairs(args: PBOArgs) -> dict:
        return {
            "ok": True,
            **_pbo([p.model_dump() for p in args.pairs]),
        }

    @mcp.tool(
        description=(
            "Minimum number of observations needed to claim observed Sharpe is "
            "statistically above benchmark at the given confidence. Returns "
            "None if observed Sharpe is below benchmark. Read-only."
        )
    )
    async def overfit_min_track_record_length(args: MinTrackRecordArgs) -> dict:
        return {
            "ok": True,
            **_min_track_record(
                args.sharpe_observed,
                benchmark_sharpe=args.benchmark_sharpe,
                skewness=args.skewness,
                kurtosis=args.kurtosis,
                confidence=args.confidence,
            ),
        }


__all__ = [
    "DeflatedSharpeArgs",
    "HaircutSharpeArgs",
    "MinTrackRecordArgs",
    "PBOArgs",
    "PBOPair",
    "_dsr",
    "_expected_max_sharpe",
    "_haircut_sharpe",
    "_min_track_record",
    "_norm_cdf",
    "_pbo",
    "register",
]
