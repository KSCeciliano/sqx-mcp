"""Position-sizing math — risk-percent lot sizing, Kelly, pyramiding.

Pure computation, no engine round-trip. Use these to translate the user's
account state and risk preferences into concrete trade sizes that can be
plugged into MT5 EAs or used to sanity-check a backtest's risk profile.

Tools:

- ``position_sizing_calc`` — given account balance, risk %, stop-loss
  distance and instrument contract spec, computes the lot size that risks
  exactly the requested fraction of the account. Handles forex (pip
  value), crypto (price * size), and generic contract-size instruments.
- ``kelly_criterion`` — given historical win rate and avg win / avg loss,
  computes the Kelly fraction *and* a more conservative "half-Kelly" /
  "quarter-Kelly" alternative. Most professional traders use 0.25 × Kelly
  because the full Kelly is extremely sensitive to estimation error.
- ``position_sizing_pyramid`` — given an initial position and a pyramiding
  schedule (e.g. "add 50% at each +1R, then 25%"), reports the cumulative
  size, average entry, and per-tier risk.
- ``position_sizing_risk_of_ruin`` — analytic approximation of
  long-run ruin probability given win rate, payoff ratio and risk-per-
  trade. Use as a complement to Monte Carlo simulation.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

# ---- argument schemas ------------------------------------------------------


class PositionSizingCalcArgs(BaseModel):
    balance: float = Field(..., gt=0)
    risk_pct: float = Field(..., gt=0, le=100)
    entry_price: float = Field(..., gt=0)
    stop_price: float = Field(..., gt=0)
    instrument_kind: str = "crypto"  # crypto | forex | generic
    contract_size: float = Field(1.0, gt=0)
    pip_size: float = Field(0.0001, gt=0)  # used for forex
    pip_value_per_lot: float = Field(10.0, gt=0)  # USD per pip per 1.0 lot (forex)
    min_lot: float = Field(0.01, gt=0)
    max_lot: float = Field(100.0, gt=0)
    lot_step: float = Field(0.01, gt=0)
    leverage: float = Field(1.0, gt=0)

    @field_validator("instrument_kind")
    @classmethod
    def _v_kind(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"crypto", "forex", "generic"}:
            raise ValueError("instrument_kind must be one of: crypto, forex, generic")
        return v


class KellyCriterionArgs(BaseModel):
    win_rate: float = Field(..., ge=0.0, le=1.0)
    avg_win: float = Field(..., gt=0)
    avg_loss: float = Field(..., gt=0)
    fraction: float = Field(0.25, gt=0.0, le=1.0)  # half-/quarter-Kelly safety factor


class PyramidTier(BaseModel):
    add_at_price: float = Field(..., gt=0)
    add_size: float = Field(..., gt=0)


class PositionSizingPyramidArgs(BaseModel):
    initial_entry_price: float = Field(..., gt=0)
    initial_size: float = Field(..., gt=0)
    tiers: list[PyramidTier] = Field(default_factory=list, max_length=20)
    stop_price: float = Field(..., gt=0)


class RiskOfRuinArgs(BaseModel):
    win_rate: float = Field(..., gt=0.0, lt=1.0)
    payoff_ratio: float = Field(..., gt=0)  # avg_win / avg_loss
    risk_per_trade_pct: float = Field(..., gt=0, le=100)
    ruin_threshold_pct: float = Field(50.0, gt=0, le=100)


# ---- helpers ---------------------------------------------------------------


def _round_to_step(x: float, step: float) -> float:
    return math.floor(x / step) * step


def _lot_size(args: PositionSizingCalcArgs) -> dict[str, Any]:
    """Compute the lot size that risks exactly ``risk_pct`` of balance."""
    sl_distance = abs(args.entry_price - args.stop_price)
    if sl_distance == 0:
        return {
            "ok": False,
            "error": "entry_price == stop_price (zero stop-loss distance)",
        }
    risk_dollars = args.balance * (args.risk_pct / 100.0)

    if args.instrument_kind == "forex":
        # pip-based: risk = lots * pip_value_per_lot * (sl_distance / pip_size)
        pips_at_risk = sl_distance / args.pip_size
        if pips_at_risk == 0:
            return {"ok": False, "error": "zero pips at risk"}
        raw_lots = risk_dollars / (pips_at_risk * args.pip_value_per_lot)
    elif args.instrument_kind == "crypto":
        # crypto contract: PnL per unit = sl_distance, risk = lots * contract_size * sl_distance
        per_lot_risk = args.contract_size * sl_distance
        if per_lot_risk == 0:
            return {"ok": False, "error": "zero per-lot risk"}
        raw_lots = risk_dollars / per_lot_risk
    else:  # generic
        per_unit_risk = args.contract_size * sl_distance
        if per_unit_risk == 0:
            return {"ok": False, "error": "zero per-unit risk"}
        raw_lots = risk_dollars / per_unit_risk

    final_lots = _round_to_step(raw_lots, args.lot_step)
    final_lots = max(args.min_lot, min(args.max_lot, final_lots))

    # Notional / margin checks
    if args.instrument_kind in {"crypto", "generic"}:
        notional = final_lots * args.contract_size * args.entry_price
    else:  # forex
        notional = final_lots * 100_000  # standard forex lot
    margin = notional / max(args.leverage, 1.0)

    return {
        "ok": True,
        "raw_lots": round(raw_lots, 6),
        "final_lots": round(final_lots, 6),
        "risk_dollars": round(risk_dollars, 2),
        "sl_distance": round(sl_distance, 8),
        "notional_dollars": round(notional, 2),
        "margin_required_dollars": round(margin, 2),
        "instrument_kind": args.instrument_kind,
        "balance_after_max_loss": round(args.balance - risk_dollars, 2),
    }


def _kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Classic Kelly: f* = p - (1 - p) / R, where R = win/loss ratio."""
    if avg_loss == 0:
        return 0.0
    r = avg_win / avg_loss
    if r == 0:
        return 0.0
    f = win_rate - (1.0 - win_rate) / r
    return max(0.0, f)  # never short


