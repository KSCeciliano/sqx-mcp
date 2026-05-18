"""Trading-cost overlay on backtest results.

Backtests rarely include realistic slippage and commission, so a
strategy that looks great in the SQ X databank may be marginal once
real costs are subtracted. These tools apply a configurable cost
model and report degraded metrics.

Model:

- Per-trade cost = ``commission_per_trade`` + ``slippage_points * point_value``
- Both costs are deducted from each trade's PnL.
- Net PnL series is then re-aggregated to compute degraded fitness,
  profit factor, drawdown, etc.

Default assumptions are loose — pass concrete numbers when you know
your broker's terms. The tool returns BOTH the original and degraded
metrics, plus the absolute reduction.

Tools:

- ``cost_apply_to_trades`` — given a trade PnL array, return the same
  array with costs deducted.
- ``cost_recompute_metrics`` — given a trade PnL array plus a cost
  model, compute side-by-side {original, degraded} metrics: net
  profit, profit factor, win rate, average trade, max drawdown.
- ``cost_break_even_per_trade`` — given a strategy's average trade,
  return the per-trade cost at which the average becomes zero
  (the "cost ceiling" for the strategy to remain viable).
- ``cost_sensitivity_grid`` — sweep through a grid of (commission,
  slippage) combinations and report the degraded net profit at each
  point.

Pure math — read-only.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._common import safe_error_payload
from sq_mcp.tools._numerics import NumericValidationError, is_finite, validate_finite_floats

# ---- argument schemas ------------------------------------------------------


class CostModelArgs(BaseModel):
    trades: list[float] = Field(..., min_length=5, max_length=100_000)
    commission_per_trade: float = Field(0.0, ge=0)
    slippage_points: float = Field(0.0, ge=0)
    point_value: float = Field(1.0, gt=0)


class CostBreakEvenArgs(BaseModel):
    average_trade: float
    point_value: float = Field(1.0, gt=0)
    commission_min: float = Field(0.0, ge=0)
    commission_max: float = Field(50.0, gt=0)


class CostSensitivityArgs(BaseModel):
    trades: list[float] = Field(..., min_length=10, max_length=100_000)
    commission_grid: list[float] = Field(..., min_length=1, max_length=10)
    slippage_grid: list[float] = Field(..., min_length=1, max_length=10)
    point_value: float = Field(1.0, gt=0)


# ---- helpers ---------------------------------------------------------------


def _per_trade_cost(args: CostModelArgs) -> float:
    for name, v in (
        ("commission_per_trade", args.commission_per_trade),
        ("slippage_points", args.slippage_points),
        ("point_value", args.point_value),
    ):
        if not is_finite(v):
            raise NumericValidationError(f"{name}={v!r} is not finite")
    return args.commission_per_trade + args.slippage_points * args.point_value


def _apply_costs(trades: list[float], per_trade_cost: float) -> list[float]:
    validate_finite_floats(trades, name="trades")
    if not is_finite(per_trade_cost):
        raise NumericValidationError(f"per_trade_cost={per_trade_cost!r} is not finite")
    return [t - per_trade_cost for t in trades]


def _trade_metrics(trades: list[float]) -> dict[str, Any]:
    """Compute the metrics we'll compare before/after costs."""
    n = len(trades)
    if n == 0:
        return {
            "n_trades": 0,
            "net_profit": 0.0,
            "win_rate": None,
            "profit_factor": None,
            "average_trade": None,
            "max_drawdown_abs": 0.0,
        }
    gross_profit = sum(t for t in trades if t > 0)
    gross_loss = -sum(t for t in trades if t < 0)
    net = sum(trades)
    wins = sum(1 for t in trades if t > 0)
    pf = gross_profit / gross_loss if gross_loss > 0 else None

    # Max drawdown on cumulative trade sequence (peak-to-trough on cumsum)
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

    return {
        "n_trades": n,
        "net_profit": round(net, 6),
        "gross_profit": round(gross_profit, 6),
        "gross_loss": round(gross_loss, 6),
        "win_rate": round(wins / n, 4),
        "profit_factor": round(pf, 4) if pf is not None else None,
        "average_trade": round(net / n, 6),
        "max_drawdown_abs": round(max_dd, 6),
    }


