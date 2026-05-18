"""Portfolio-level tools: dedupe, ranking, summary, diversity selection.

These operate on a databank folder (a collection of .sqx files) rather than
a single strategy. They are the building blocks for selecting which subset
of a builder's output to promote to retest / OOS / live deployment.

All tools read .sqx files directly from disk (no engine call), so they
work even when the SQ X engine is offline.
"""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    validate_databank_name,
    validate_project_name,
)
from sq_mcp.engine import EngineError
from sq_mcp.parsers.sqx import derive_metrics, parse_sqx
from sq_mcp.tools._common import get_engine, safe_error_payload

# Which fingerprint slot to group on. "trades_hash" matches strategies whose
# backtest produced the exact same trade sequence — that is the strongest
# duplicate signal SQ X gives us. "exact" is the engine's own fingerprint
# (also includes structure + parameters); same hash means same strategy.
_DEDUPE_KEYS = ("trades_hash", "exact")

# Ranking modes: each one specifies the metric to read off the derived dict
# and whether higher is better.
_RANKING_MODES: dict[str, tuple[str, bool]] = {
    "fitness_oos": ("fitness_oos", True),
    "fitness_is": ("fitness_is", True),
    "fitness_full": ("fitness_full", True),
    "profit_to_dd": ("profit_to_dd_ratio", True),
    "return_pct": ("return_pct", True),
    "trades": ("trades", True),
    "lowest_drawdown_pct": ("drawdown_pct", False),
}


class PortfolioBaseArgs(BaseModel):
    project: str = Field(..., description="Project name.")
    databank: str = Field("Results", description="Databank folder name under the project.")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_databank(cls, v: str) -> str:
        return validate_databank_name(v)


class PortfolioDedupeArgs(PortfolioBaseArgs):
    by: Literal["trades_hash", "exact"] = Field(
        "trades_hash",
        description=(
            "Dedupe key. 'trades_hash' = same trade sequence (most defensive "
            "duplicate signal). 'exact' = SQ's full identity hash (structure+params)."
        ),
    )
    keep: Literal["best_oos", "best_profit_to_dd", "first"] = Field(
        "best_oos",
        description=(
            "Within each duplicate group, which strategy to flag as the survivor. "
            "'best_oos' = highest fitness_oos. 'best_profit_to_dd' = best risk-adjusted. "
            "'first' = lexicographic first filename."
        ),
    )


class PortfolioRankArgs(PortfolioBaseArgs):
    mode: Literal[
        "fitness_oos",
        "fitness_is",
        "fitness_full",
        "profit_to_dd",
        "return_pct",
        "trades",
        "lowest_drawdown_pct",
        "defensive",
    ] = Field(
        "defensive",
        description=(
            "Ranking mode. 'defensive' = composite score penalizing overfit "
            "(low OOS/IS ratio) and large drawdown. Use single-metric modes "
            "when you want a clean baseline."
        ),
    )
    top_n: int = Field(20, ge=1, le=1000, description="Number of top strategies to return.")
    min_trades: int | None = Field(
        None, ge=1, description="Optional minimum trade count filter."
    )


class PortfolioSummaryArgs(PortfolioBaseArgs):
    pass


class PortfolioSelectDiverseArgs(PortfolioBaseArgs):
    n: int = Field(5, ge=1, le=50, description="How many strategies to select.")
    rank_mode: Literal[
        "fitness_oos", "fitness_is", "fitness_full", "profit_to_dd", "defensive"
    ] = Field(
        "defensive",
        description=(
            "How to rank within each fingerprint bucket — the top scorer in "
            "each bucket is the bucket's representative."
        ),
    )
    min_trades: int | None = Field(None, ge=1)
    diversify_by: Literal["trades_hash", "exact", "symbol_tf"] = Field(
        "trades_hash",
        description=(
            "Diversity axis. 'trades_hash' / 'exact' avoid backtest twins; "
            "'symbol_tf' picks across different (symbol, timeframe) buckets."
        ),
    )


# ---- helpers ---------------------------------------------------------------