def _kelly_outputs(args: KellyCriterionArgs) -> dict[str, Any]:
    full = _kelly_fraction(args.win_rate, args.avg_win, args.avg_loss)
    return {
        "full_kelly_fraction": round(full, 6),
        "half_kelly_fraction": round(full * 0.5, 6),
        "quarter_kelly_fraction": round(full * 0.25, 6),
        "recommended_fraction": round(full * args.fraction, 6),
        "interpretation": (
            "negative or zero — strategy has no edge; do not size up"
            if full <= 0
            else "extreme — bet sizes near Kelly amplify drawdowns; consider fraction <= 0.25"
            if full > 0.5
            else "moderate — typical professional sizing is 0.25-0.5 of full Kelly"
        ),
        "payoff_ratio": round(args.avg_win / args.avg_loss, 4) if args.avg_loss else None,
    }


def _pyramid_tiers(args: PositionSizingPyramidArgs) -> dict[str, Any]:
    """Compute cumulative size, weighted entry, R-multiple of each tier."""
    sl_dist0 = abs(args.initial_entry_price - args.stop_price)
    rows: list[dict[str, Any]] = []
    cum_size = args.initial_size
    cum_cost = args.initial_entry_price * args.initial_size
    rows.append(
        {
            "tier": 0,
            "entry_price": round(args.initial_entry_price, 8),
            "size": round(args.initial_size, 6),
            "cumulative_size": round(cum_size, 6),
            "avg_entry": round(args.initial_entry_price, 8),
            "r_multiple": 0.0,
        }
    )
    for i, t in enumerate(args.tiers, start=1):
        cum_size += t.add_size
        cum_cost += t.add_at_price * t.add_size
        avg_entry = cum_cost / cum_size
        r_mult = (t.add_at_price - args.initial_entry_price) / sl_dist0 if sl_dist0 else 0.0
        rows.append(
            {
                "tier": i,
                "entry_price": round(t.add_at_price, 8),
                "size": round(t.add_size, 6),
                "cumulative_size": round(cum_size, 6),
                "avg_entry": round(avg_entry, 8),
                "r_multiple": round(r_mult, 4),
            }
        )
    final_risk = cum_size * abs(args.initial_entry_price - args.stop_price)
    return {
        "ok": True,
        "tiers": rows,
        "final_cumulative_size": round(cum_size, 6),
        "final_avg_entry": round(cum_cost / cum_size, 8) if cum_size else None,
        "absolute_risk_to_initial_stop": round(final_risk, 2),
    }


