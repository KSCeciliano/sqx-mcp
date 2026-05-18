"""Opinionated configuration advisor.

Given a stated goal ("yield-maximizing", "risk-min", "balanced", "smoke test")
and an asset class hint ("crypto", "forex", "futures"), suggest a coherent
bundle of cfx settings the user could apply via the lower-level ``cfx_set_*``
tools.

This is a pure heuristic — it does NOT read any project files. Think of it as
a knowledge-base lookup, not a model.

Tools:

- ``cfx_recommend_settings`` — return the recommendation bundle for a given
  (goal, asset_class) combination.
- ``cfx_recommend_for_symbol`` — same but inferred from the symbol name
  (BTCUSDT → crypto, EURUSD → forex, ES → futures, fallback → balanced).
"""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_symbol
from sq_mcp.tools._common import safe_error_payload

_GOALS = ("yield", "risk_min", "balanced", "smoke")
_ASSET_CLASSES = ("crypto", "forex", "futures", "equities", "unknown")


class CfxRecommendArgs(BaseModel):
    goal: str = Field(
        "balanced",
        description=f"One of: {', '.join(_GOALS)}.",
    )
    asset_class: str = Field(
        "unknown",
        description=f"One of: {', '.join(_ASSET_CLASSES)}.",
    )


class CfxRecommendForSymbolArgs(BaseModel):
    symbol: str
    goal: str = "balanced"

    @field_validator("symbol")
    @classmethod
    def _v_symbol(cls, v: str) -> str:
        return validate_symbol(v)


class CfxCompareAgainstRecommendationArgs(BaseModel):
    project: str
    goal: str = "balanced"
    asset_class: str = "unknown"


