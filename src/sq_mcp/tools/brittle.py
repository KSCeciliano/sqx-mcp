"""Brittle-strategy detection.

A strategy is "brittle" when most of its P&L came from a small handful
of trades — i.e. the equity curve looks great in aggregate but is
sustained by tail outcomes rather than a consistent edge. These tools
quantify that concentration and flag strategies whose past performance
is unlikely to repeat.

Two angles:

1. **Trade-level concentration** — what fraction of the equity gain
   came from the top-N trades? If the top 5% of trades produced 80%+
   of the P&L, the strategy is fragile. Pure Pareto / Gini-style
   distribution measures.

2. **Equity-curve concentration** — looking at the equity curve alone
   (no trade list), what's the longest *flat* period vs the longest
   *productive* period? A curve that's 90% flat with a few big jumps
   is brittle even if you don't have the trade list.

Tools:

- ``brittle_score_from_trades`` — Pareto + Gini on a trade-PnL array.
  Verdict from clean (uniform contribution) to brittle (top-N
  dominate).
- ``brittle_curve_concentration`` — segment an equity curve into
  ``n_buckets`` and measure how concentrated the gains are across
  buckets.
- ``brittle_top_trades_share`` — explicit "what % of total profit
  came from the top X trades" report; useful for talking to
  stakeholders.
- ``brittle_consecutive_loss_streak`` — longest losing streak as
  a fraction of total trades; brittle strategies often have long
  flat or losing periods.

All math is pure Python, read-only.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._common import safe_error_payload
from sq_mcp.tools._numerics import NumericValidationError, validate_finite_floats

# ---- argument schemas ------------------------------------------------------


class BrittleTradesArgs(BaseModel):
    trades: list[float] = Field(..., min_length=10, max_length=100_000)


class BrittleCurveArgs(BaseModel):
    equity_curve: list[float] = Field(..., min_length=10, max_length=100_000)
    n_buckets: int = Field(20, ge=4, le=200)


class BrittleTopShareArgs(BaseModel):
    trades: list[float] = Field(..., min_length=5, max_length=100_000)
    top_n: int = Field(5, ge=1, le=1000)


class BrittleStreakArgs(BaseModel):
    trades: list[float] = Field(..., min_length=10, max_length=100_000)


# ---- helpers ---------------------------------------------------------------


def _gini(xs: list[float]) -> float:
    """Gini coefficient on |contributions|. 0 = perfectly equal, 1 = pure top."""
    abs_xs = sorted(abs(x) for x in xs)
    n = len(abs_xs)
    if n == 0:
        return 0.0
    total = sum(abs_xs)
    if total == 0:
        return 0.0
    cum = 0.0
    rank_sum = 0.0
    for i, v in enumerate(abs_xs, start=1):
        cum += v
        rank_sum += i * v
    g = (2.0 * rank_sum) / (n * total) - (n + 1.0) / n
    return max(0.0, min(1.0, g))


def _top_n_share(trades: list[float], n: int) -> dict[str, Any]:
    validate_finite_floats(trades, name="trades")
    pos = [t for t in trades if t > 0]
    if not pos:
        return {
            "top_n": n,
            "top_n_sum": 0.0,
            "total_positive_pnl": 0.0,
            "share_of_total_pct": 0.0,
            "note": "no positive trades",
        }
    sorted_desc = sorted(pos, reverse=True)
    top_n = sorted_desc[:n]
    total = sum(pos)
    return {
        "top_n": n,
        "top_n_sum": round(sum(top_n), 6),
        "total_positive_pnl": round(total, 6),
        "share_of_total_pct": round(100.0 * sum(top_n) / total, 4),
        "n_positive_trades": len(pos),
    }


def _brittle_score_from_trades(trades: list[float]) -> dict[str, Any]:
    validate_finite_floats(trades, name="trades")
    g = _gini(trades)
    n = len(trades)
    # Top 5% / Top 10% shares
    top_5 = max(1, n // 20)
    top_10 = max(1, n // 10)
    share_5 = _top_n_share(trades, top_5)
    share_10 = _top_n_share(trades, top_10)

    # Score combining Gini and concentration
    if share_10["share_of_total_pct"] >= 80:
        verdict = "very_brittle"
    elif share_10["share_of_total_pct"] >= 60:
        verdict = "brittle"
    elif g > 0.7:
        verdict = "skewed"
    else:
        verdict = "robust"

    return {
        "verdict": verdict,
        "gini": round(g, 4),
        "top_5pct_share_pct": share_5["share_of_total_pct"],
        "top_10pct_share_pct": share_10["share_of_total_pct"],
        "n_trades": n,
        "top_5pct_trade_count": top_5,
        "top_10pct_trade_count": top_10,
        "interpretation": {
            "very_brittle": (
                "top 10% of trades produced >=80% of P&L — extreme fragility"
            ),
            "brittle": (
                "top 10% of trades produced >=60% of P&L — moderate fragility"
            ),
            "skewed": (
                "Gini > 0.7 but concentration not yet extreme — monitor closely"
            ),
            "robust": "P&L well-distributed across trades",
        }[verdict],
    }


def _curve_buckets(curve: list[float], n_buckets: int) -> list[float]:
    """Return per-bucket equity DELTAS (last - first) in absolute terms."""
    if n_buckets < 1 or len(curve) < n_buckets:
        return []
    chunk = len(curve) // n_buckets
    deltas = []
    for b in range(n_buckets):
        lo = b * chunk
        hi = (b + 1) * chunk if b < n_buckets - 1 else len(curve)
        sub = curve[lo:hi]
        if len(sub) >= 2:
            deltas.append(sub[-1] - sub[0])
        else:
            deltas.append(0.0)
    return deltas


def _brittle_curve_concentration(curve: list[float], n_buckets: int) -> dict[str, Any]:
    validate_finite_floats(curve, name="equity_curve")
    deltas = _curve_buckets(curve, n_buckets)
    if not deltas:
        return {"verdict": "insufficient_data"}
    pos_deltas = [d for d in deltas if d > 0]
    neg_deltas = [d for d in deltas if d < 0]
    flat_buckets = sum(1 for d in deltas if abs(d) < 1e-9)
    total_gain = sum(pos_deltas)
    if total_gain == 0:
        return {
            "verdict": "no_gain",
            "n_buckets": n_buckets,
            "n_flat_buckets": flat_buckets,
            "n_negative_buckets": len(neg_deltas),
        }
    # What fraction of gain came from the top 20% of buckets?
    top_20 = max(1, n_buckets // 5)
    top_pos = sorted(pos_deltas, reverse=True)[:top_20]
    top_share = sum(top_pos) / total_gain
    g = _gini(deltas)
    if top_share >= 0.8:
        verdict = "very_brittle"
    elif top_share >= 0.6:
        verdict = "brittle"
    elif g > 0.6:
        verdict = "skewed"
    else:
        verdict = "robust"
    return {
        "verdict": verdict,
        "n_buckets": n_buckets,
        "n_flat_buckets": flat_buckets,
        "n_negative_buckets": len(neg_deltas),
        "top_20pct_buckets_share_pct": round(top_share * 100, 2),
        "gini_on_deltas": round(g, 4),
    }


def _consecutive_loss_streak(trades: list[float]) -> dict[str, Any]:
    validate_finite_floats(trades, name="trades")
    longest = 0
    current = 0
    longest_win = 0
    current_win = 0
    for t in trades:
        if t < 0:
            current += 1
            current_win = 0
            longest = max(longest, current)
        elif t > 0:
            current_win += 1
            current = 0
            longest_win = max(longest_win, current_win)
        else:
            # zero — break both streaks
            current = 0
            current_win = 0
    n = len(trades)
    streak_frac = longest / n if n > 0 else 0
    verdict = (
        "brittle"
        if streak_frac > 0.1
        else "moderate"
        if streak_frac > 0.05
        else "robust"
    )
    return {
        "longest_loss_streak": longest,
        "longest_win_streak": longest_win,
        "longest_loss_streak_fraction": round(streak_frac, 4),
        "verdict": verdict,
        "n_trades": n,
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Detect a brittle strategy from its trade-PnL array. Returns a "
            "verdict (very_brittle / brittle / skewed / robust) based on the top "
            "5% / top 10% trade contribution and Gini coefficient. Use to "
            "decide whether a high-fitness backtest is worth deploying. Read-only."
        )
    )
    async def brittle_score_from_trades(args: BrittleTradesArgs) -> dict:
        try:
            return {"ok": True, **_brittle_score_from_trades(args.trades)}
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Measure concentration of equity-curve gains across n_buckets "
            "segments. Verdict based on what fraction came from the top 20% of "
            "buckets + Gini coefficient. Use when you have the curve but not "
            "the per-trade list. Read-only."
        )
    )
    async def brittle_curve_concentration(args: BrittleCurveArgs) -> dict:
        try:
            return {
                "ok": True,
                **_brittle_curve_concentration(args.equity_curve, args.n_buckets),
            }
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Explicit 'top N trades share of total profit' breakdown. Useful "
            "for stakeholder reports. Returns top_n_sum, total positive PnL, "
            "and share-of-total %."
        )
    )
    async def brittle_top_trades_share(args: BrittleTopShareArgs) -> dict:
        try:
            return {"ok": True, **_top_n_share(args.trades, args.top_n)}
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compute longest consecutive losing streak (and win streak) from a "
            "trade array. Reports the streak as a fraction of total trades. "
            "Brittle strategies often hide their fragility in long flat or "
            "losing periods."
        )
    )
    async def brittle_consecutive_loss_streak(args: BrittleStreakArgs) -> dict:
        try:
            return {"ok": True, **_consecutive_loss_streak(args.trades)}
        except NumericValidationError as exc:
            return safe_error_payload(exc)
