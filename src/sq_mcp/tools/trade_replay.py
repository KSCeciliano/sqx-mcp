"""Trade replay simulator: re-run a trade list under modified assumptions.

The original backtest assumed specific commission, slippage, position
sizing, and risk caps. This module lets you replay the same trade
sequence with overridden assumptions to ask: would this strategy have
survived 2× commission? Would tighter risk caps have killed it? What
happens with fixed-fractional sizing instead of fixed lot?

Tools:

- ``trade_replay_apply_costs`` — apply alternate commission/slippage to
  a trade list and report the new metrics.
- ``trade_replay_position_sizing`` — replay with a different sizing
  policy (fixed lot, fixed fractional, percent risk).
- ``trade_replay_risk_caps`` — apply a per-trade max-loss cap and skip
  trades that would exceed daily/weekly loss limits.
- ``trade_replay_filter_by_regime`` — replay but skip trades in
  specified regimes (using parallel regime labels).
- ``trade_replay_compare_to_original`` — side-by-side metrics of
  original vs replayed.

Pure Python. Read-only.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field


class CostOverrideArgs(BaseModel):
    trade_pnls: list[float] = Field(..., min_length=1, max_length=500_000)
    commission_per_trade: float = Field(0.0, ge=0.0)
    slippage_per_trade: float = Field(0.0, ge=0.0)


class PositionSizingArgs(BaseModel):
    trade_pnls_per_unit: list[float] = Field(..., min_length=1, max_length=500_000)
    equity_start: float = Field(10_000.0, gt=0.0)
    sizing_mode: Literal["fixed_lot", "fixed_fractional", "percent_risk"]
    fixed_lot_size: float = Field(1.0, gt=0.0)
    fraction: float = Field(0.01, gt=0.0, le=1.0)
    risk_per_trade_pct: float = Field(1.0, gt=0.0, le=100.0)
    trade_avg_risk: float = Field(100.0, gt=0.0)


class RiskCapArgs(BaseModel):
    trade_pnls: list[float] = Field(..., min_length=1, max_length=500_000)
    max_loss_per_trade: float | None = None
    max_daily_loss: float | None = None
    max_weekly_loss: float | None = None
    trades_per_day: int = Field(1, ge=1, le=1000)


class RegimeFilterArgs(BaseModel):
    trade_pnls: list[float] = Field(..., min_length=1, max_length=500_000)
    trade_regimes: list[str] = Field(..., min_length=1, max_length=500_000)
    skip_regimes: list[str] = Field(..., min_length=1, max_length=20)


class CompareReplayArgs(BaseModel):
    original_pnls: list[float] = Field(..., min_length=1, max_length=500_000)
    replayed_pnls: list[float] = Field(..., min_length=1, max_length=500_000)


def _metrics_from_pnls(pnls: list[float]) -> dict[str, Any]:
    n = len(pnls)
    if n == 0:
        return {"n": 0}
    total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / n
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    # Equity curve + max DD
    equity = [pnls[0]]
    for p in pnls[1:]:
        equity.append(equity[-1] + p)
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    mean_pnl = total / n
    std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1)) if n > 1 else 0.0
    return {
        "n_trades": n,
        "total_pnl": round(total, 6),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(pf, 4) if math.isfinite(pf) else None,
        "max_drawdown_abs": round(max_dd, 6),
        "mean_pnl": round(mean_pnl, 6),
        "std_pnl": round(std_pnl, 6),
        "final_equity": round(equity[-1], 6),
    }


def _apply_costs(
    pnls: list[float], commission: float, slippage: float
) -> dict[str, Any]:
    cost = commission + slippage
    new_pnls = [p - cost for p in pnls]
    return {
        "original_metrics": _metrics_from_pnls(pnls),
        "replayed_metrics": _metrics_from_pnls(new_pnls),
        "cost_per_trade": cost,
        "total_cost": round(cost * len(pnls), 6),
        "replayed_pnls": new_pnls,
    }


def _position_sizing(
    pnls_per_unit: list[float],
    equity_start: float,
    sizing_mode: str,
    fixed_lot_size: float,
    fraction: float,
    risk_per_trade_pct: float,
    trade_avg_risk: float,
) -> dict[str, Any]:
    equity = equity_start
    scaled_pnls: list[float] = []
    sizes: list[float] = []
    for p in pnls_per_unit:
        if sizing_mode == "fixed_lot":
            size = fixed_lot_size
        elif sizing_mode == "fixed_fractional":
            size = max(0.0, equity * fraction)
        else:  # percent_risk
            size = max(0.0, equity * (risk_per_trade_pct / 100.0) / max(trade_avg_risk, 1e-9))
        sizes.append(size)
        pnl = p * size
        scaled_pnls.append(pnl)
        equity += pnl
    return {
        "sizing_mode": sizing_mode,
        "starting_equity": equity_start,
        "final_equity": round(equity, 6),
        "metrics": _metrics_from_pnls(scaled_pnls),
        "avg_size": round(sum(sizes) / len(sizes), 6) if sizes else 0.0,
        "min_size": round(min(sizes), 6) if sizes else 0.0,
        "max_size": round(max(sizes), 6) if sizes else 0.0,
        "n_trades_blocked": sum(1 for s in sizes if s <= 0),
    }


def _risk_caps(
    pnls: list[float],
    max_loss_per_trade: float | None,
    max_daily_loss: float | None,
    max_weekly_loss: float | None,
    trades_per_day: int,
) -> dict[str, Any]:
    capped: list[float] = []
    day_loss = 0.0
    week_loss = 0.0
    day_idx = 0
    week_idx = 0
    trades_today = 0
    days_this_week = 0
    skipped = 0
    capped_count = 0
    for p in pnls:
        # Per-trade loss cap
        adjusted = p
        if max_loss_per_trade is not None and adjusted < -max_loss_per_trade:
            adjusted = -max_loss_per_trade
            capped_count += 1
        # Daily loss cap
        if max_daily_loss is not None and day_loss - adjusted > max_daily_loss and adjusted < 0:
            skipped += 1
            adjusted = 0.0
        # Weekly loss cap
        if max_weekly_loss is not None and week_loss - adjusted > max_weekly_loss and adjusted < 0:
            skipped += 1
            adjusted = 0.0
        capped.append(adjusted)
        day_loss = max(0.0, day_loss - adjusted)
        week_loss = max(0.0, week_loss - adjusted)
        trades_today += 1
        if trades_today >= trades_per_day:
            trades_today = 0
            day_loss = 0.0
            day_idx += 1
            days_this_week += 1
            if days_this_week >= 5:
                days_this_week = 0
                week_loss = 0.0
                week_idx += 1
    return {
        "metrics": _metrics_from_pnls(capped),
        "n_capped": capped_count,
        "n_skipped": skipped,
        "n_days_simulated": day_idx,
        "n_weeks_simulated": week_idx,
        "capped_pnls": capped,
    }


def _filter_by_regime(
    pnls: list[float], regimes: list[str], skip: list[str]
) -> dict[str, Any]:
    skip_set = set(skip)
    n = min(len(pnls), len(regimes))
    kept_pnls: list[float] = []
    skipped_regimes: dict[str, int] = {}
    for i in range(n):
        if regimes[i] in skip_set:
            skipped_regimes[regimes[i]] = skipped_regimes.get(regimes[i], 0) + 1
            continue
        kept_pnls.append(pnls[i])
    return {
        "n_input": n,
        "n_kept": len(kept_pnls),
        "n_skipped": n - len(kept_pnls),
        "skipped_by_regime": skipped_regimes,
        "metrics": _metrics_from_pnls(kept_pnls),
    }


def _compare(original: list[float], replayed: list[float]) -> dict[str, Any]:
    o = _metrics_from_pnls(original)
    r = _metrics_from_pnls(replayed)
    delta: dict[str, Any] = {}
    for k in ("total_pnl", "win_rate", "profit_factor", "max_drawdown_abs", "final_equity"):
        if o.get(k) is not None and r.get(k) is not None:
            try:
                delta[k] = round(r[k] - o[k], 6)
            except TypeError:
                pass
    return {"original": o, "replayed": r, "delta": delta}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Replay a trade PnL list with overridden commission and slippage "
            "(per-trade cost). Reports original and replayed metrics for "
            "side-by-side comparison."
        )
    )
    async def trade_replay_apply_costs(args: CostOverrideArgs) -> dict:
        return {
            "ok": True,
            **_apply_costs(
                args.trade_pnls,
                args.commission_per_trade,
                args.slippage_per_trade,
            ),
        }

    @mcp.tool(
        description=(
            "Replay trades with a different position-sizing policy: fixed_lot, "
            "fixed_fractional (% of equity per trade), or percent_risk (% of "
            "equity at risk per trade, given trade_avg_risk). Caller supplies "
            "per-unit PnLs."
        )
    )
    async def trade_replay_position_sizing(args: PositionSizingArgs) -> dict:
        return {
            "ok": True,
            **_position_sizing(
                args.trade_pnls_per_unit,
                args.equity_start,
                args.sizing_mode,
                args.fixed_lot_size,
                args.fraction,
                args.risk_per_trade_pct,
                args.trade_avg_risk,
            ),
        }

    @mcp.tool(
        description=(
            "Apply per-trade, per-day, and per-week loss caps. Trades that "
            "would exceed a cap are either capped (per-trade) or skipped "
            "(daily/weekly). Reports replayed metrics."
        )
    )
    async def trade_replay_risk_caps(args: RiskCapArgs) -> dict:
        return {
            "ok": True,
            **_risk_caps(
                args.trade_pnls,
                args.max_loss_per_trade,
                args.max_daily_loss,
                args.max_weekly_loss,
                args.trades_per_day,
            ),
        }

    @mcp.tool(
        description=(
            "Replay trades but skip those in the listed regimes (caller "
            "supplies a parallel regime-label array). Use to validate a "
            "'trade only in low-vol' filter."
        )
    )
    async def trade_replay_filter_by_regime(args: RegimeFilterArgs) -> dict:
        return {
            "ok": True,
            **_filter_by_regime(args.trade_pnls, args.trade_regimes, args.skip_regimes),
        }

    @mcp.tool(
        description=(
            "Compare original vs replayed PnLs side by side. Reports each "
            "metric's delta. Use to quantify the impact of a replay tweak."
        )
    )
    async def trade_replay_compare_to_original(args: CompareReplayArgs) -> dict:
        return {"ok": True, **_compare(args.original_pnls, args.replayed_pnls)}


__all__ = [
    "CompareReplayArgs",
    "CostOverrideArgs",
    "PositionSizingArgs",
    "RegimeFilterArgs",
    "RiskCapArgs",
    "_apply_costs",
    "_compare",
    "_filter_by_regime",
    "_metrics_from_pnls",
    "_position_sizing",
    "_risk_caps",
    "register",
]
