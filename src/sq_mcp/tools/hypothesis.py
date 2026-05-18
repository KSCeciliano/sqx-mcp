"""Statistical hypothesis tests for comparing two strategies.

When you have two backtests of the same strategy (or two different
strategies) and want to know whether one is *really* better — not just
"better on this sample" — these tests give a defensible answer.

Tools:

- ``hypothesis_paired_t_test`` — paired t-test on trade-by-trade
  differences. Use when the two strategies trade the same instrument
  in the same period, so trade pairs are naturally aligned.
- ``hypothesis_sign_test`` — non-parametric: counts how many paired
  trades have strategy A > strategy B. Robust to outliers.
- ``hypothesis_wilcoxon_signed_rank`` — non-parametric Wilcoxon
  signed-rank test on paired differences. Used when paired
  differences aren't normally distributed.
- ``hypothesis_unpaired_t_test`` — two-sample (Welch's) t-test when
  trades aren't naturally paired.

All use only the standard library — pure Python, no scipy.

All tests return a p-value approximation; for small sample sizes the
approximation is rough. The threshold for "significant" is usually
0.05; pass ``alpha`` to override.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# ---- argument schemas ------------------------------------------------------


class PairedArgs(BaseModel):
    strategy_a: list[float] = Field(..., min_length=5, max_length=100_000)
    strategy_b: list[float] = Field(..., min_length=5, max_length=100_000)
    alpha: float = Field(0.05, gt=0, lt=1.0)


class UnpairedArgs(BaseModel):
    strategy_a: list[float] = Field(..., min_length=5, max_length=100_000)
    strategy_b: list[float] = Field(..., min_length=5, max_length=100_000)
    alpha: float = Field(0.05, gt=0, lt=1.0)


# ---- helpers ---------------------------------------------------------------


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _var(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def _norm_cdf(z: float) -> float:
    """Standard normal CDF via erf."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _two_tailed_p_from_z(z: float) -> float:
    return 2.0 * (1.0 - _norm_cdf(abs(z)))


def _paired_t_test(
    a: list[float], b: list[float], alpha: float
) -> dict[str, Any]:
    n = min(len(a), len(b))
    diffs = [a[i] - b[i] for i in range(n)]
    mean_d = _mean(diffs)
    var_d = _var(diffs)
    if var_d == 0:
        return {
            "verdict": "no_difference",
            "n_pairs": n,
            "mean_difference": round(mean_d, 6),
            "t_statistic": None,
            "p_value": 1.0 if mean_d == 0 else 0.0,
            "alpha": alpha,
            "note": "zero variance in differences",
        }
    se = math.sqrt(var_d / n)
    t = mean_d / se
    # Approximate p-value using normal approximation (good for n > 30)
    p = _two_tailed_p_from_z(t)
    return {
        "verdict": "different" if p < alpha else "no_difference",
        "n_pairs": n,
        "mean_difference": round(mean_d, 6),
        "std_difference": round(math.sqrt(var_d), 6),
        "t_statistic": round(t, 4),
        "p_value": round(p, 6),
        "alpha": alpha,
        "interpretation": (
            f"strategy_a {'outperforms' if mean_d > 0 else 'underperforms'} strategy_b "
            f"({'significant' if p < alpha else 'not significant'} at α={alpha})"
        ),
    }


def _sign_test(a: list[float], b: list[float], alpha: float) -> dict[str, Any]:
    n = min(len(a), len(b))
    plus = sum(1 for i in range(n) if a[i] > b[i])
    minus = sum(1 for i in range(n) if a[i] < b[i])
    ties = n - plus - minus
    nonties = plus + minus
    if nonties == 0:
        return {
            "verdict": "no_difference",
            "n_pairs": n,
            "plus": 0,
            "minus": 0,
            "ties": ties,
            "p_value": 1.0,
            "alpha": alpha,
            "note": "all pairs are ties",
        }
    # Under H0, plus follows Binomial(nonties, 0.5). Use normal approximation:
    mean_h0 = nonties / 2.0
    var_h0 = nonties / 4.0
    z = (plus - mean_h0) / math.sqrt(var_h0) if var_h0 > 0 else 0
    p = _two_tailed_p_from_z(z)
    return {
        "verdict": "different" if p < alpha else "no_difference",
        "n_pairs": n,
        "plus": plus,
        "minus": minus,
        "ties": ties,
        "z_statistic": round(z, 4),
        "p_value": round(p, 6),
        "alpha": alpha,
        "interpretation": (
            f"a > b in {plus}/{nonties} non-tie pairs "
            f"({'significant' if p < alpha else 'not significant'})"
        ),
    }


