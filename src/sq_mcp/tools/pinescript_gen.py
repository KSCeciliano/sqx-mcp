"""Pine Script (TradingView) generator scaffolds.

Generate TradingView Pine Script v5 source for indicator and strategy
templates that mirror a typical SQ X strategy's structure. The output
is text-only — read-only with respect to the workspace.

Tools:

- ``pinescript_generate_indicator`` — emit a Pine Script v5 indicator
  with a simple entry signal based on a user-supplied indicator spec.
- ``pinescript_generate_strategy`` — emit a Pine Script v5 strategy with
  entries, exits, position sizing, and date filter.
- ``pinescript_generate_alert_template`` — emit alertcondition()
  scaffolding compatible with TradingView's webhook integrations.
- ``pinescript_translate_mt5_basic`` — translate the high-level shape
  of an MT5 basic EA into Pine Script (best-effort, manual review
  required).

Pure-text tools. No engine, no workspace mutation.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field


class IndicatorArgs(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    indicator_type: Literal["sma_crossover", "ema_crossover", "rsi", "macd"]
    fast_period: int = Field(20, ge=2, le=500)
    slow_period: int = Field(50, ge=2, le=500)
    rsi_overbought: int = Field(70, ge=50, le=95)
    rsi_oversold: int = Field(30, ge=5, le=50)


class StrategyArgs(IndicatorArgs):
    risk_per_trade_pct: float = Field(1.0, gt=0.0, le=10.0)
    take_profit_pct: float = Field(2.0, gt=0.0, le=100.0)
    stop_loss_pct: float = Field(1.0, gt=0.0, le=100.0)
    date_from: str = Field("2023.01.01", min_length=10, max_length=10)
    use_pyramiding: bool = False


class AlertArgs(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    indicator_name: str = Field(..., min_length=1, max_length=128)
    webhook_message_template: str = Field(
        '{"symbol":"{{ticker}}","price":"{{close}}","action":"{{strategy.order.action}}"}',
        min_length=1,
        max_length=1024,
    )


class TranslateMt5Args(BaseModel):
    mt5_summary: dict[str, Any]


def _indicator_source(args: IndicatorArgs) -> str:
    header = (
        "//@version=5\n"
        f"indicator(title=\"{args.name}\", overlay=true)\n\n"
    )
    if args.indicator_type == "sma_crossover":
        return header + (
            f"fast = ta.sma(close, {args.fast_period})\n"
            f"slow = ta.sma(close, {args.slow_period})\n"
            "plot(fast, color=color.green, title=\"Fast SMA\")\n"
            "plot(slow, color=color.red,   title=\"Slow SMA\")\n"
            "longSignal  = ta.crossover(fast, slow)\n"
            "shortSignal = ta.crossunder(fast, slow)\n"
            "plotshape(longSignal,  title=\"Long\",  style=shape.triangleup,   location=location.belowbar, color=color.green, size=size.small)\n"
            "plotshape(shortSignal, title=\"Short\", style=shape.triangledown, location=location.abovebar, color=color.red,   size=size.small)\n"
        )
    if args.indicator_type == "ema_crossover":
        return header + (
            f"fast = ta.ema(close, {args.fast_period})\n"
            f"slow = ta.ema(close, {args.slow_period})\n"
            "plot(fast, color=color.green, title=\"Fast EMA\")\n"
            "plot(slow, color=color.red,   title=\"Slow EMA\")\n"
            "longSignal  = ta.crossover(fast, slow)\n"
            "shortSignal = ta.crossunder(fast, slow)\n"
        )
    if args.indicator_type == "rsi":
        return header + (
            f"rsi = ta.rsi(close, {args.fast_period})\n"
            "plot(rsi, title=\"RSI\")\n"
            f"hline({args.rsi_overbought}, \"OB\")\n"
            f"hline({args.rsi_oversold}, \"OS\")\n"
            f"longSignal  = ta.crossover(rsi, {args.rsi_oversold})\n"
            f"shortSignal = ta.crossunder(rsi, {args.rsi_overbought})\n"
        )
    # macd
    return header + (
        f"[macdLine, signalLine, _] = ta.macd(close, {args.fast_period}, {args.slow_period}, 9)\n"
        "plot(macdLine,   title=\"MACD\",   color=color.blue)\n"
        "plot(signalLine, title=\"Signal\", color=color.orange)\n"
        "longSignal  = ta.crossover(macdLine, signalLine)\n"
        "shortSignal = ta.crossunder(macdLine, signalLine)\n"
    )


def _strategy_source(args: StrategyArgs) -> str:
    header = (
        "//@version=5\n"
        f"strategy(title=\"{args.name}\", overlay=true, default_qty_type=strategy.percent_of_equity, "
        f"default_qty_value={args.risk_per_trade_pct}, pyramiding={1 if args.use_pyramiding else 0})\n\n"
        f"dateFrom = timestamp(\"{args.date_from} 00:00 +0000\")\n"
        "inWindow = time >= dateFrom\n\n"
    )
    base = _indicator_source(args).split("\n", 2)[2] if False else ""
    body: str
    if args.indicator_type in ("sma_crossover", "ema_crossover"):
        ma_func = "ta.sma" if args.indicator_type == "sma_crossover" else "ta.ema"
        body = (
            f"fast = {ma_func}(close, {args.fast_period})\n"
            f"slow = {ma_func}(close, {args.slow_period})\n"
            "longSignal  = ta.crossover(fast, slow)\n"
            "shortSignal = ta.crossunder(fast, slow)\n\n"
        )
    elif args.indicator_type == "rsi":
        body = (
            f"rsi = ta.rsi(close, {args.fast_period})\n"
            f"longSignal  = ta.crossover(rsi, {args.rsi_oversold})\n"
            f"shortSignal = ta.crossunder(rsi, {args.rsi_overbought})\n\n"
        )
    else:
        body = (
            f"[macdLine, signalLine, _] = ta.macd(close, {args.fast_period}, {args.slow_period}, 9)\n"
            "longSignal  = ta.crossover(macdLine, signalLine)\n"
            "shortSignal = ta.crossunder(macdLine, signalLine)\n\n"
        )
    body += (
        f"tpLong  = close * (1 + {args.take_profit_pct} / 100)\n"
        f"slLong  = close * (1 - {args.stop_loss_pct} / 100)\n"
        f"tpShort = close * (1 - {args.take_profit_pct} / 100)\n"
        f"slShort = close * (1 + {args.stop_loss_pct} / 100)\n\n"
        "if inWindow and longSignal\n"
        "    strategy.entry(\"L\", strategy.long)\n"
        "    strategy.exit(\"L-exit\",  from_entry=\"L\", limit=tpLong,  stop=slLong)\n\n"
        "if inWindow and shortSignal\n"
        "    strategy.entry(\"S\", strategy.short)\n"
        "    strategy.exit(\"S-exit\", from_entry=\"S\", limit=tpShort, stop=slShort)\n"
    )
    return header + body + base


def _alert_template(args: AlertArgs) -> str:
    return (
        "//@version=5\n"
        f"indicator(title=\"{args.indicator_name} Alerts\", overlay=true)\n\n"
        "// Define your signal condition here (replace the SMA example below)\n"
        "fastSignal = ta.crossover(ta.sma(close, 20), ta.sma(close, 50))\n\n"
        "alertcondition(fastSignal, title=\"Long entry\", message='" + args.webhook_message_template.replace("'", "\\'") + "')\n"
    )


def _translate_mt5(summary: dict[str, Any]) -> dict[str, Any]:
    """Best-effort translation of an MT5 EA summary into a Pine Script
    scaffold. Returns code + a `manual_review_required` flag because
    semantic differences (timeframe, slippage, magic numbers) often
    require human attention.
    """
    direction = (summary.get("direction") or "both").lower()
    indicators = summary.get("indicators") or []
    risk = summary.get("risk_per_trade_pct") or 1.0
    tp = summary.get("take_profit_pct") or 2.0
    sl = summary.get("stop_loss_pct") or 1.0
    name = summary.get("name") or "TranslatedEA"
    args = StrategyArgs(
        name=name,
        indicator_type="ema_crossover",
        risk_per_trade_pct=risk,
        take_profit_pct=tp,
        stop_loss_pct=sl,
    )
    code = _strategy_source(args)
    return {
        "pine_source": code,
        "manual_review_required": True,
        "notes": (
            "Translation is structural only — Pine Script does not have an exact "
            "equivalent of MT5's order pool, magic numbers, or some indicator "
            "implementations. Review the entry/exit logic and adjust manually."
        ),
        "indicators_in_source": indicators,
        "direction_in_source": direction,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Emit Pine Script v5 indicator source. Supports sma_crossover, "
            "ema_crossover, rsi, macd. Returns the source as a string the "
            "user can paste into TradingView's Pine editor. Read-only."
        )
    )
    async def pinescript_generate_indicator(args: IndicatorArgs) -> dict:
        return {
            "ok": True,
            "language": "pinescript-v5",
            "name": args.name,
            "source": _indicator_source(args),
        }

    @mcp.tool(
        description=(
            "Emit a Pine Script v5 strategy with entries, TP/SL exits, "
            "risk-percent sizing, and date filter. Read-only."
        )
    )
    async def pinescript_generate_strategy(args: StrategyArgs) -> dict:
        return {
            "ok": True,
            "language": "pinescript-v5",
            "name": args.name,
            "source": _strategy_source(args),
        }

    @mcp.tool(
        description=(
            "Emit Pine Script alertcondition() scaffold compatible with "
            "TradingView webhooks. Caller supplies the webhook message "
            "template (JSON with TradingView placeholders like {{ticker}})."
        )
    )
    async def pinescript_generate_alert_template(args: AlertArgs) -> dict:
        return {
            "ok": True,
            "language": "pinescript-v5",
            "source": _alert_template(args),
        }

    @mcp.tool(
        description=(
            "Best-effort translate the structural shape of an MT5 strategy "
            "into Pine Script. Returns the source plus a 'manual_review_"
            "required' flag — semantic gaps (magic numbers, slippage, "
            "indicator differences) need human attention."
        )
    )
    async def pinescript_translate_mt5_basic(args: TranslateMt5Args) -> dict:
        return {"ok": True, **_translate_mt5(args.mt5_summary)}


__all__ = [
    "AlertArgs",
    "IndicatorArgs",
    "StrategyArgs",
    "TranslateMt5Args",
    "_alert_template",
    "_indicator_source",
    "_strategy_source",
    "_translate_mt5",
    "register",
]
