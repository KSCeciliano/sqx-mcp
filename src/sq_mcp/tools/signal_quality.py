"""Signal quality diagnostics: Information Coefficient, IC decay, hit rate by quantile.

The Information Coefficient (IC) is the rank correlation between a
predictive signal and the forward return it should predict. It's the
canonical metric in quantitative equity research, but useful for any
strategy that emits a numeric signal.

Tools:

- ``signal_information_coefficient`` — Spearman rank correlation between
  signal[t] and forward_return[t+1..t+k]. Single-lag.
- ``signal_ic_decay`` — IC at multiple forward horizons (1, 2, 5, 10, …
  bars). Useful for deciding how long to hold a position.
- ``signal_hit_rate_by_quantile`` — quintile / decile sorts: rank
  signals into Q buckets and report mean forward return per bucket.
  A monotonic relationship = clean signal.
- ``signal_to_noise_ratio`` — variance of the signal divided by the
  variance of the noise it's trying to extract. Higher = cleaner.
- ``signal_turnover`` — fraction of signal that changes per bar (how
  fast the signal flips). High turnover = high transaction costs.

Pure Python. Spearman uses average ranks for ties.
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


class ICArgs(BaseModel):
    signal: list[float] = Field(..., min_length=10, max_length=500_000)
    forward_return: list[float] = Field(..., min_length=10, max_length=500_000)


class ICDecayArgs(BaseModel):
    signal: list[float] = Field(..., min_length=20, max_length=500_000)
    future_prices: list[float] = Field(..., min_length=20, max_length=500_000)
    horizons: list[int] = Field(
        default_factory=lambda: [1, 2, 5, 10, 20],
        min_length=1,
        max_length=50,
    )


class QuantileArgs(BaseModel):
    signal: list[float] = Field(..., min_length=20, max_length=500_000)
    forward_return: list[float] = Field(..., min_length=20, max_length=500_000)
    n_quantiles: int = Field(5, ge=2, le=20)


class SNRArgs(BaseModel):
    signal: list[float] = Field(..., min_length=10, max_length=500_000)
    noise: list[float] = Field(..., min_length=10, max_length=500_000)


class TurnoverArgs(BaseModel):
    signal: list[float] = Field(..., min_length=2, max_length=500_000)
    normalize: bool = True


def _ranks(xs: list[float]) -> list[float]:
    """Average-rank vector. Ties get the mean of their ranks."""
    indexed = sorted(enumerate(xs), key=lambda p: p[1])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-based
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _spearman(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    ra = _ranks(a[:n])
    rb = _ranks(b[:n])
    mean_a = sum(ra) / n
    mean_b = sum(rb) / n
    da = [x - mean_a for x in ra]
    db = [x - mean_b for x in rb]
    var_a = sum(x * x for x in da)
    var_b = sum(x * x for x in db)
    if var_a <= 0 or var_b <= 0:
        return 0.0
    return sum(da[i] * db[i] for i in range(n)) / math.sqrt(var_a * var_b)


def _information_coefficient(
    signal: list[float], forward: list[float]
) -> dict[str, Any]:
    validate_finite_floats(signal, name="signal")
    validate_finite_floats(forward, name="forward_return")
    n = min(len(signal), len(forward))
    ic = _spearman(signal[:n], forward[:n])
    # Standard t-stat for Spearman: ic * sqrt((n-2)/(1-ic²))
    if abs(ic) >= 1.0:
        t_stat = float("inf") if ic > 0 else float("-inf")
    elif n < 4:
        t_stat = 0.0
    else:
        t_stat = ic * math.sqrt((n - 2) / max(1e-9, 1.0 - ic * ic))
    return {
        "information_coefficient": round(ic, 6),
        "t_statistic": round(t_stat, 4) if math.isfinite(t_stat) else None,
        "n_samples": n,
        "verdict": (
            "very_strong_signal" if abs(ic) > 0.15
            else "strong_signal" if abs(ic) > 0.08
            else "weak_signal" if abs(ic) > 0.03
            else "noise"
        ),
        "interpretation": (
            "Equity-research convention: |IC| > 0.05 is a useful signal at "
            "scale; > 0.10 is exceptional."
        ),
    }


def _ic_decay(
    signal: list[float], prices: list[float], horizons: list[int]
) -> dict[str, Any]:
    validate_finite_floats(signal, name="signal")
    validate_finite_floats(prices, name="future_prices")
    n_signal = len(signal)
    out: list[dict[str, Any]] = []
    for h in sorted(set(horizons)):
        # Build forward returns at horizon h
        usable = min(n_signal, len(prices) - h)
        if usable < 10:
            out.append({"horizon": h, "ic": None, "note": "insufficient samples"})
            continue
        forward = [
            (prices[i + h] - prices[i]) / prices[i] if prices[i] > 0 else 0.0
            for i in range(usable)
        ]
        ic = _spearman(signal[:usable], forward)
        out.append({
            "horizon": h,
            "ic": round(ic, 6),
            "n_samples": usable,
        })
    # Find peak IC horizon
    valid = [r for r in out if r.get("ic") is not None]
    peak = max(valid, key=lambda r: abs(r["ic"])) if valid else None
    return {
        "by_horizon": out,
        "peak_horizon": peak["horizon"] if peak else None,
        "peak_ic": peak["ic"] if peak else None,
        "interpretation": (
            "The peak horizon is the natural holding period for the signal. "
            "IC dropping below 0.03 indicates the signal has decayed to noise."
        ),
    }


def _hit_rate_by_quantile(
    signal: list[float], forward: list[float], q: int
) -> dict[str, Any]:
    validate_finite_floats(signal, name="signal")
    validate_finite_floats(forward, name="forward_return")
    n = min(len(signal), len(forward))
    if n < q * 2:
        return {"buckets": [], "note": "insufficient samples for given quantiles"}
    paired = sorted(zip(signal[:n], forward[:n], strict=False), key=lambda p: p[0])
    bucket_size = n // q
    buckets: list[dict[str, Any]] = []
    for b in range(q):
        lo = b * bucket_size
        hi = (b + 1) * bucket_size if b < q - 1 else n
        sub = paired[lo:hi]
        rets = [r for _s, r in sub]
        sigs = [s for s, _r in sub]
        mean_r = sum(rets) / len(rets) if rets else 0.0
        hits = sum(1 for r in rets if r > 0)
        buckets.append({
            "quantile": b + 1,
            "n": len(sub),
            "mean_signal": round(sum(sigs) / len(sigs), 6) if sigs else 0.0,
            "mean_forward_return": round(mean_r, 8),
            "hit_rate": round(hits / len(rets), 4) if rets else 0.0,
        })
    # Monotonicity check: spread between top and bottom
    if len(buckets) >= 2:
        top = buckets[-1]["mean_forward_return"]
        bot = buckets[0]["mean_forward_return"]
        spread = top - bot
    else:
        spread = 0.0
    return {
        "buckets": buckets,
        "n_quantiles": q,
        "top_bottom_spread": round(spread, 8),
        "monotonic_increasing": all(
            buckets[i]["mean_forward_return"] <= buckets[i + 1]["mean_forward_return"]
            for i in range(len(buckets) - 1)
        ),
        "verdict": (
            "clean_signal" if spread > 0 and all(
                buckets[i]["mean_forward_return"] <= buckets[i + 1]["mean_forward_return"]
                for i in range(len(buckets) - 1)
            )
            else "noisy_but_directional" if abs(spread) > 0
            else "no_signal"
        ),
    }


def _snr(signal: list[float], noise: list[float]) -> dict[str, Any]:
    validate_finite_floats(signal, name="signal")
    validate_finite_floats(noise, name="noise")
    mean_s = sum(signal) / len(signal)
    var_s = sum((x - mean_s) ** 2 for x in signal) / max(1, len(signal) - 1)
    mean_n = sum(noise) / len(noise)
    var_n = sum((x - mean_n) ** 2 for x in noise) / max(1, len(noise) - 1)
    if var_n <= 0:
        return {"snr": None, "note": "zero noise variance"}
    snr = var_s / var_n
    snr_db = 10.0 * math.log10(max(1e-30, snr))
    return {
        "snr": round(snr, 6),
        "snr_db": round(snr_db, 4),
        "signal_variance": round(var_s, 6),
        "noise_variance": round(var_n, 6),
        "verdict": (
            "excellent" if snr_db > 10
            else "good" if snr_db > 3
            else "marginal" if snr_db > 0
            else "noise_dominated"
        ),
    }


def _turnover(signal: list[float], normalize: bool) -> dict[str, Any]:
    validate_finite_floats(signal, name="signal")
    n = len(signal)
    abs_changes = sum(abs(signal[i] - signal[i - 1]) for i in range(1, n))
    if normalize:
        # Normalize by mean absolute signal level
        avg_abs = sum(abs(s) for s in signal) / n if n > 0 else 0.0
        turnover = abs_changes / (n - 1) / max(avg_abs, 1e-12) if n > 1 else 0.0
    else:
        turnover = abs_changes / max(1, n - 1)
    return {
        "turnover": round(turnover, 6),
        "abs_changes_total": round(abs_changes, 6),
        "n_changes": n - 1,
        "normalized": normalize,
        "verdict": (
            "low_turnover" if turnover < 0.05
            else "moderate_turnover" if turnover < 0.2
            else "high_turnover" if turnover < 0.5
            else "extreme_turnover"
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Information Coefficient: Spearman rank correlation between "
            "signal[t] and forward_return[t]. Returns IC + t-statistic + "
            "verdict (very_strong / strong / weak / noise). |IC| > 0.05 is "
            "useful at scale; > 0.10 is exceptional in equity research."
        )
    )
    async def signal_information_coefficient(args: ICArgs) -> dict:
        try:
            return {"ok": True, **_information_coefficient(args.signal, args.forward_return)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "IC decay across multiple forward horizons (default 1, 2, 5, 10, "
            "20 bars). Identifies the peak horizon (natural holding period) "
            "and where the signal decays to noise (|IC| < 0.03)."
        )
    )
    async def signal_ic_decay(args: ICDecayArgs) -> dict:
        try:
            return {
                "ok": True,
                **_ic_decay(args.signal, args.future_prices, args.horizons),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Quantile sort: rank signals into Q buckets, report mean forward "
            "return + hit rate per bucket. Returns top-bottom spread and a "
            "monotonicity check. Clean signal = monotonic + positive spread."
        )
    )
    async def signal_hit_rate_by_quantile(args: QuantileArgs) -> dict:
        try:
            return {
                "ok": True,
                **_hit_rate_by_quantile(args.signal, args.forward_return, args.n_quantiles),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Signal-to-noise ratio = var(signal) / var(noise). Returns SNR "
            "in dB. Verdict: excellent (>10dB), good (3-10), marginal (0-3), "
            "noise_dominated (<0)."
        )
    )
    async def signal_to_noise_ratio(args: SNRArgs) -> dict:
        try:
            return {"ok": True, **_snr(args.signal, args.noise)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Turnover: mean absolute per-bar change in the signal, "
            "optionally normalized by the mean absolute signal level. "
            "Verdict: low / moderate / high / extreme. High turnover means "
            "high transaction costs."
        )
    )
    async def signal_turnover(args: TurnoverArgs) -> dict:
        try:
            return {"ok": True, **_turnover(args.signal, args.normalize)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "ICArgs",
    "ICDecayArgs",
    "QuantileArgs",
    "SNRArgs",
    "TurnoverArgs",
    "_hit_rate_by_quantile",
    "_ic_decay",
    "_information_coefficient",
    "_ranks",
    "_snr",
    "_spearman",
    "_turnover",
    "register",
]
