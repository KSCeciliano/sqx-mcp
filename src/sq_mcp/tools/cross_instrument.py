"""Cross-instrument portfolio construction.

Build a mixed-asset portfolio across symbols and asset classes. The
existing `portfolio.py` and `analytics.py` modules assume strategies
share a databank (same project, same symbol). These tools relax that:
given strategies from multiple projects / multiple symbols, recommend
diversification weights and flag concentration.

Tools:

- ``cross_instrument_group_by_symbol`` — given a list of strategy
  dicts each with a `symbol` field, group them and report per-symbol
  counts + aggregate fitness.
- ``cross_instrument_diversification_score`` — score the portfolio's
  diversification across symbols using Herfindahl normalized index
  on symbol weights.
- ``cross_instrument_recommend_caps`` — given strategies + a per-symbol
  cap (e.g. "no more than 25% on any one symbol"), report which
  strategies fit within the caps.
- ``cross_instrument_asset_class_split`` — classify symbols into
  asset classes (crypto / forex / futures / equities / unknown)
  and report split percentages by trade count and weight.
- ``cross_instrument_correlation_groups`` — group symbols by likely
  correlation: BTC/ETH together, EURUSD/GBPUSD together, etc.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# ---- argument schemas ------------------------------------------------------


class CrossInstrumentStrategy(BaseModel):
    name: str
    symbol: str
    weight: float = Field(1.0, gt=0)
    fitness: float | None = None


class GroupBySymbolArgs(BaseModel):
    strategies: list[CrossInstrumentStrategy] = Field(..., min_length=1, max_length=500)


class DiversificationArgs(BaseModel):
    strategies: list[CrossInstrumentStrategy] = Field(..., min_length=2, max_length=500)


class RecommendCapsArgs(BaseModel):
    strategies: list[CrossInstrumentStrategy] = Field(..., min_length=1, max_length=500)
    per_symbol_cap_pct: float = Field(25.0, gt=0, le=100)


class AssetClassSplitArgs(BaseModel):
    strategies: list[CrossInstrumentStrategy] = Field(..., min_length=1, max_length=500)


# Common asset-class buckets keyed by symbol prefix / suffix.
CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "BTC", "ETH", "USD")
FOREX_MAJORS = ("EUR", "GBP", "USD", "JPY", "CHF", "CAD", "AUD", "NZD")
FUTURES_SYMBOLS = {"ES", "NQ", "YM", "RTY", "CL", "GC", "SI", "HG", "ZB", "ZN", "ZF"}


def _classify_asset(symbol: str) -> str:
    s = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    # Forex check first — 6 chars and both parts in forex majors
    if len(s) == 6 and s[:3] in FOREX_MAJORS and s[3:] in FOREX_MAJORS:
        return "forex"
    if s.endswith(".US") or s in {"SPY", "QQQ", "IWM", "DIA"}:
        return "equities"
    if any(s.endswith(q) for q in CRYPTO_QUOTES) and len(s) > 4:
        # crypto pattern: BASE + quote where len > 4
        return "crypto"
    if s in FUTURES_SYMBOLS:
        return "futures"
    return "unknown"


def _group_by_symbol(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        sym = r["symbol"]
        g = groups.setdefault(
            sym,
            {
                "symbol": sym,
                "n_strategies": 0,
                "total_weight": 0.0,
                "fitness_values": [],
                "names": [],
            },
        )
        g["n_strategies"] += 1
        g["total_weight"] += r["weight"]
        if r.get("fitness") is not None:
            g["fitness_values"].append(r["fitness"])
        g["names"].append(r["name"])
    # Compute per-group stats
    out = []
    total_weight = sum(g["total_weight"] for g in groups.values())
    for g in groups.values():
        fits = g["fitness_values"]
        g["weight_share_pct"] = (
            round(100.0 * g["total_weight"] / total_weight, 4) if total_weight else 0
        )
        g["mean_fitness"] = round(sum(fits) / len(fits), 4) if fits else None
        # Don't return the raw fitness array (capped)
        g["fitness_count"] = len(fits)
        del g["fitness_values"]
        # Cap example name list
        g["names"] = g["names"][:10]
        out.append(g)
    out.sort(key=lambda g: g["weight_share_pct"], reverse=True)
    return {"groups": out, "n_unique_symbols": len(groups), "n_total_strategies": len(rows)}


def _diversification_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = _group_by_symbol(rows)
    n = grouped["n_unique_symbols"]
    if n < 2:
        return {
            "score": 0,
            "verdict": "single_symbol",
            "n_unique_symbols": n,
        }
    shares = [g["weight_share_pct"] / 100.0 for g in grouped["groups"]]
    hhi = sum(s * s for s in shares)
    normalized = (hhi - 1.0 / n) / (1.0 - 1.0 / n) if n > 1 else 1.0
    # diversification score = 100 × (1 - normalized HHI)
    div_score = 100.0 * (1.0 - normalized)
    verdict = (
        "well_diversified" if div_score >= 75
        else "moderately_diversified" if div_score >= 40
        else "concentrated"
    )
    return {
        "score": round(div_score, 2),
        "verdict": verdict,
        "n_unique_symbols": n,
        "hhi": round(hhi, 6),
        "hhi_normalized": round(normalized, 6),
        "top_symbol_share_pct": grouped["groups"][0]["weight_share_pct"],
    }


def _recommend_caps(
    rows: list[dict[str, Any]], cap_pct: float
) -> dict[str, Any]:
    grouped = _group_by_symbol(rows)
    violators: list[dict[str, Any]] = []
    for g in grouped["groups"]:
        if g["weight_share_pct"] > cap_pct:
            violators.append(
                {
                    "symbol": g["symbol"],
                    "weight_share_pct": g["weight_share_pct"],
                    "cap_pct": cap_pct,
                    "excess_pct": round(g["weight_share_pct"] - cap_pct, 4),
                    "n_strategies": g["n_strategies"],
                }
            )
    return {
        "per_symbol_cap_pct": cap_pct,
        "n_violations": len(violators),
        "violators": violators,
        "fits_caps": len(violators) == 0,
        "n_unique_symbols": grouped["n_unique_symbols"],
    }


def _asset_class_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    total_weight = 0.0
    for r in rows:
        klass = _classify_asset(r["symbol"])
        b = buckets.setdefault(
            klass,
            {"asset_class": klass, "n_strategies": 0, "weight": 0.0, "symbols": set()},
        )
        b["n_strategies"] += 1
        b["weight"] += r["weight"]
        b["symbols"].add(r["symbol"])
        total_weight += r["weight"]
    out = []
    for klass, b in buckets.items():
        out.append({
            "asset_class": klass,
            "n_strategies": b["n_strategies"],
            "n_unique_symbols": len(b["symbols"]),
            "weight_share_pct": round(100.0 * b["weight"] / total_weight, 4)
                if total_weight else 0,
            "symbols": sorted(b["symbols"])[:20],
        })
    out.sort(key=lambda b: b["weight_share_pct"], reverse=True)
    return {"buckets": out, "n_asset_classes": len(buckets)}


def _correlation_groups(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Group symbols into clusters likely to be correlated.

    Heuristic only — no actual correlation computation. Examples:
        - crypto majors (BTC*, ETH*) → "crypto_majors"
        - crypto alts (everything else *USDT) → "crypto_alts"
        - USD-quoted forex → "forex_usd_quote"
        - JPY pairs → "forex_jpy"
    """
    clusters: dict[str, list[str]] = {}
    for r in rows:
        s = r["symbol"].upper().replace("/", "").replace("-", "")
        bucket = "other"
        if s.startswith(("BTC", "ETH")) and any(s.endswith(q) for q in CRYPTO_QUOTES):
            bucket = "crypto_majors"
        elif any(s.endswith(q) for q in ("USDT", "USDC", "BUSD")):
            bucket = "crypto_alts"
        elif s.endswith("JPY") and len(s) == 6:
            bucket = "forex_jpy"
        elif s.endswith("USD") and len(s) == 6:
            bucket = "forex_usd_quote"
        elif s.startswith("USD") and len(s) == 6:
            bucket = "forex_usd_base"
        elif _classify_asset(r["symbol"]) == "futures":
            bucket = "futures"
        clusters.setdefault(bucket, []).append(r["symbol"])
    out = []
    for c, syms in clusters.items():
        out.append({"cluster": c, "n_strategies": len(syms), "symbols": sorted(set(syms))})
    out.sort(key=lambda x: x["n_strategies"], reverse=True)
    return {"clusters": out, "n_clusters": len(clusters)}


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Group a list of strategies by symbol. Each group reports "
            "n_strategies, total weight, weight share %, mean fitness, and "
            "example names. Read-only."
        )
    )
    async def cross_instrument_group_by_symbol(args: GroupBySymbolArgs) -> dict:
        return {
            "ok": True,
            **_group_by_symbol([s.model_dump() for s in args.strategies]),
        }

    @mcp.tool(
        description=(
            "Diversification score across symbols (0-100, higher = more diverse). "
            "Uses normalized Herfindahl-Hirschman index on symbol weights. "
            "Returns a verdict: well_diversified / moderately_diversified / "
            "concentrated. Read-only."
        )
    )
    async def cross_instrument_diversification_score(args: DiversificationArgs) -> dict:
        return {
            "ok": True,
            **_diversification_score([s.model_dump() for s in args.strategies]),
        }

    @mcp.tool(
        description=(
            "Check whether any symbol's weight share exceeds per_symbol_cap_pct. "
            "Returns the list of violators with their excess %. Use to validate "
            "a portfolio against a max-concentration policy."
        )
    )
    async def cross_instrument_recommend_caps(args: RecommendCapsArgs) -> dict:
        return {
            "ok": True,
            **_recommend_caps(
                [s.model_dump() for s in args.strategies], args.per_symbol_cap_pct
            ),
        }

    @mcp.tool(
        description=(
            "Classify each strategy's symbol into an asset class (crypto / forex "
            "/ futures / equities / unknown) and report split % by weight and "
            "count. Useful for portfolio-level allocation reports."
        )
    )
    async def cross_instrument_asset_class_split(args: AssetClassSplitArgs) -> dict:
        return {
            "ok": True,
            **_asset_class_split([s.model_dump() for s in args.strategies]),
        }

    @mcp.tool(
        description=(
            "Group symbols into likely-correlated clusters by heuristic naming: "
            "crypto majors (BTC/ETH), crypto alts, forex USD-quote, JPY pairs, "
            "futures, other. Use to assess concentration risk beyond just unique "
            "symbol counts."
        )
    )
    async def cross_instrument_correlation_groups(args: AssetClassSplitArgs) -> dict:
        return {
            "ok": True,
            **_correlation_groups([s.model_dump() for s in args.strategies]),
        }