def _infer_asset_class(symbol: str) -> str:
    """Heuristic: classify a symbol by name patterns. Conservative — falls back to 'unknown'."""
    sym = symbol.upper()
    # Crypto pairs typically end in USDT/USDC/BUSD or contain BTC/ETH
    crypto_quote = ("USDT", "USDC", "BUSD", "DAI", "TUSD")
    crypto_base = ("BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "MATIC", "AVAX", "LINK")
    if any(sym.endswith(q) for q in crypto_quote) or any(b in sym for b in crypto_base):
        return "crypto"
    # Forex pairs are typically 6 letters, the second 3 a fiat
    if re.fullmatch(r"[A-Z]{6}", sym):
        fiat_quotes = ("USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK")
        if sym[3:] in fiat_quotes:
            return "forex"
    # Common futures roots
    if sym in ("ES", "NQ", "YM", "RTY", "CL", "GC", "SI", "ZN", "ZB", "ZW", "ZC"):
        return "futures"
    return "unknown"


def _recommend(goal: str, asset_class: str) -> dict[str, Any]:
    """The actual heuristic table. Returns the bundle the agent should apply."""
    # Base table per goal
    if goal == "yield":
        base = {
            "fitness_criterion": "NetProfit",
            "population_size": 200,
            "max_generations": 200,
            "in_sample_ratio_pct": 50,
            "max_strategies": 1000,
            "min_trades": 50,
            "money_management": "RiskFixedBalancePct",
            "mm_params": {"RiskedMoney": "2.0"},
        }
    elif goal == "risk_min":
        base = {
            "fitness_criterion": "ReturnDDRatio",
            "population_size": 200,
            "max_generations": 200,
            "in_sample_ratio_pct": 60,
            "max_strategies": 500,
            "min_trades": 100,
            "money_management": "RiskFixedBalancePct",
            "mm_params": {"RiskedMoney": "0.5"},
        }
    elif goal == "smoke":
        base = {
            "fitness_criterion": "NetProfit",
            "population_size": 20,
            "max_generations": 10,
            "in_sample_ratio_pct": 50,
            "max_strategies": 20,
            "min_trades": 10,
            "money_management": "FixedSize",
            "mm_params": {"Size": "0.1"},
        }
    else:  # balanced (default)
        base = {
            "fitness_criterion": "ReturnDDRatio",
            "population_size": 100,
            "max_generations": 100,
            "in_sample_ratio_pct": 50,
            "max_strategies": 1000,
            "min_trades": 50,
            "money_management": "RiskFixedBalancePct",
            "mm_params": {"RiskedMoney": "1.0"},
        }

    # Asset-class tweaks
    notes: list[str] = []
    if asset_class == "crypto":
        base["exit_on_friday"] = False
        base["limit_time_range"] = False
        base["slpt_value_type"] = "percent"
        base["min_sl_pct"] = 1.0
        base["max_sl_pct"] = 10.0
        base["min_pt_pct"] = 1.0
        base["max_pt_pct"] = 20.0
        notes.append("Crypto is 24/7 — disabled ExitOnFriday/LimitTimeRange.")
        notes.append("Crypto price moves are large — using percent-based SLPT.")
    elif asset_class == "forex":
        base["exit_on_friday"] = True
        base["slpt_value_type"] = "pips"
        base["min_sl_pips"] = 30
        base["max_sl_pips"] = 80
        base["min_pt_pips"] = 60
        base["max_pt_pips"] = 200
        notes.append("Forex closes Friday — keep ExitOnFriday=true.")
    elif asset_class == "futures":
        base["slpt_value_type"] = "money"
        notes.append("Futures: SL/PT in money units is more interpretable than pips.")
    return {
        "goal": goal,
        "asset_class": asset_class,
        "settings": base,
        "notes": notes,
        "tool_calls_to_apply": [
            "cfx_set_fitness_criterion (ranking_type=settings['fitness_criterion'])",
            "cfx_set_genetic_options (population_size, max_generations, in_sample_ratio_pct)",
            "cfx_set_max_strategies",
            "cfx_set_money_management (method_type=settings['money_management'], params=settings['mm_params'])",
            "cfx_set_trade_caps (exit_on_friday, limit_time_range)",
            "cfx_set_sl_pt_range (min_/max_sl_/pt_ * by value type)",
        ],
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Return a recommended bundle of cfx settings for a given goal "
            "(yield / risk_min / balanced / smoke) and asset_class "
            "(crypto / forex / futures / equities / unknown). Pure heuristic — "
            "the actual application is up to the agent via cfx_set_* tools."
        )
    )
    async def cfx_recommend_settings(
        args: CfxRecommendArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            if args.goal not in _GOALS:
                return {
                    "ok": False,
                    "error": f"unknown goal: {args.goal}. Valid: {', '.join(_GOALS)}",
                }
            if args.asset_class not in _ASSET_CLASSES:
                return {
                    "ok": False,
                    "error": f"unknown asset_class: {args.asset_class}. Valid: {', '.join(_ASSET_CLASSES)}",
                }
            return {"ok": True, **_recommend(args.goal, args.asset_class)}
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compare an existing project's current cfx settings against advisor "
            "recommendations for the given goal × asset_class. Returns per-field "
            "drift: which recommended settings differ from current. Read-only."
        )
    )
    async def cfx_compare_against_recommendation(
        args: CfxCompareAgainstRecommendationArgs, ctx: Context
    ) -> dict:
        try:
            from sq_mcp._validation import validate_project_name as _vpn
            from sq_mcp.tools._common import get_engine
            from sq_mcp.tools.cfx_advanced import _walk_first_buildlike_xml
            from sq_mcp.tools.cfx_config import _inspect_task_xml
            from sq_mcp.tools.cfx_lint import _analyze_cfx
            eng = get_engine(ctx)
            project = _vpn(args.project)
            cfx_path = eng.config.projects_dir / project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}

            # Read current
            raw = _walk_first_buildlike_xml(cfx_path)
            if raw is None:
                return {"ok": False, "error": "no Build/Optimize task XML"}
            inspect = _inspect_task_xml(raw)
            analysis = _analyze_cfx(cfx_path)

            # Build the comparable "current" dict
            current = {
                "fitness_criterion": (inspect.get("fitness") or {}).get("ranking_type"),
                "money_management": (inspect.get("money_management") or {}).get("active_method"),
                "population_size": analysis.population_size,
                "max_generations": analysis.max_generations,
                "in_sample_ratio_pct": analysis.is_ratio,
                "max_strategies": analysis.max_strategies,
            }

            rec = _recommend(args.goal, args.asset_class)
            rec_settings = rec["settings"]
            drift: list[dict[str, Any]] = []
            for key in (
                "fitness_criterion",
                "money_management",
                "population_size",
                "max_generations",
                "in_sample_ratio_pct",
                "max_strategies",
            ):
                cur_val = current.get(key)
                rec_val = rec_settings.get(key)
                if rec_val is None:
                    continue
                if cur_val != rec_val:
                    drift.append(
                        {
                            "field": key,
                            "current": cur_val,
                            "recommended": rec_val,
                        }
                    )

            return {
                "ok": True,
                "project": project,
                "goal": args.goal,
                "asset_class": args.asset_class,
                "current": current,
                "recommended": rec_settings,
                "drift_count": len(drift),
                "drift": drift,
                "fully_aligned": len(drift) == 0,
            }
        except (OSError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Infer asset_class from a symbol name (BTCUSDT → crypto, EURUSD → forex, "
            "ES → futures) and return cfx_recommend_settings for that class plus the "
            "given goal. Read-only."
        )
    )
    async def cfx_recommend_for_symbol(
        args: CfxRecommendForSymbolArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            asset_class = _infer_asset_class(args.symbol)
            return {
                "ok": True,
                "symbol": args.symbol,
                "inferred_asset_class": asset_class,
                **_recommend(args.goal, asset_class),
            }
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "CfxCompareAgainstRecommendationArgs",
    "CfxRecommendArgs",
    "CfxRecommendForSymbolArgs",
    "_ASSET_CLASSES",
    "_GOALS",
    "_infer_asset_class",
    "_recommend",
    "register",
]
