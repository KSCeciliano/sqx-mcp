"""Information-theoretic measures: entropy, KL divergence, mutual information.

Tools to quantify the *distinctiveness* of a strategy's return distribution,
or to compare two strategies / regimes by how much information they share.

Tools:

- ``info_shannon_entropy`` — entropy of a discretized series in bits.
  High entropy = unpredictable; low = concentrated mass.
- ``info_kl_divergence`` — Kullback-Leibler divergence between two
  empirical distributions. D_KL(P‖Q) = 0 iff identical.
- ``info_mutual_information`` — mutual information between two series
  (joint vs marginal distributions). Captures non-linear dependence
  that correlation misses.
- ``info_transfer_entropy`` — directional information flow from
  series X to series Y at lag k. Detects predictive relationships.
- ``info_diversity_index`` — for a set of strategies, average pairwise
  Jensen-Shannon distance — high = diversified, low = concentrated.

Pure Python. Histograms use equal-width binning.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)


class EntropyArgs(BaseModel):
    series: list[float] = Field(..., min_length=10, max_length=500_000)
    n_bins: int = Field(20, ge=2, le=1000)
    base: float = Field(2.0, gt=1.0, le=10.0)


class TwoSeriesArgs(BaseModel):
    series_a: list[float] = Field(..., min_length=10, max_length=500_000)
    series_b: list[float] = Field(..., min_length=10, max_length=500_000)
    n_bins: int = Field(20, ge=2, le=1000)


class TransferEntropyArgs(BaseModel):
    source: list[float] = Field(..., min_length=20, max_length=500_000)
    target: list[float] = Field(..., min_length=20, max_length=500_000)
    lag: int = Field(1, ge=1, le=100)
    n_bins: int = Field(8, ge=2, le=100)


class DiversityArgs(BaseModel):
    series_by_name: dict[str, list[float]] = Field(
        ..., min_length=2, max_length=200
    )
    n_bins: int = Field(20, ge=2, le=1000)


def _histogram(xs: list[float], n_bins: int) -> tuple[list[float], float, float]:
    """Return (probabilities, lo, bin_width)."""
    lo = min(xs)
    hi = max(xs)
    if hi == lo:
        # Degenerate — all mass in one bin
        probs = [0.0] * n_bins
        probs[0] = 1.0
        return probs, lo, 1.0
    width = (hi - lo) / n_bins
    counts = [0] * n_bins
    for x in xs:
        idx = int((x - lo) / width)
        if idx >= n_bins:
            idx = n_bins - 1
        counts[idx] += 1
    total = sum(counts)
    return [c / total for c in counts], lo, width


def _entropy_of(probs: list[float], base: float) -> float:
    log_base = math.log(base)
    s = 0.0
    for p in probs:
        if p > 0:
            s -= p * math.log(p) / log_base
    return s


def _shannon_entropy(xs: list[float], n_bins: int, base: float) -> dict[str, Any]:
    validate_finite_floats(xs, name="series")
    probs, _lo, _w = _histogram(xs, n_bins)
    H = _entropy_of(probs, base)
    H_max = math.log(n_bins) / math.log(base)
    return {
        "entropy": round(H, 6),
        "max_entropy": round(H_max, 6),
        "normalized_entropy": round(H / H_max, 6) if H_max > 0 else 0.0,
        "base": base,
        "n_bins": n_bins,
        "n_samples": len(xs),
        "interpretation": (
            "uniform" if H / H_max > 0.95
            else "diffuse" if H / H_max > 0.7
            else "moderately_concentrated" if H / H_max > 0.4
            else "concentrated"
        ),
    }


def _kl_divergence(a: list[float], b: list[float], n_bins: int) -> dict[str, Any]:
    validate_finite_floats(a, name="series_a")
    validate_finite_floats(b, name="series_b")
    # Use the union range so both histograms share bins
    lo = min(min(a), min(b))
    hi = max(max(a), max(b))
    if hi == lo:
        return {"kl_divergence": 0.0, "note": "both series degenerate"}
    width = (hi - lo) / n_bins
    counts_a = [0] * n_bins
    counts_b = [0] * n_bins
    for x in a:
        idx = min(int((x - lo) / width), n_bins - 1)
        counts_a[idx] += 1
    for x in b:
        idx = min(int((x - lo) / width), n_bins - 1)
        counts_b[idx] += 1
    # Laplace smoothing to avoid log(0)
    eps = 1e-12
    p = [(c + eps) / (sum(counts_a) + n_bins * eps) for c in counts_a]
    q = [(c + eps) / (sum(counts_b) + n_bins * eps) for c in counts_b]
    kl = sum(p[i] * math.log(p[i] / q[i]) for i in range(n_bins))
    return {
        "kl_divergence": round(kl, 6),
        "kl_divergence_nats": round(kl, 6),
        "n_bins": n_bins,
        "interpretation": (
            "near_identical" if kl < 0.05
            else "similar" if kl < 0.2
            else "different" if kl < 1.0
            else "very_different"
        ),
    }


def _mutual_information(
    a: list[float], b: list[float], n_bins: int
) -> dict[str, Any]:
    validate_finite_floats(a, name="series_a")
    validate_finite_floats(b, name="series_b")
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    lo_a, hi_a = min(a), max(a)
    lo_b, hi_b = min(b), max(b)
    if hi_a == lo_a or hi_b == lo_b:
        return {"mutual_information": 0.0, "note": "degenerate series"}
    w_a = (hi_a - lo_a) / n_bins
    w_b = (hi_b - lo_b) / n_bins
    joint = [[0] * n_bins for _ in range(n_bins)]
    marg_a = [0] * n_bins
    marg_b = [0] * n_bins
    for i in range(n):
        ia = min(int((a[i] - lo_a) / w_a), n_bins - 1)
        ib = min(int((b[i] - lo_b) / w_b), n_bins - 1)
        joint[ia][ib] += 1
        marg_a[ia] += 1
        marg_b[ib] += 1
    mi = 0.0
    for ia in range(n_bins):
        for ib in range(n_bins):
            if joint[ia][ib] == 0 or marg_a[ia] == 0 or marg_b[ib] == 0:
                continue
            p_ab = joint[ia][ib] / n
            p_a = marg_a[ia] / n
            p_b = marg_b[ib] / n
            mi += p_ab * math.log(p_ab / (p_a * p_b))
    return {
        "mutual_information": round(mi, 6),
        "mi_nats": round(mi, 6),
        "n_bins": n_bins,
        "n_samples": n,
        "interpretation": (
            "independent" if mi < 0.05
            else "weakly_dependent" if mi < 0.2
            else "dependent" if mi < 1.0
            else "strongly_dependent"
        ),
    }


def _transfer_entropy(
    source: list[float], target: list[float], lag: int, n_bins: int
) -> dict[str, Any]:
    """Schreiber's transfer entropy from source to target at given lag.

    TE_{X→Y} = H(Y_{t+1} | Y_t) - H(Y_{t+1} | Y_t, X_t)
    """
    validate_finite_floats(source, name="source")
    validate_finite_floats(target, name="target")
    n = min(len(source), len(target)) - lag
    if n < 30:
        return {"transfer_entropy": None, "note": "insufficient overlap"}
    # Discretize all three: source lag, target lag, target future
    src_lag = source[:n]
    tgt_lag = target[:n]
    tgt_fut = target[lag : lag + n]

    def _digitize(xs: list[float]) -> list[int]:
        lo, hi = min(xs), max(xs)
        if hi == lo:
            return [0] * len(xs)
        w = (hi - lo) / n_bins
        return [min(int((x - lo) / w), n_bins - 1) for x in xs]

    s_idx = _digitize(src_lag)
    tl_idx = _digitize(tgt_lag)
    tf_idx = _digitize(tgt_fut)

    # Build joint distributions
    p_y1y0x0: dict[tuple[int, int, int], int] = {}
    p_y0x0: dict[tuple[int, int], int] = {}
    p_y1y0: dict[tuple[int, int], int] = {}
    p_y0: dict[int, int] = {}
    for i in range(n):
        a = (tf_idx[i], tl_idx[i], s_idx[i])
        b = (tl_idx[i], s_idx[i])
        c = (tf_idx[i], tl_idx[i])
        p_y1y0x0[a] = p_y1y0x0.get(a, 0) + 1
        p_y0x0[b] = p_y0x0.get(b, 0) + 1
        p_y1y0[c] = p_y1y0.get(c, 0) + 1
        p_y0[tl_idx[i]] = p_y0.get(tl_idx[i], 0) + 1

    te = 0.0
    for (y1, y0, x0), cnt in p_y1y0x0.items():
        joint = cnt / n
        cond_with = cnt / p_y0x0[(y0, x0)] if p_y0x0.get((y0, x0)) else 0
        cond_without = p_y1y0.get((y1, y0), 0) / p_y0.get(y0, 1) if p_y0.get(y0) else 0
        if cond_with > 0 and cond_without > 0:
            te += joint * math.log(cond_with / cond_without)
    return {
        "transfer_entropy": round(te, 6),
        "direction": "source_to_target",
        "lag": lag,
        "n_bins": n_bins,
        "n_samples": n,
        "interpretation": (
            "no_information_flow" if te < 0.01
            else "weak_information_flow" if te < 0.05
            else "moderate_information_flow" if te < 0.2
            else "strong_information_flow"
        ),
    }


def _diversity_index(
    series_by_name: dict[str, list[float]], n_bins: int
) -> dict[str, Any]:
    for k, v in series_by_name.items():
        validate_finite_floats(v, name=f"series[{k!r}]")
    names = sorted(series_by_name.keys())
    n_strategies = len(names)
    pairs: list[dict[str, Any]] = []
    distances: list[float] = []
    for i in range(n_strategies):
        for j in range(i + 1, n_strategies):
            a = series_by_name[names[i]]
            b = series_by_name[names[j]]
            kl_ab = _kl_divergence(a, b, n_bins)["kl_divergence"]
            kl_ba = _kl_divergence(b, a, n_bins)["kl_divergence"]
            js = 0.5 * (kl_ab + kl_ba)  # Jensen-Shannon-like (symmetric)
            distances.append(js)
            pairs.append({"a": names[i], "b": names[j], "js_distance": round(js, 6)})
    mean_d = sum(distances) / len(distances) if distances else 0.0
    return {
        "mean_pairwise_distance": round(mean_d, 6),
        "n_strategies": n_strategies,
        "n_pairs": len(pairs),
        "pairs": pairs[:50],
        "verdict": (
            "highly_diversified" if mean_d > 1.0
            else "moderately_diversified" if mean_d > 0.3
            else "concentrated" if mean_d > 0.1
            else "near_duplicates"
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Shannon entropy of a discretized series in bits (or other base). "
            "High entropy = uniform / unpredictable; low = concentrated mass. "
            "Returns normalized entropy ∈ [0, 1] for direct interpretation."
        )
    )
    async def info_shannon_entropy(args: EntropyArgs) -> dict:
        try:
            return {"ok": True, **_shannon_entropy(args.series, args.n_bins, args.base)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Kullback-Leibler divergence between two empirical distributions. "
            "D_KL(P‖Q) = 0 iff P ≡ Q. Use to compare strategy return "
            "distributions or live-vs-backtest distributions. Laplace-smoothed "
            "to avoid log(0)."
        )
    )
    async def info_kl_divergence(args: TwoSeriesArgs) -> dict:
        try:
            return {"ok": True, **_kl_divergence(args.series_a, args.series_b, args.n_bins)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Mutual information between two series. Captures non-linear "
            "dependence (which Pearson correlation misses). 0 = independent; "
            ">0 = dependent. Useful for spotting hidden relationships between "
            "strategies."
        )
    )
    async def info_mutual_information(args: TwoSeriesArgs) -> dict:
        try:
            return {"ok": True, **_mutual_information(args.series_a, args.series_b, args.n_bins)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Schreiber's transfer entropy from source to target at given lag. "
            "Detects directional information flow that correlation cannot. "
            "Use to test 'does X predict Y k bars later?'"
        )
    )
    async def info_transfer_entropy(args: TransferEntropyArgs) -> dict:
        try:
            return {
                "ok": True,
                **_transfer_entropy(args.source, args.target, args.lag, args.n_bins),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Mean pairwise Jensen-Shannon-like distance across a portfolio of "
            "strategies. Verdict: highly_diversified (>1.0), "
            "moderately_diversified (0.3-1.0), concentrated (0.1-0.3), "
            "near_duplicates (<0.1). Use to audit a portfolio for "
            "redundancy at the distribution level."
        )
    )
    async def info_diversity_index(args: DiversityArgs) -> dict:
        try:
            return {"ok": True, **_diversity_index(args.series_by_name, args.n_bins)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "DiversityArgs",
    "EntropyArgs",
    "TransferEntropyArgs",
    "TwoSeriesArgs",
    "_diversity_index",
    "_entropy_of",
    "_histogram",
    "_kl_divergence",
    "_mutual_information",
    "_shannon_entropy",
    "_transfer_entropy",
    "register",
]
