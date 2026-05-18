"""Drift detection — compare live trade results vs backtest expectations.

A backtest tells you what *should* happen if the strategy keeps working.
Once it's live, every trade is a draw from a distribution that may have
shifted. These tools compute simple, defensive statistics to flag when
the live distribution stops looking like the backtest distribution.

Key idea: do NOT try to detect drift on a handful of trades. The
defaults here require **>= 20 live trades** before any divergence
verdict is rendered. Below that threshold the tool returns
"insufficient_sample" so the agent doesn't act on noise.

Tools:

- ``drift_compare_win_rate`` — Wilson 95% confidence interval on the
  live win-rate; flag if the backtest win-rate falls outside the band.
- ``drift_compare_average_trade`` — z-score of live mean trade PnL vs
  backtest mean (treating backtest mean as population mean). |z|>2 →
  divergent.
- ``drift_compare_distributions`` — Kolmogorov-Smirnov-style two-sample
  distance between live and backtest trade-PnL distributions. Pure
  Python, no scipy.
- ``drift_score_overall`` — combine the three flags into a single score
  and verdict (green/yellow/red).
- ``drift_compare_drawdown`` — compares live max drawdown vs backtest
  max drawdown; flags if live DD exceeds backtest by ``threshold_pct``.

All math is pure Python and read-only.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

MIN_LIVE_TRADES = 20


# ---- argument schemas ------------------------------------------------------


class WinRateArgs(BaseModel):
    backtest_win_rate: float = Field(..., ge=0.0, le=1.0)
    live_wins: int = Field(..., ge=0)
    live_losses: int = Field(..., ge=0)
    confidence: float = Field(0.95, gt=0.5, lt=1.0)


class MeanTradeArgs(BaseModel):
    backtest_mean: float
    backtest_std: float = Field(..., gt=0)
    live_trades: list[float] = Field(..., max_length=10000)


class DistributionsArgs(BaseModel):
    backtest_trades: list[float] = Field(..., min_length=10, max_length=100000)
    live_trades: list[float] = Field(..., max_length=10000)


class DrawdownArgs(BaseModel):
    backtest_max_dd_pct: float = Field(..., ge=0, le=100)
    live_max_dd_pct: float = Field(..., ge=0, le=100)
    threshold_excess_pct: float = Field(5.0, gt=0, le=100)


class OverallArgs(BaseModel):
    backtest_win_rate: float = Field(..., ge=0.0, le=1.0)
    backtest_mean_trade: float
    backtest_std_trade: float = Field(..., gt=0)
    backtest_max_dd_pct: float = Field(..., ge=0, le=100)
    live_trades: list[float] = Field(..., max_length=10000)
    live_max_dd_pct: float = Field(..., ge=0, le=100)


# ---- helpers ---------------------------------------------------------------


def _wilson_interval(p_hat: float, n: int, confidence: float) -> tuple[float, float]:
    """Wilson score interval. Returns (lower, upper)."""
    if n == 0:
        return 0.0, 1.0
    # Approximate z for confidence (95% → 1.96, 99% → 2.576)
    if confidence >= 0.99:
        z = 2.576
    elif confidence >= 0.95:
        z = 1.96
    elif confidence >= 0.90:
        z = 1.645
    else:
        z = 1.96  # fallback
    denom = 1.0 + (z * z) / n
    centre = p_hat + (z * z) / (2 * n)
    margin = z * math.sqrt((p_hat * (1.0 - p_hat) + (z * z) / (4 * n)) / n)
    lo = (centre - margin) / denom
    hi = (centre + margin) / denom
    return max(0.0, lo), min(1.0, hi)


def _win_rate_drift(args: WinRateArgs) -> dict[str, Any]:
    n = args.live_wins + args.live_losses
    if n < MIN_LIVE_TRADES:
        return {
            "verdict": "insufficient_sample",
            "n_live_trades": n,
            "min_required": MIN_LIVE_TRADES,
        }
    p_hat = args.live_wins / n if n > 0 else 0.0
    lo, hi = _wilson_interval(p_hat, n, args.confidence)
    inside = lo <= args.backtest_win_rate <= hi
    return {
        "verdict": "within_band" if inside else "drift_detected",
        "live_win_rate": round(p_hat, 4),
        "backtest_win_rate": args.backtest_win_rate,
        "live_ci_lower": round(lo, 4),
        "live_ci_upper": round(hi, 4),
        "n_live_trades": n,
        "drift_direction": (
            None
            if inside
            else ("better_than_backtest" if p_hat > args.backtest_win_rate else "worse_than_backtest")
        ),
    }


def _mean_trade_drift(args: MeanTradeArgs) -> dict[str, Any]:
    n = len(args.live_trades)
    if n < MIN_LIVE_TRADES:
        return {
            "verdict": "insufficient_sample",
            "n_live_trades": n,
            "min_required": MIN_LIVE_TRADES,
        }
    live_mean = sum(args.live_trades) / n
    # Standard error of the mean under H0: backtest distribution
    se = args.backtest_std / math.sqrt(n)
    z = (live_mean - args.backtest_mean) / se if se > 0 else 0.0
    verdict = (
        "drift_detected" if abs(z) > 2.0 else "within_band"
    )
    return {
        "verdict": verdict,
        "live_mean": round(live_mean, 6),
        "backtest_mean": args.backtest_mean,
        "z_score": round(z, 4),
        "n_live_trades": n,
        "drift_direction": (
            None
            if abs(z) <= 2.0
            else ("better_than_backtest" if z > 0 else "worse_than_backtest")
        ),
    }


def _ks_statistic(a: list[float], b: list[float]) -> float:
    """Simple two-sample KS statistic: max |F_a(x) - F_b(x)|.

    Both lists are sorted internally; we walk through the combined
    sorted values and track each empirical CDF.
    """
    if not a or not b:
        return 0.0
    a_sorted = sorted(a)
    b_sorted = sorted(b)
    all_vals = sorted(set(a_sorted) | set(b_sorted))
    max_d = 0.0
    n_a = len(a_sorted)
    n_b = len(b_sorted)
    # For each unique value, count <= it
    i_a = 0
    i_b = 0
    for v in all_vals:
        while i_a < n_a and a_sorted[i_a] <= v:
            i_a += 1
        while i_b < n_b and b_sorted[i_b] <= v:
            i_b += 1
        d = abs(i_a / n_a - i_b / n_b)
        if d > max_d:
            max_d = d
    return max_d


def _ks_critical(n_a: int, n_b: int, alpha: float = 0.05) -> float:
    """Approximate critical value for two-sample KS at alpha=0.05."""
    if n_a == 0 or n_b == 0:
        return 1.0
    c_alpha = 1.36  # 0.05 significance
    if alpha <= 0.01:
        c_alpha = 1.63
    return c_alpha * math.sqrt((n_a + n_b) / (n_a * n_b))


def _distribution_drift(args: DistributionsArgs) -> dict[str, Any]:
    n_live = len(args.live_trades)
    if n_live < MIN_LIVE_TRADES:
        return {
            "verdict": "insufficient_sample",
            "n_live_trades": n_live,
            "min_required": MIN_LIVE_TRADES,
        }
    d = _ks_statistic(args.backtest_trades, args.live_trades)
    crit = _ks_critical(len(args.backtest_trades), n_live, alpha=0.05)
    verdict = "drift_detected" if d > crit else "within_band"
    return {
        "verdict": verdict,
        "ks_statistic": round(d, 4),
        "critical_value_5pct": round(crit, 4),
        "n_backtest_trades": len(args.backtest_trades),
        "n_live_trades": n_live,
        "note": "two-sample KS distance approximation",
    }


def _drawdown_drift(args: DrawdownArgs) -> dict[str, Any]:
    excess = args.live_max_dd_pct - args.backtest_max_dd_pct
    verdict = (
        "drift_detected" if excess > args.threshold_excess_pct else "within_band"
    )
    return {
        "verdict": verdict,
        "backtest_max_dd_pct": args.backtest_max_dd_pct,
        "live_max_dd_pct": args.live_max_dd_pct,
        "excess_pct": round(excess, 4),
        "threshold_excess_pct": args.threshold_excess_pct,
    }


def _overall_drift(args: OverallArgs) -> dict[str, Any]:
    # Win rate
    wins = sum(1 for t in args.live_trades if t > 0)
    losses = len(args.live_trades) - wins
    win_rate = _win_rate_drift(
        WinRateArgs(
            backtest_win_rate=args.backtest_win_rate,
            live_wins=wins,
            live_losses=losses,
        )
    )
    mean = _mean_trade_drift(
        MeanTradeArgs(
            backtest_mean=args.backtest_mean_trade,
            backtest_std=args.backtest_std_trade,
            live_trades=args.live_trades,
        )
    )
    dd = _drawdown_drift(
        DrawdownArgs(
            backtest_max_dd_pct=args.backtest_max_dd_pct,
            live_max_dd_pct=args.live_max_dd_pct,
        )
    )

    if win_rate["verdict"] == "insufficient_sample":
        return {
            "verdict": "insufficient_sample",
            "components": {"win_rate": win_rate, "mean_trade": mean, "drawdown": dd},
            "recommendation": (
                f"need at least {MIN_LIVE_TRADES} live trades; "
                f"have {len(args.live_trades)}"
            ),
        }

    drift_count = sum(
        1
        for v in (win_rate["verdict"], mean["verdict"], dd["verdict"])
        if v == "drift_detected"
    )
    if drift_count == 0:
        verdict = "green"
    elif drift_count == 1:
        verdict = "yellow"
    else:
        verdict = "red"
    return {
        "verdict": verdict,
        "drift_count": drift_count,
        "components": {"win_rate": win_rate, "mean_trade": mean, "drawdown": dd},
        "recommendation": {
            "green": "no action — keep monitoring",
            "yellow": "investigate the flagged component; reduce position size if uncertain",
            "red": "pause or shrink the strategy; live distribution diverging from backtest",
        }[verdict],
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Wilson confidence interval on live win-rate; flag drift if the "
            "backtest win-rate falls outside the band. Requires >=20 live "
            "trades. Read-only."
        )
    )
    async def drift_compare_win_rate(args: WinRateArgs) -> dict:
        return {"ok": True, **_win_rate_drift(args)}

    @mcp.tool(
        description=(
            "Z-score of the live mean trade PnL vs the backtest mean (assuming "
            "backtest std). |z|>2 → drift. Requires >=20 live trades. Read-only."
        )
    )
    async def drift_compare_average_trade(args: MeanTradeArgs) -> dict:
        return {"ok": True, **_mean_trade_drift(args)}

    @mcp.tool(
        description=(
            "Two-sample Kolmogorov-Smirnov distance between backtest and live "
            "trade-PnL distributions, compared against the 5% critical value. "
            "Pure Python (no scipy). Read-only."
        )
    )
    async def drift_compare_distributions(args: DistributionsArgs) -> dict:
        return {"ok": True, **_distribution_drift(args)}

    @mcp.tool(
        description=(
            "Flag drift if live max drawdown exceeds backtest by more than "
            "threshold_excess_pct. Use as an early stop on a strategy that's "
            "underperforming."
        )
    )
    async def drift_compare_drawdown(args: DrawdownArgs) -> dict:
        return {"ok": True, **_drawdown_drift(args)}

    @mcp.tool(
        description=(
            "Combine win-rate, mean-trade, and drawdown drift checks into one "
            "traffic-light verdict (green / yellow / red) and a recommendation. "
            "Requires >=20 live trades. Read-only."
        )
    )
    async def drift_score_overall(args: OverallArgs) -> dict:
        return {"ok": True, **_overall_drift(args)}
