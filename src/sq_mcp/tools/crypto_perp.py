"""Crypto perpetual-future-specific corrections: funding-rate PnL and
liquidation-distance stress.

Crypto perpetual futures have two mechanics that break a naive spot
backtest:

1. **Funding payments** flow between longs and shorts every funding
   interval (typically 8 hours on Binance / Bybit). A long pays positive
   funding to shorts; a short receives it. A backtest that ignores
   funding overstates long returns and understates short returns by
   anywhere from 5%/yr (low-funding regime) to 30%/yr (BTC bull run).

2. **Liquidation** triggers when the maintenance margin is breached.
   Intra-bar excursion can liquidate a leveraged position even when the
   bar close is profitable. Spot backtests never simulate this.

Tools:

- ``perp_funding_adjusted_pnl`` — given trade list + funding rate series
  + position side, return funding-adjusted PnL per trade.
- ``perp_liquidation_price`` — compute liquidation price for a given
  entry, leverage, side, maintenance margin %.
- ``perp_liquidation_distance_pct`` — percentage move from entry that
  would liquidate the position.
- ``perp_liquidation_stress_test`` — replay trades with intra-bar high/
  low excursion and report which would have been liquidated.
- ``perp_funding_regime_classifier`` — bucket funding-rate observations
  into contango/neutral/backwardation regimes.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)


class FundingAdjustedPnlArgs(BaseModel):
    trade_pnls: list[float] = Field(..., min_length=1, max_length=200_000)
    holding_periods_hours: list[float] = Field(..., min_length=1, max_length=200_000)
    avg_funding_rate_per_8h: list[float] = Field(..., min_length=1, max_length=200_000)
    position_sides: list[Literal["long", "short"]] = Field(
        ..., min_length=1, max_length=200_000
    )
    position_notional: list[float] = Field(..., min_length=1, max_length=200_000)


class LiquidationPriceArgs(BaseModel):
    entry_price: float = Field(..., gt=0.0)
    leverage: float = Field(..., gt=1.0, le=125.0)
    side: Literal["long", "short"] = "long"
    maintenance_margin_pct: float = Field(0.5, gt=0.0, le=10.0)


class LiquidationDistanceArgs(LiquidationPriceArgs):
    pass


class LiquidationStressArgs(BaseModel):
    entries: list[float] = Field(..., min_length=1, max_length=200_000)
    intrabar_highs: list[float] = Field(..., min_length=1, max_length=200_000)
    intrabar_lows: list[float] = Field(..., min_length=1, max_length=200_000)
    sides: list[Literal["long", "short"]] = Field(..., min_length=1, max_length=200_000)
    leverage: float = Field(..., gt=1.0, le=125.0)
    maintenance_margin_pct: float = Field(0.5, gt=0.0, le=10.0)


class FundingRegimeArgs(BaseModel):
    funding_rates_per_8h: list[float] = Field(..., min_length=10, max_length=200_000)
    contango_threshold: float = Field(0.0001, ge=0.0, le=0.01)
    backwardation_threshold: float = Field(-0.0001, ge=-0.01, le=0.0)


def _funding_adjusted_pnl(
    pnls: list[float],
    holding_hours: list[float],
    funding_per_8h: list[float],
    sides: list[str],
    notionals: list[float],
) -> dict[str, Any]:
    validate_finite_floats(pnls, name="trade_pnls")
    validate_finite_floats(holding_hours, name="holding_periods_hours")
    validate_finite_floats(funding_per_8h, name="avg_funding_rate_per_8h")
    validate_finite_floats(notionals, name="position_notional")
    n = min(len(pnls), len(holding_hours), len(funding_per_8h), len(sides), len(notionals))
    adjusted: list[float] = []
    total_funding = 0.0
    for i in range(n):
        # Funding flows in increments of 8h. Total funding = avg_rate × (hours/8) × notional
        # Long pays positive funding (subtract); short receives (add).
        intervals = holding_hours[i] / 8.0
        funding_pay = funding_per_8h[i] * intervals * notionals[i]
        if sides[i] == "long":
            adjusted.append(pnls[i] - funding_pay)
            total_funding -= funding_pay
        else:
            adjusted.append(pnls[i] + funding_pay)
            total_funding += funding_pay
    orig_total = sum(pnls[:n])
    adj_total = sum(adjusted)
    return {
        "adjusted_pnls": adjusted,
        "n_trades": n,
        "original_total_pnl": round(orig_total, 6),
        "adjusted_total_pnl": round(adj_total, 6),
        "total_funding_impact": round(total_funding, 6),
        "funding_pct_of_pnl": (
            round(100.0 * abs(total_funding) / abs(orig_total), 4)
            if orig_total != 0 else None
        ),
        "warning": (
            "funding impact > 10% of original PnL — original backtest "
            "significantly misstates strategy returns"
        ) if (
            orig_total != 0 and abs(total_funding) / abs(orig_total) > 0.1
        ) else None,
    }


def _liquidation_price(
    entry: float, leverage: float, side: str, maintenance_pct: float
) -> dict[str, Any]:
    """Approximate liquidation price for isolated margin perp:
       long:  liq = entry × (1 - 1/lev + maint/100)
       short: liq = entry × (1 + 1/lev - maint/100)
    """
    m = maintenance_pct / 100.0
    if side == "long":
        liq = entry * (1.0 - 1.0 / leverage + m)
    else:
        liq = entry * (1.0 + 1.0 / leverage - m)
    distance_pct = abs(liq - entry) / entry * 100.0
    return {
        "liquidation_price": round(liq, 8),
        "distance_pct": round(distance_pct, 4),
        "entry_price": entry,
        "leverage": leverage,
        "side": side,
        "maintenance_margin_pct": maintenance_pct,
    }


def _liquidation_stress(
    entries: list[float],
    highs: list[float],
    lows: list[float],
    sides: list[str],
    leverage: float,
    maintenance_pct: float,
) -> dict[str, Any]:
    validate_finite_floats(entries, name="entries")
    validate_finite_floats(highs, name="intrabar_highs")
    validate_finite_floats(lows, name="intrabar_lows")
    n = min(len(entries), len(highs), len(lows), len(sides))
    liquidations: list[dict[str, Any]] = []
    survived = 0
    for i in range(n):
        liq = _liquidation_price(entries[i], leverage, sides[i], maintenance_pct)
        liq_price = liq["liquidation_price"]
        if sides[i] == "long":
            hit = lows[i] <= liq_price
        else:
            hit = highs[i] >= liq_price
        if hit:
            liquidations.append({
                "index": i,
                "entry": entries[i],
                "side": sides[i],
                "liquidation_price": liq_price,
                "intrabar_low": lows[i],
                "intrabar_high": highs[i],
            })
        else:
            survived += 1
    return {
        "n_trades": n,
        "n_liquidated": len(liquidations),
        "n_survived": survived,
        "liquidation_rate_pct": round(100.0 * len(liquidations) / n, 4) if n else 0.0,
        "leverage": leverage,
        "maintenance_margin_pct": maintenance_pct,
        "liquidations": liquidations[:50],  # cap output
        "verdict": (
            "safe" if len(liquidations) == 0
            else "marginal" if len(liquidations) / n < 0.01
            else "dangerous" if len(liquidations) / n < 0.05
            else "unfeasible"
        ),
    }


def _funding_regime(
    rates: list[float], contango: float, backwardation: float
) -> dict[str, Any]:
    validate_finite_floats(rates, name="funding_rates_per_8h")
    counts = {"contango": 0, "neutral": 0, "backwardation": 0}
    labels: list[str] = []
    for r in rates:
        if r >= contango:
            labels.append("contango")
            counts["contango"] += 1
        elif r <= backwardation:
            labels.append("backwardation")
            counts["backwardation"] += 1
        else:
            labels.append("neutral")
            counts["neutral"] += 1
    n = len(rates)
    return {
        "labels": labels,
        "counts": counts,
        "share_pct": {k: round(100.0 * v / n, 2) for k, v in counts.items()},
        "annualized_funding_pct": round(sum(rates) / n * 3 * 365 * 100, 4) if n else 0.0,
        "n_periods": n,
        "interpretation": (
            "bullish" if counts["contango"] > counts["backwardation"] * 2
            else "bearish" if counts["backwardation"] > counts["contango"] * 2
            else "balanced"
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Adjust trade PnLs for funding-rate payments on perpetual "
            "futures. Caller supplies per-trade holding hours, average "
            "funding rate per 8h, side (long/short), and notional. Longs "
            "pay positive funding; shorts receive it. Reports the adjusted "
            "per-trade PnLs + the total funding impact + a warning if "
            "funding cost exceeds 10% of original PnL."
        )
    )
    async def perp_funding_adjusted_pnl(args: FundingAdjustedPnlArgs) -> dict:
        try:
            return {
                "ok": True,
                **_funding_adjusted_pnl(
                    args.trade_pnls,
                    args.holding_periods_hours,
                    args.avg_funding_rate_per_8h,
                    args.position_sides,
                    args.position_notional,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Compute the liquidation price for an isolated-margin "
            "perpetual-futures position given entry price, leverage, side, "
            "and maintenance-margin percentage (default 0.5%). Returns the "
            "liquidation price and the distance from entry as a percent."
        )
    )
    async def perp_liquidation_price(args: LiquidationPriceArgs) -> dict:
        return {
            "ok": True,
            **_liquidation_price(
                args.entry_price, args.leverage, args.side, args.maintenance_margin_pct
            ),
        }

    @mcp.tool(
        description=(
            "Same as perp_liquidation_price but returns just the percentage "
            "distance from entry. Convenience for risk dashboards."
        )
    )
    async def perp_liquidation_distance_pct(args: LiquidationDistanceArgs) -> dict:
        result = _liquidation_price(
            args.entry_price, args.leverage, args.side, args.maintenance_margin_pct
        )
        return {
            "ok": True,
            "distance_pct": result["distance_pct"],
            "leverage": args.leverage,
            "side": args.side,
        }

    @mcp.tool(
        description=(
            "Replay trades with intra-bar high/low excursion to detect "
            "which would have been liquidated at the given leverage. "
            "Reports per-trade liquidation hits (up to 50) and a verdict "
            "(safe / marginal / dangerous / unfeasible)."
        )
    )
    async def perp_liquidation_stress_test(args: LiquidationStressArgs) -> dict:
        try:
            return {
                "ok": True,
                **_liquidation_stress(
                    args.entries,
                    args.intrabar_highs,
                    args.intrabar_lows,
                    args.sides,
                    args.leverage,
                    args.maintenance_margin_pct,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Bucket per-8h funding-rate observations into "
            "contango / neutral / backwardation regimes by threshold. "
            "Returns per-bucket counts, share percentages, and an "
            "annualized-funding-cost estimate."
        )
    )
    async def perp_funding_regime_classifier(args: FundingRegimeArgs) -> dict:
        try:
            return {
                "ok": True,
                **_funding_regime(
                    args.funding_rates_per_8h,
                    args.contango_threshold,
                    args.backwardation_threshold,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "FundingAdjustedPnlArgs",
    "FundingRegimeArgs",
    "LiquidationDistanceArgs",
    "LiquidationPriceArgs",
    "LiquidationStressArgs",
    "_funding_adjusted_pnl",
    "_funding_regime",
    "_liquidation_price",
    "_liquidation_stress",
    "register",
]