def _risk_of_ruin(args: RiskOfRuinArgs) -> dict[str, Any]:
    """Approx risk of ruin via the simplified Larry Williams / Vince formula.

    For a binary-outcome trade with edge ``e = p*R - q`` and risk fraction f,
    the probability of an N-unit drawdown is approximately
    ``((1 - e) / (1 + e)) ^ N``, where N = ruin_threshold_pct / risk_per_trade.

    Treat this as a quick analytic estimate; for precision use the Monte Carlo
    simulator.
    """
    p = args.win_rate
    q = 1.0 - p
    edge = p * args.payoff_ratio - q
    if edge <= 0:
        return {
            "edge": round(edge, 6),
            "risk_of_ruin": 1.0,
            "note": "negative edge → ruin is certain in the limit",
        }
    n_units = args.ruin_threshold_pct / args.risk_per_trade_pct
    base = (1.0 - edge) / (1.0 + edge)
    ror = base ** n_units
    return {
        "edge": round(edge, 6),
        "ruin_units": round(n_units, 4),
        "risk_of_ruin": round(min(1.0, max(0.0, ror)), 6),
        "interpretation": (
            "very low (<1%) — strategy can tolerate a streak"
            if ror < 0.01
            else "moderate — consider tighter risk per trade"
            if ror < 0.1
            else "high — reduce risk_per_trade_pct or improve payoff_ratio"
        ),
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Compute the lot/contract size that risks exactly risk_pct of the "
            "account given a stop-loss distance. Handles crypto, forex (pip-based) "
            "and generic contract-size instruments. Rounds to lot_step. Returns "
            "raw + final lots, risk in dollars, notional and margin. Pure math — "
            "no engine call."
        )
    )
    async def position_sizing_calc(args: PositionSizingCalcArgs) -> dict:
        return _lot_size(args)

    @mcp.tool(
        description=(
            "Kelly criterion + fractional-Kelly variants. Given win rate, avg win "
            "and avg loss, returns the full Kelly fraction plus half / quarter "
            "Kelly. Most pros use 0.25 × Kelly because full Kelly is extremely "
            "sensitive to estimation error. Read-only."
        )
    )
    async def kelly_criterion(args: KellyCriterionArgs) -> dict:
        return _kelly_outputs(args)

    @mcp.tool(
        description=(
            "Given an initial position and a pyramiding schedule (a list of "
            "(add_at_price, add_size) tiers), report cumulative size, weighted "
            "average entry, R-multiple of each tier vs the initial stop, and the "
            "total absolute risk to the original stop. Use to design scale-in "
            "rules before coding them into an EA."
        )
    )
    async def position_sizing_pyramid(args: PositionSizingPyramidArgs) -> dict:
        return _pyramid_tiers(args)

    @mcp.tool(
        description=(
            "Analytic approximation of long-run risk of ruin given win rate, "
            "payoff ratio (avg_win / avg_loss) and risk_per_trade_pct. Quick "
            "first-cut estimate; pair with the Monte Carlo simulator for "
            "distribution-aware results."
        )
    )
    async def position_sizing_risk_of_ruin(args: RiskOfRuinArgs) -> dict:
        return _risk_of_ruin(args)
