"""Explain-yourself tools — long-form descriptions of audit codes + finding suggestions.

When `audit.Finding` returns a code like `OVERFIT_OOS_DEGRADATION`, the user
might want a fuller explanation (what does this mean, why does SQ care,
what concrete tool calls help fix it). This module provides that lookup.

Tools:

- ``explain_finding`` — given a finding code, return long description +
  suggested remediation tool calls + related code references.
- ``explain_metric`` — given a metric name from `derive_metrics`, explain
  what it measures + typical good/bad ranges.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._common import safe_error_payload

_FINDING_EXPLANATIONS: dict[str, dict[str, Any]] = {
    "TOO_FEW_TRADES": {
        "severity": "high",
        "what": (
            "Fewer than 30 trades is statistically too thin to draw reliable "
            "performance conclusions. With small samples, any backtest metric "
            "could be due to chance."
        ),
        "fix": [
            "Extend the backtest date range (cfx_set_data_range).",
            "Lower the timeframe (cfx_set_instrument with a finer TF).",
            "Relax entry filters in the Builder Conditions block.",
        ],
    },
    "NO_OOS_FITNESS": {
        "severity": "high",
        "what": (
            "The strategy has been evaluated only in-sample. With no OOS sample "
            "you can't tell if the strategy works on unseen data."
        ),
        "fix": [
            "cfx_set_genetic_options in_sample_ratio_pct=50 (creates a 50% OOS holdout).",
            "Or run a Retest with an OOS sample on existing strategies.",
        ],
    },
    "OVERFIT_OOS_DEGRADATION": {
        "severity": "critical",
        "what": (
            "OOS fitness is less than half of IS fitness. This is a strong "
            "indicator the strategy has been curve-fit to the in-sample data and "
            "won't hold up on unseen markets."
        ),
        "fix": [
            "cfx_set_genetic_options max_generations=50 (reduce overfit cycles).",
            "cfx_configure_robustness with stricter WhatIf checks.",
            "Widen the IS sample window.",
        ],
    },
    "UNREALISTIC_PROFIT_TO_DD": {
        "severity": "high",
        "what": (
            "Profit-to-DD ratios above ~20 typically indicate overfitting, "
            "look-ahead bias, or unrealistic backtest assumptions (zero slippage, "
            "perfect fills)."
        ),
        "fix": [
            "Re-test with realistic spread + slippage + commission.",
            "Compare a coarse-tick vs fine-tick backtest (cfx_set_setup_attrs test_precision=2).",
        ],
    },
    "DRAWDOWN_OVER_HALF": {
        "severity": "high",
        "what": (
            "Max drawdown exceeds 50% of initial capital. Real accounts would be "
            "margin-called or psychologically abandoned long before."
        ),
        "fix": [
            "Lower position size (cfx_set_money_management).",
            "Use risk-percent sizing instead of fixed size.",
        ],
    },
    "AMBIGUOUS_TRADES": {
        "severity": "medium",
        "what": (
            "SQ flagged ambiguous trades — orders that could have been filled "
            "multiple ways within a single bar. Backtest P/L may be unstable."
        ),
        "fix": [
            "Re-run on higher BacktestPrecision (M1 → tick).",
            "cfx_set_setup_attrs test_precision=2 (tick).",
        ],
    },
    "STRATEGY_PROBLEMS": {
        "severity": "high",
        "what": "SQ engine reported StrategyProblems > 0 — internal validation issues.",
        "fix": ["Open the .sqx in the SQ X GUI to inspect the warnings."],
    },
    "VERY_SHORT_BACKTEST": {
        "severity": "medium",
        "what": (
            "Less than a year of history typically doesn't include both bullish "
            "and bearish regimes, so robustness on unseen years is unproven."
        ),
        "fix": ["cfx_set_data_range to at least 3 years.", "data_update to fetch more history."],
    },
    "SUSPICIOUSLY_LOW_DRAWDOWN": {
        "severity": "medium",
        "what": (
            "Near-zero drawdown is suspicious — often a symptom of synthetic "
            "data or look-ahead leaking into entry signals."
        ),
        "fix": ["Inspect the equity curve manually.", "Rerun on realistic data."],
    },
    "EXTREMELY_HIGH_TRADE_FREQ": {
        "severity": "medium",
        "what": (
            "Trading thousands of times per year per instrument is rarely viable "
            "after broker spread + commission."
        ),
        "fix": ["Re-evaluate with realistic costs.", "Filter entries with cfx_set_trade_caps."],
    },
    "SYSTEMIC_OVERFIT": {
        "severity": "high",
        "what": (
            "More than 30% of survivors in the databank show heavy OOS degradation. "
            "The Builder run is finding noise, not edge."
        ),
        "fix": [
            "Reduce population/generations cap (cfx_set_genetic_options).",
            "Add WhatIf robustness (cfx_configure_robustness).",
            "Widen the IS window.",
        ],
    },
    "HIGH_DUPLICATE_RATE": {
        "severity": "medium",
        "what": "A large share of strategies share the same trade sequence — permutations of one edge.",
        "fix": ["portfolio_dedupe to remove duplicates before retest."],
    },
    "HIGH_TRADES_HASH_CONCENTRATION": {
        "severity": "medium",
        "what": "Most strategies cluster into a small number of trade-sequence buckets.",
        "fix": [
            "portfolio_select_diverse to pick across buckets.",
            "cfx_toggle_building_blocks to widen the Builder block pool.",
        ],
    },
    "SINGLE_SYMBOL_ONLY": {
        "severity": "low",
        "what": "All survivors trade the same symbol — no cross-asset diversification.",
        "fix": ["project_clone + cfx_set_instrument to clone for a second symbol."],
    },
    "SINGLE_TIMEFRAME_ONLY": {
        "severity": "low",
        "what": "All survivors trade the same timeframe — single-horizon edge.",
        "fix": ["Re-run Builder on a different TF and merge databanks."],
    },
    "TINY_DATABANK": {
        "severity": "info",
        "what": "Few strategies in this databank — portfolio statistics may be noisy.",
        "fix": ["cfx_set_max_strategies to raise the cap, then re-run Builder."],
    },
    "LOW_PROFITABLE_RATE": {
        "severity": "high",
        "what": "Less than 20% of strategies are profitable — Builder isn't finding edge.",
        "fix": [
            "Tighten Builder Conditions.",
            "Change fitness criterion (cfx_set_fitness_criterion).",
            "Increase IS sample.",
        ],
    },
}


_METRIC_EXPLANATIONS: dict[str, dict[str, Any]] = {
    "fitness_oos": {
        "what": "Out-of-sample fitness — the strategy's performance on data it wasn't trained on.",
        "good_when": ">= 0.5 (usually) and at least 50% of fitness_is.",
        "bad_when": "<= 0.1 (no edge) or much less than fitness_is (overfit).",
    },
    "oos_is_ratio": {
        "what": "fitness_oos / fitness_is. Closer to 1.0 = consistent across train/test.",
        "good_when": ">= 0.6 ideally; >= 0.5 acceptable.",
        "bad_when": "< 0.5 (overfit); 0 (no OOS sample was configured).",
    },
    "drawdown_pct": {
        "what": "Maximum drawdown as a percentage of initial capital.",
        "good_when": "<= 20% for a defensive strategy.",
        "bad_when": ">= 40% for live deployment (psychologically painful, margin-call risk).",
    },
    "profit_to_dd_ratio": {
        "what": "Net profit divided by max drawdown — risk-adjusted return.",
        "good_when": ">= 3 (good), >= 5 (excellent).",
        "bad_when": "> 20 (suspiciously high, check for look-ahead).",
    },
    "trades": {
        "what": "Total number of completed trades in the backtest.",
        "good_when": ">= 100 (statistically meaningful).",
        "bad_when": "< 30 (too thin to draw conclusions).",
    },
    "history_years": {
        "what": "Span of history the strategy was tested on, derived from HistoryFrom/HistoryTo.",
        "good_when": ">= 3 years (covers multiple regimes).",
        "bad_when": "< 1 year (single regime).",
    },
    "trades_per_year": {
        "what": "Trade frequency (trades / history_years).",
        "good_when": "10-1000 for most retail systems.",
        "bad_when": "> 5000 (scalping that won't survive real costs).",
    },
}


class ExplainFindingArgs(BaseModel):
    code: str = Field(..., max_length=64, description="Finding code (e.g. 'OVERFIT_OOS_DEGRADATION').")


class ExplainMetricArgs(BaseModel):
    metric: str = Field(..., max_length=64, description="Metric name (e.g. 'fitness_oos').")


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Explain an audit finding code in plain English: what it means, "
            "why SQ flags it, and concrete tool calls to fix it. Returns "
            "'unknown_code' if the code isn't in the knowledge base."
        )
    )
    async def explain_finding(args: ExplainFindingArgs, ctx: Context) -> dict:  # noqa: ARG001
        try:
            entry = _FINDING_EXPLANATIONS.get(args.code)
            if entry is None:
                return {
                    "ok": True,
                    "code": args.code,
                    "known": False,
                    "hint": (
                        "Code not in the knowledge base — check audit.py source "
                        "or pass a different code."
                    ),
                    "available_codes": sorted(_FINDING_EXPLANATIONS.keys()),
                }
            return {
                "ok": True,
                "code": args.code,
                "known": True,
                "severity": entry["severity"],
                "explanation": entry["what"],
                "remediation": entry["fix"],
            }
        except OSError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Explain a derive_metrics field in plain English: what it measures, "
            "good vs bad ranges. Use to teach an agent what each metric means."
        )
    )
    async def explain_metric(args: ExplainMetricArgs, ctx: Context) -> dict:  # noqa: ARG001
        try:
            entry = _METRIC_EXPLANATIONS.get(args.metric)
            if entry is None:
                return {
                    "ok": True,
                    "metric": args.metric,
                    "known": False,
                    "available_metrics": sorted(_METRIC_EXPLANATIONS.keys()),
                }
            return {"ok": True, "metric": args.metric, "known": True, **entry}
        except OSError as exc:
            return safe_error_payload(exc)


__all__ = [
    "ExplainFindingArgs",
    "ExplainMetricArgs",
    "_FINDING_EXPLANATIONS",
    "_METRIC_EXPLANATIONS",
    "register",
]