def _recompute_metrics(args: CostModelArgs) -> dict[str, Any]:
    cost = _per_trade_cost(args)
    original = _trade_metrics(args.trades)
    degraded_trades = _apply_costs(args.trades, cost)
    degraded = _trade_metrics(degraded_trades)
    reduction = (original["net_profit"] or 0.0) - (degraded["net_profit"] or 0.0)
    survives = (degraded["net_profit"] or 0.0) > 0
    return {
        "per_trade_cost": round(cost, 6),
        "original": original,
        "degraded": degraded,
        "net_profit_reduction": round(reduction, 6),
        "net_profit_reduction_pct": (
            round(100 * reduction / abs(original["net_profit"]), 4)
            if original["net_profit"]
            else None
        ),
        "survives_costs": survives,
        "verdict": "viable" if survives else "not_viable_after_costs",
    }


def _break_even_search(args: CostBreakEvenArgs) -> dict[str, Any]:
    """Find the per-trade cost at which average_trade becomes 0."""
    if not is_finite(args.average_trade):
        raise NumericValidationError(f"average_trade={args.average_trade!r} is not finite")
    # Average trade is in dollars; per-trade cost is in dollars
    # Break-even cost = average_trade (when avg > 0)
    if args.average_trade <= 0:
        return {
            "break_even_cost_per_trade": 0.0,
            "note": "strategy already non-positive average; any cost makes it worse",
        }
    return {
        "break_even_cost_per_trade": round(args.average_trade, 6),
        "interpretation": (
            f"costs above {round(args.average_trade, 4)} per trade make the strategy unprofitable on average"
        ),
    }


def _sensitivity_grid(args: CostSensitivityArgs) -> dict[str, Any]:
    validate_finite_floats(args.trades, name="trades")
    validate_finite_floats(args.commission_grid, name="commission_grid")
    validate_finite_floats(args.slippage_grid, name="slippage_grid")
    if not is_finite(args.point_value):
        raise NumericValidationError(f"point_value={args.point_value!r} is not finite")
    grid_rows = []
    original_net = sum(args.trades)
    for c in args.commission_grid:
        for s in args.slippage_grid:
            cost = c + s * args.point_value
            degraded = _apply_costs(args.trades, cost)
            net = sum(degraded)
            grid_rows.append(
                {
                    "commission_per_trade": c,
                    "slippage_points": s,
                    "per_trade_cost": round(cost, 6),
                    "degraded_net_profit": round(net, 6),
                    "reduction": round(original_net - net, 6),
                    "survives": net > 0,
                }
            )
    # Sort by degraded net descending (best first)
    grid_rows.sort(key=lambda r: r["degraded_net_profit"], reverse=True)
    return {
        "original_net_profit": round(original_net, 6),
        "n_combinations": len(grid_rows),
        "n_surviving_combinations": sum(1 for r in grid_rows if r["survives"]),
        "grid": grid_rows,
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Subtract a per-trade cost (commission + slippage_points × "
            "point_value) from each PnL. Returns the cost-adjusted trade list. "
            "Useful to feed into other analytics with realistic costs."
        )
    )
    async def cost_apply_to_trades(args: CostModelArgs) -> dict:
        try:
            cost = _per_trade_cost(args)
            degraded = _apply_costs(args.trades, cost)
            return {
                "ok": True,
                "per_trade_cost": round(cost, 6),
                "trades_count": len(degraded),
                "degraded_trades": [round(t, 6) for t in degraded],
            }
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Side-by-side original vs cost-degraded metrics: net profit, profit "
            "factor, win rate, average trade, max drawdown. Reports whether the "
            "strategy survives the modeled costs."
        )
    )
    async def cost_recompute_metrics(args: CostModelArgs) -> dict:
        try:
            return {"ok": True, **_recompute_metrics(args)}
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compute the per-trade cost at which a strategy's average trade hits "
            "zero. The 'cost ceiling' for the strategy to remain viable. "
            "Pure math."
        )
    )
    async def cost_break_even_per_trade(args: CostBreakEvenArgs) -> dict:
        try:
            return {"ok": True, **_break_even_search(args)}
        except NumericValidationError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Sweep over a grid of (commission, slippage_points) values; report "
            "degraded net profit and survival at each combination. Sorted "
            "best→worst. Use to find the cost envelope a strategy can tolerate."
        )
    )
    async def cost_sensitivity_grid(args: CostSensitivityArgs) -> dict:
        try:
            return {"ok": True, **_sensitivity_grid(args)}
        except NumericValidationError as exc:
            return safe_error_payload(exc)