def _wilcoxon_signed_rank(
    a: list[float], b: list[float], alpha: float
) -> dict[str, Any]:
    n = min(len(a), len(b))
    diffs = [a[i] - b[i] for i in range(n)]
    # Drop zeros
    nonzero = [d for d in diffs if d != 0]
    if len(nonzero) < 5:
        return {
            "verdict": "no_difference",
            "n_nonzero": len(nonzero),
            "p_value": 1.0,
            "alpha": alpha,
            "note": "need at least 5 non-zero differences",
        }
    # Rank by |diff|
    abs_diffs = sorted(enumerate(nonzero), key=lambda kv: abs(kv[1]))
    ranks = [0.0] * len(nonzero)
    i = 0
    while i < len(abs_diffs):
        j = i
        # Find ties
        while j + 1 < len(abs_diffs) and abs(abs_diffs[j + 1][1]) == abs(abs_diffs[i][1]):
            j += 1
        # Average rank
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[abs_diffs[k][0]] = avg_rank
        i = j + 1
    w_plus = sum(ranks[i] for i, d in enumerate(nonzero) if d > 0)
    w_minus = sum(ranks[i] for i, d in enumerate(nonzero) if d < 0)
    w = min(w_plus, w_minus)
    # Normal approximation: mean = n(n+1)/4, var = n(n+1)(2n+1)/24
    nn = len(nonzero)
    mean_w = nn * (nn + 1) / 4.0
    var_w = nn * (nn + 1) * (2 * nn + 1) / 24.0
    z = (w - mean_w) / math.sqrt(var_w) if var_w > 0 else 0
    p = _two_tailed_p_from_z(z)
    return {
        "verdict": "different" if p < alpha else "no_difference",
        "n_nonzero": nn,
        "w_plus": round(w_plus, 4),
        "w_minus": round(w_minus, 4),
        "z_statistic": round(z, 4),
        "p_value": round(p, 6),
        "alpha": alpha,
    }


def _welchs_t_test(
    a: list[float], b: list[float], alpha: float
) -> dict[str, Any]:
    """Two-sample Welch's t-test (unequal variances)."""
    n_a, n_b = len(a), len(b)
    if n_a < 2 or n_b < 2:
        return {"verdict": "insufficient_sample", "p_value": 1.0}
    m_a, m_b = _mean(a), _mean(b)
    v_a, v_b = _var(a), _var(b)
    if v_a == 0 and v_b == 0:
        return {
            "verdict": "no_difference" if m_a == m_b else "different",
            "p_value": 1.0 if m_a == m_b else 0.0,
            "note": "both samples have zero variance",
        }
    se = math.sqrt(v_a / n_a + v_b / n_b)
    if se == 0:
        return {"verdict": "no_difference", "p_value": 1.0}
    t = (m_a - m_b) / se
    p = _two_tailed_p_from_z(t)
    return {
        "verdict": "different" if p < alpha else "no_difference",
        "n_a": n_a,
        "n_b": n_b,
        "mean_a": round(m_a, 6),
        "mean_b": round(m_b, 6),
        "t_statistic": round(t, 4),
        "p_value": round(p, 6),
        "alpha": alpha,
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Paired t-test on per-trade differences between two strategies. Use "
            "when the strategies trade the same instrument over the same period "
            "so each trade is naturally paired. Normal approximation; tight for "
            "n > 30."
        )
    )
    async def hypothesis_paired_t_test(args: PairedArgs) -> dict:
        return {
            "ok": True,
            **_paired_t_test(args.strategy_a, args.strategy_b, args.alpha),
        }

    @mcp.tool(
        description=(
            "Non-parametric sign test on paired differences. Counts +, -, and "
            "tie pairs and runs a binomial test. Robust to outliers but less "
            "powerful than the t-test."
        )
    )
    async def hypothesis_sign_test(args: PairedArgs) -> dict:
        return {
            "ok": True,
            **_sign_test(args.strategy_a, args.strategy_b, args.alpha),
        }

    @mcp.tool(
        description=(
            "Wilcoxon signed-rank test on paired differences. Non-parametric "
            "alternative to the paired t-test that uses magnitudes (ranked). "
            "Use when differences aren't normally distributed."
        )
    )
    async def hypothesis_wilcoxon_signed_rank(args: PairedArgs) -> dict:
        return {
            "ok": True,
            **_wilcoxon_signed_rank(args.strategy_a, args.strategy_b, args.alpha),
        }

    @mcp.tool(
        description=(
            "Two-sample Welch's t-test (unequal variances) between two "
            "independent samples. Use when trades aren't naturally paired."
        )
    )
    async def hypothesis_unpaired_t_test(args: UnpairedArgs) -> dict:
        return {
            "ok": True,
            **_welchs_t_test(args.strategy_a, args.strategy_b, args.alpha),
        }
