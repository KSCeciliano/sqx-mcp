"""Databank partitioning + filtered exports.

Already have ``databank_filter`` (in databanks.py) which mutates the databank
or exports survivors. This module is for *read-only* partitioning — answer
"how would the databank split if I filtered by drawdown < 20%?" without
touching anything on disk.

Tools:

- ``databank_partition`` — scan a databank, classify each strategy as
  above/below a threshold on a chosen metric, return both groups.
- ``databank_top_bottom`` — return the top-K and bottom-K rows by a metric
  in one shot (useful for "show me the best and worst").
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_databank_name, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.portfolio import _filter_min_trades, _rank_rows, _scan_databank

_PARTITIONABLE_METRICS = (
    "fitness_oos",
    "fitness_is",
    "fitness_full",
    "profit_to_dd_ratio",
    "return_pct",
    "drawdown_pct",
    "trades",
    "oos_is_ratio",
    "history_years",
)


class DatabankPartitionArgs(BaseModel):
    project: str
    databank: str = "Results"
    metric: str = Field(
        "fitness_oos",
        description=f"Which metric to partition on. One of: {', '.join(_PARTITIONABLE_METRICS)}.",
    )
    threshold: float = Field(
        ...,
        description=(
            "Strategies with metric >= threshold go to 'above'; the rest go "
            "to 'below'. Rows with metric=None are bucketed as 'missing'."
        ),
    )
    min_trades: int | None = None

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class DatabankTopBottomArgs(BaseModel):
    project: str
    databank: str = "Results"
    metric: str = Field(
        "fitness_oos",
        description=(
            "Metric to rank on. Use a name like 'fitness_oos' (descending = better) "
            "or 'lowest_drawdown_pct' (ascending = better; see _rank_rows)."
        ),
    )
    k: int = Field(5, ge=1, le=50, description="How many top and how many bottom rows.")
    min_trades: int | None = None

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


def _partition_rows(
    rows: list[dict[str, Any]], *, metric: str, threshold: float
) -> dict[str, list[dict[str, Any]]]:
    above: list[dict[str, Any]] = []
    below: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for r in rows:
        v = r.get(metric)
        if v is None:
            missing.append(r)
            continue
        if v >= threshold:
            above.append(r)
        else:
            below.append(r)
    return {"above": above, "below": below, "missing": missing}


def _slim_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "rel": r.get("rel"),
        "strategy_name": r.get("strategy_name"),
        "symbol": r.get("symbol"),
        "timeframe": r.get("timeframe"),
        "trades": r.get("trades"),
        "fitness_oos": r.get("fitness_oos"),
        "drawdown_pct": r.get("drawdown_pct"),
        "profit_to_dd_ratio": r.get("profit_to_dd_ratio"),
        "return_pct": r.get("return_pct"),
        "oos_is_ratio": r.get("oos_is_ratio"),
        "trades_hash": r.get("trades_hash"),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Read-only partition of a databank by metric ≥ threshold. Returns "
            "three buckets: above (qualifies), below (drops out), missing "
            "(metric not available). Use this to answer 'how many strategies "
            "in this databank have OOS/IS ratio >= 0.5?' without modifying anything."
        )
    )
    async def databank_partition(
        args: DatabankPartitionArgs, ctx: Context
    ) -> dict:
        try:
            if args.metric not in _PARTITIONABLE_METRICS:
                return {
                    "ok": False,
                    "error": (
                        f"unsupported metric: {args.metric}. Valid: "
                        f"{', '.join(_PARTITIONABLE_METRICS)}"
                    ),
                }
            eng = get_engine(ctx)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / args.project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            parts = _partition_rows(rows, metric=args.metric, threshold=args.threshold)
            return {
                "ok": True,
                "project": args.project,
                "databank": databank,
                "metric": args.metric,
                "threshold": args.threshold,
                "unparseable_count": len(bad),
                "scanned": len(rows),
                "above_count": len(parts["above"]),
                "below_count": len(parts["below"]),
                "missing_count": len(parts["missing"]),
                "above": [_slim_row(r) for r in parts["above"]],
                "below": [_slim_row(r) for r in parts["below"]],
                "missing": [_slim_row(r) for r in parts["missing"]],
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Return the top-K and bottom-K strategies of a databank by metric "
            "in a single call. Use for 'show me the best and worst' triage. "
            "Read-only."
        )
    )
    async def databank_top_bottom(
        args: DatabankTopBottomArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / args.project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            ranked = _rank_rows(rows, args.metric)
            top = ranked[: args.k]
            bottom = list(reversed(ranked[-args.k :])) if len(ranked) > args.k else []
            return {
                "ok": True,
                "project": args.project,
                "databank": databank,
                "metric": args.metric,
                "k": args.k,
                "unparseable_count": len(bad),
                "scanned": len(rows),
                "top": [_slim_row(r) for r in top],
                "bottom": [_slim_row(r) for r in bottom],
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "DatabankPartitionArgs",
    "DatabankTopBottomArgs",
    "_PARTITIONABLE_METRICS",
    "_partition_rows",
    "_slim_row",
    "register",
]