def _scan_databank(db_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read every .sqx under db_dir. Returns (metrics_rows, unparseable_rows).

    Each metrics row is `{"file": ..., "rel": ..., **derived_metrics, "fingerprint_exact": ...}`.
    """
    rows: list[dict[str, Any]] = []
    bad: list[dict[str, Any]] = []
    for sqx_path in sorted(db_dir.rglob("*.sqx")):
        try:
            info = parse_sqx(sqx_path)
        except (ValueError, OSError) as exc:
            bad.append({"file": str(sqx_path.relative_to(db_dir)), "reason": str(exc)})
            continue
        m = derive_metrics(info)
        m["file"] = str(sqx_path)
        m["rel"] = str(sqx_path.relative_to(db_dir))
        m["fingerprint_exact"] = info.fingerprint.exact if info.fingerprint else None
        rows.append(m)
    return rows, bad


def _composite_score(m: dict[str, Any]) -> float | None:
    """Defensive composite ranking score.

    Returns None if there isn't enough signal to compute it (i.e. no fitness).
    The formula rewards OOS fitness and penalizes:
      * overfit (low OOS/IS ratio)
      * deep drawdown (>25% of capital)
      * too few trades (<100 dampened proportionally)
    """
    base = m.get("fitness_oos") or m.get("fitness_full") or m.get("fitness_is")
    if base is None:
        return None
    score = float(base)

    oos_is = m.get("oos_is_ratio")
    if oos_is is not None:
        # Penalize overfit: ratio<0.5 -> halve, ratio<0.25 -> quarter.
        score *= max(0.1, min(1.0, float(oos_is) * 2.0))

    dd_pct = m.get("drawdown_pct")
    if dd_pct is not None and dd_pct > 25.0:
        score *= 25.0 / float(dd_pct)

    trades = m.get("trades")
    if trades is not None and trades < 100:
        score *= max(0.1, float(trades) / 100.0)

    return round(score, 6)


def _pick_best_in_group(rows: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    """Pick the survivor of a duplicate group by the chosen strategy."""
    if strategy == "first":
        return min(rows, key=lambda r: r["rel"])

    metric_name = (
        "fitness_oos" if strategy == "best_oos" else "profit_to_dd_ratio"
    )

    def keyfn(r: dict[str, Any]) -> float:
        v = r.get(metric_name)
        return float(v) if isinstance(v, (int, float)) else float("-inf")

    return max(rows, key=keyfn)


def _rank_rows(
    rows: list[dict[str, Any]], mode: str
) -> list[dict[str, Any]]:
    """Sort rows by the chosen mode. Items with missing metric sink to the bottom."""
    if mode == "defensive":
        for r in rows:
            r["composite_score"] = _composite_score(r)
        key_field, descending = "composite_score", True
    else:
        key_field, descending = _RANKING_MODES[mode]

    sentinel = float("-inf") if descending else float("inf")

    def keyfn(r: dict[str, Any]) -> float:
        v = r.get(key_field)
        return float(v) if isinstance(v, (int, float)) else sentinel

    return sorted(rows, key=keyfn, reverse=descending)


def _filter_min_trades(rows: list[dict[str, Any]], min_trades: int | None) -> list[dict[str, Any]]:
    if min_trades is None:
        return rows
    return [r for r in rows if (r.get("trades") or 0) >= min_trades]


def _summary_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate stats over a list of metric rows."""
    if not rows:
        return {"strategies": 0}

    def _vals(key: str) -> list[float]:
        return [
            float(r[key])
            for r in rows
            if r.get(key) is not None and isinstance(r[key], (int, float))
        ]

    def _stats(key: str) -> dict[str, float | None]:
        vals = _vals(key)
        if not vals:
            return {"count": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None}
        return {
            "count": len(vals),
            "mean": round(statistics.fmean(vals), 6),
            "median": round(statistics.median(vals), 6),
            "min": round(min(vals), 6),
            "max": round(max(vals), 6),
            "stdev": round(statistics.pstdev(vals), 6) if len(vals) > 1 else 0.0,
        }

    symbol_counts = Counter(r.get("symbol") or "(unknown)" for r in rows)
    tf_counts = Counter(r.get("timeframe") or "(unknown)" for r in rows)

    trades = _vals("trades")
    oos_is_ratios = _vals("oos_is_ratio")
    overfit_count = sum(1 for v in oos_is_ratios if v < 0.5)
    profitable = sum(1 for r in rows if (r.get("net_profit") or 0) > 0)
    has_fingerprint = sum(
        1 for r in rows if r.get("fingerprint_exact") or r.get("trades_hash")
    )
    unique_trades_hashes = len({r.get("trades_hash") for r in rows if r.get("trades_hash")})
    unique_exact = len({r.get("fingerprint_exact") for r in rows if r.get("fingerprint_exact")})

    return {
        "strategies": len(rows),
        "profitable_count": profitable,
        "with_fingerprint": has_fingerprint,
        "unique_trades_hashes": unique_trades_hashes,
        "unique_exact_fingerprints": unique_exact,
        "duplicate_rate_trades_hash": (
            round(1 - (unique_trades_hashes / len(rows)), 4)
            if unique_trades_hashes
            else None
        ),
        "overfit_oos_under_0p5": overfit_count,
        "by_symbol": dict(symbol_counts.most_common()),
        "by_timeframe": dict(tf_counts.most_common()),
        "trades": _stats("trades"),
        "net_profit": _stats("net_profit"),
        "fitness_is": _stats("fitness_is"),
        "fitness_oos": _stats("fitness_oos"),
        "drawdown_pct": _stats("drawdown_pct"),
        "profit_to_dd_ratio": _stats("profit_to_dd_ratio"),
        "oos_is_ratio": _stats("oos_is_ratio"),
        "history_years": _stats("history_years"),
        "trades_total": int(sum(trades)) if trades else 0,
    }


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """Bucket rows by a key. Rows with no value go under '__no_key__'."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if key == "symbol_tf":
            k = f"{r.get('symbol') or '?'}_{r.get('timeframe') or '?'}"
        else:
            k = r.get(key) if key == "trades_hash" else r.get("fingerprint_exact")
            k = str(k) if k else "__no_key__"
        groups.setdefault(k, []).append(r)
    return groups


def _select_diverse(
    rows: list[dict[str, Any]], *, n: int, rank_mode: str, diversify_by: str
) -> list[dict[str, Any]]:
    """Pick up to N strategies from different fingerprint / symbol_tf buckets.

    Strategy: rank within each bucket, then round-robin draw the top of each
    bucket. This yields a portfolio that prefers diversity over raw score.
    """
    groups = _group_by(rows, diversify_by)
    ranked_groups = [
        _rank_rows(grp, rank_mode) for grp in groups.values()
    ]
    # Sort buckets by their best-row score so we draw the strongest buckets first.
    def bucket_score(grp: list[dict[str, Any]]) -> float:
        if not grp:
            return float("-inf")
        if rank_mode == "defensive":
            v = grp[0].get("composite_score")
        else:
            v = grp[0].get(_RANKING_MODES[rank_mode][0])
        return float(v) if isinstance(v, (int, float)) else float("-inf")

    ranked_groups.sort(key=bucket_score, reverse=True)

    picks: list[dict[str, Any]] = []
    indices = [0] * len(ranked_groups)
    while len(picks) < n:
        progressed = False
        for i, grp in enumerate(ranked_groups):
            if indices[i] < len(grp):
                picks.append(grp[indices[i]])
                indices[i] += 1
                progressed = True
                if len(picks) >= n:
                    break
        if not progressed:
            break
    return picks


# ---- public tools ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Find duplicate strategies in a databank by trades_hash (default — same "
            "trade sequence => functionally identical) or by SQ's full identity hash. "
            "Returns each duplicate group with the survivor flagged and the redundant "
            "files listed. Use this before promoting to a retest/optimizer input "
            "databank — duplicates inflate the candidate pool without adding signal."
        )
    )
    async def portfolio_dedupe(args: PortfolioDedupeArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = eng.config.projects_dir / args.project / "databanks" / args.databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank directory not found: {db_dir}"}

            rows, bad = _scan_databank(db_dir)
            groups: dict[str, list[dict[str, Any]]] = {}
            no_key: list[dict[str, Any]] = []
            for r in rows:
                key = r.get("trades_hash") if args.by == "trades_hash" else r.get("fingerprint_exact")
                if not key:
                    no_key.append(r)
                    continue
                groups.setdefault(str(key), []).append(r)

            duplicate_groups: list[dict[str, Any]] = []
            redundant: list[str] = []
            survivors: list[str] = []
            for key, grp in groups.items():
                if len(grp) < 2:
                    survivors.append(grp[0]["rel"])
                    continue
                best = _pick_best_in_group(grp, args.keep)
                duplicate_groups.append(
                    {
                        "key": key,
                        "size": len(grp),
                        "survivor": best["rel"],
                        "survivor_metrics": {
                            "fitness_oos": best.get("fitness_oos"),
                            "profit_to_dd_ratio": best.get("profit_to_dd_ratio"),
                            "trades": best.get("trades"),
                            "drawdown_pct": best.get("drawdown_pct"),
                        },
                        "redundant": sorted(
                            [r["rel"] for r in grp if r["rel"] != best["rel"]]
                        ),
                    }
                )
                survivors.append(best["rel"])
                redundant.extend(r["rel"] for r in grp if r["rel"] != best["rel"])

            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "dedupe_by": args.by,
                "keep": args.keep,
                "total_scanned": len(rows) + len(bad),
                "parseable": len(rows),
                "unparseable_count": len(bad),
                "groups_total": len(groups),
                "duplicate_groups_count": len(duplicate_groups),
                "duplicate_groups": duplicate_groups,
                "survivors_count": len(survivors),
                "redundant_count": len(redundant),
                "without_key_count": len(no_key),
                "without_key": [r["rel"] for r in no_key[:20]],
                "unparseable": bad[:10],
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Rank strategies in a databank by a chosen metric. Default 'defensive' "
            "mode is a composite that rewards OOS fitness and penalizes overfit "
            "(low OOS/IS ratio), large drawdown (>25%), and small sample size "
            "(<100 trades). Single-metric modes: fitness_oos, fitness_is, fitness_full, "
            "profit_to_dd, return_pct, trades, lowest_drawdown_pct. Returns top N."
        )
    )
    async def portfolio_rank(args: PortfolioRankArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = eng.config.projects_dir / args.project / "databanks" / args.databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank directory not found: {db_dir}"}

            rows, bad = _scan_databank(db_dir)
            filtered = _filter_min_trades(rows, args.min_trades)
            ranked = _rank_rows(filtered, args.mode)
            top = ranked[: args.top_n]

            def _row_view(r: dict[str, Any]) -> dict[str, Any]:
                return {
                    "rel": r["rel"],
                    "strategy_name": r.get("strategy_name"),
                    "symbol": r.get("symbol"),
                    "timeframe": r.get("timeframe"),
                    "trades": r.get("trades"),
                    "fitness_is": r.get("fitness_is"),
                    "fitness_oos": r.get("fitness_oos"),
                    "fitness_full": r.get("fitness_full"),
                    "oos_is_ratio": r.get("oos_is_ratio"),
                    "drawdown_pct": r.get("drawdown_pct"),
                    "profit_to_dd_ratio": r.get("profit_to_dd_ratio"),
                    "return_pct": r.get("return_pct"),
                    "composite_score": r.get("composite_score"),
                }

            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "mode": args.mode,
                "min_trades": args.min_trades,
                "scanned": len(rows),
                "filtered": len(filtered),
                "unparseable_count": len(bad),
                "top": [_row_view(r) for r in top],
                "count_returned": len(top),
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Aggregate statistics across an entire databank: how many strategies, "
            "how many profitable, fitness/DD/trades distributions, OOS-ratio overfit "
            "count, symbol/TF spread, duplicate rate by trades_hash. Use this as a "
            "first look at any builder output before deciding what to keep."
        )
    )
    async def portfolio_summary(args: PortfolioSummaryArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = eng.config.projects_dir / args.project / "databanks" / args.databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank directory not found: {db_dir}"}

            rows, bad = _scan_databank(db_dir)
            summary = _summary_for_rows(rows)
            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "unparseable_count": len(bad),
                "summary": summary,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Select N strategies from a databank, maximizing diversity. Buckets "
            "strategies by trades_hash / exact fingerprint / (symbol,timeframe), "
            "ranks within each bucket, then round-robins the top picks. The result is "
            "a portfolio that avoids backtest twins. Pair with portfolio_dedupe for "
            "pre-deployment selection."
        )
    )
    async def portfolio_select_diverse(
        args: PortfolioSelectDiverseArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = eng.config.projects_dir / args.project / "databanks" / args.databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank directory not found: {db_dir}"}

            rows, bad = _scan_databank(db_dir)
            filtered = _filter_min_trades(rows, args.min_trades)
            picks = _select_diverse(
                filtered,
                n=args.n,
                rank_mode=args.rank_mode,
                diversify_by=args.diversify_by,
            )

            view = [
                {
                    "rel": r["rel"],
                    "strategy_name": r.get("strategy_name"),
                    "symbol": r.get("symbol"),
                    "timeframe": r.get("timeframe"),
                    "trades": r.get("trades"),
                    "fitness_oos": r.get("fitness_oos"),
                    "drawdown_pct": r.get("drawdown_pct"),
                    "profit_to_dd_ratio": r.get("profit_to_dd_ratio"),
                    "trades_hash": r.get("trades_hash"),
                    "fingerprint_exact": r.get("fingerprint_exact"),
                    "composite_score": r.get("composite_score"),
                }
                for r in picks
            ]

            unique_keys = set()
            for r in picks:
                if args.diversify_by == "symbol_tf":
                    unique_keys.add(f"{r.get('symbol')}_{r.get('timeframe')}")
                elif args.diversify_by == "trades_hash":
                    unique_keys.add(r.get("trades_hash"))
                else:
                    unique_keys.add(r.get("fingerprint_exact"))

            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "requested": args.n,
                "scanned": len(rows),
                "filtered": len(filtered),
                "unparseable_count": len(bad),
                "diversify_by": args.diversify_by,
                "rank_mode": args.rank_mode,
                "selected_count": len(picks),
                "unique_diversity_keys": len(unique_keys),
                "selected": view,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


# Export internal helpers used by tests (and by potential cross-module callers).
__all__ = [
    "register",
    "_composite_score",
    "_pick_best_in_group",
    "_rank_rows",
    "_filter_min_trades",
    "_summary_for_rows",
    "_select_diverse",
    "_group_by",
    "_scan_databank",
    "_RANKING_MODES",
    "_DEDUPE_KEYS",
]
