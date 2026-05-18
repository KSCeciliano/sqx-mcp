"""A/B comparison + correlation tools for SQ X databanks.

These tools answer two practical questions:

- ``portfolio_ab_test`` — "did my new Builder iteration produce a better
  databank than the previous one?" Compares two databanks (any project/name
  pair) on fitness distribution, dedup rate, and overlap by trade-sequence
  hash. Useful right after a re-run.

- ``databank_correlation_matrix`` — "are my 'top 20' strategies actually
  diverse, or are they all riding the same edge?" Computes Pearson
  correlation between the equity curves embedded in each ``.sqx`` (MEC
  sparklines parsed by ``parsers.sqx``) and flags strategies above a
  threshold so the agent can drop redundant ones before a portfolio export.

Both tools are read-only — they never touch the databanks or .cfx files.
"""

from __future__ import annotations

import math
from pathlib import Path
from statistics import mean, median
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp._validation import ValidationError, validate_databank_name, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.parsers.sqx import derive_metrics, parse_sqx
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.portfolio import _filter_min_trades, _rank_rows, _scan_databank

# ---- argument schemas ------------------------------------------------------


class PortfolioAbTestArgs(BaseModel):
    project_a: str
    databank_a: str = "Results"
    project_b: str
    databank_b: str = "Results"
    min_trades: int | None = Field(
        default=None,
        description="Drop strategies with fewer trades than this on BOTH sides before comparing.",
    )


class DatabankCorrelationArgs(BaseModel):
    project: str
    databank: str = "Results"
    top_n: int = Field(default=20, ge=2, le=200)
    rank_mode: str = Field(
        default="defensive",
        description=(
            "Pre-filter ranking mode (see portfolio_rank). 'defensive' uses the "
            "composite score; otherwise use a single metric name."
        ),
    )
    min_trades: int | None = None
    threshold: float = Field(
        default=0.85,
        ge=-1.0,
        le=1.0,
        description="Flag pairs whose absolute Pearson correlation >= this value.",
    )
    curve: str = Field(
        default="full",
        description="Which embedded equity curve to use: 'full', 'is' or 'oos'.",
    )


# ---- helpers ---------------------------------------------------------------


def _pearson(x: list[float], y: list[float]) -> float | None:
    """Sample Pearson correlation. Returns None if undefined."""
    n = min(len(x), len(y))
    if n < 3:
        return None
    x = x[:n]
    y = y[:n]
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _curve_to_returns(values: list[float]) -> list[float]:
    """Convert an equity curve to step-over-step returns. Skips non-finite values."""
    out: list[float] = []
    for prev, curr in zip(values, values[1:], strict=False):
        if not (math.isfinite(prev) and math.isfinite(curr)):
            continue
        if prev == 0:
            out.append(curr)
            continue
        out.append((curr - prev) / abs(prev))
    return out


def _load_curve(sqx_path: Path, which: str) -> list[float]:
    """Re-parse a .sqx and return the requested embedded equity curve."""
    info = parse_sqx(sqx_path)
    meta = info.meta
    if meta is None:
        return []
    if which == "is":
        return list(meta.equity_curve_is)
    if which == "oos":
        return list(meta.equity_curve_oos)
    return list(meta.equity_curve_full)


def _ab_summarize_side(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-side numeric summary used by portfolio_ab_test."""
    if not rows:
        return {
            "strategies": 0,
            "profitable_count": 0,
            "unique_trades_hashes": 0,
            "duplicate_rate_trades_hash": None,
        }

    profitable = sum(1 for r in rows if (r.get("net_profit") or 0) > 0)
    hashes = {r.get("trades_hash") for r in rows if r.get("trades_hash")}
    fitness_vals = [r["fitness_oos"] for r in rows if r.get("fitness_oos") is not None]
    dd_vals = [r["drawdown_pct"] for r in rows if r.get("drawdown_pct") is not None]
    pdd_vals = [
        r["profit_to_dd_ratio"]
        for r in rows
        if r.get("profit_to_dd_ratio") is not None
    ]
    return {
        "strategies": len(rows),
        "profitable_count": profitable,
        "unique_trades_hashes": len(hashes),
        "duplicate_rate_trades_hash": (
            round(1.0 - len(hashes) / len(rows), 4) if rows else None
        ),
        "fitness_oos": {
            "count": len(fitness_vals),
            "mean": round(mean(fitness_vals), 6) if fitness_vals else None,
            "median": round(median(fitness_vals), 6) if fitness_vals else None,
        },
        "drawdown_pct": {
            "count": len(dd_vals),
            "mean": round(mean(dd_vals), 4) if dd_vals else None,
            "median": round(median(dd_vals), 4) if dd_vals else None,
        },
        "profit_to_dd_ratio": {
            "count": len(pdd_vals),
            "mean": round(mean(pdd_vals), 4) if pdd_vals else None,
            "median": round(median(pdd_vals), 4) if pdd_vals else None,
        },
    }


def _verdict_on_metric(a: float | None, b: float | None, *, higher_is_better: bool) -> str:
    """Compare two scalar summaries — returns 'A', 'B', 'tie' or 'unknown'."""
    if a is None and b is None:
        return "unknown"
    if a is None:
        return "B"
    if b is None:
        return "A"
    if math.isclose(a, b, rel_tol=1e-6):
        return "tie"
    if higher_is_better:
        return "A" if a > b else "B"
    return "A" if a < b else "B"


def _overlap_by_hash(
    rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]]
) -> dict[str, Any]:
    hashes_a = {r["trades_hash"] for r in rows_a if r.get("trades_hash")}
    hashes_b = {r["trades_hash"] for r in rows_b if r.get("trades_hash")}
    in_both = hashes_a & hashes_b
    only_a = hashes_a - hashes_b
    only_b = hashes_b - hashes_a
    union = hashes_a | hashes_b
    jaccard = (len(in_both) / len(union)) if union else None
    return {
        "in_both_count": len(in_both),
        "only_a_count": len(only_a),
        "only_b_count": len(only_b),
        "jaccard": round(jaccard, 4) if jaccard is not None else None,
    }


def _correlation_matrix(
    name_to_curve: dict[str, list[float]],
    *,
    threshold: float,
) -> dict[str, Any]:
    """Compute Pearson on returns derived from curves, find highly-correlated pairs."""
    names = sorted(name_to_curve.keys())
    returns = {n: _curve_to_returns(name_to_curve[n]) for n in names}

    matrix: dict[str, dict[str, float | None]] = {n: {} for n in names}
    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            corr = _pearson(returns[a], returns[b])
            matrix[a][b] = round(corr, 4) if corr is not None else None
            matrix[b][a] = matrix[a][b]
            if corr is not None and abs(corr) >= threshold:
                pairs.append({"a": a, "b": b, "corr": round(corr, 4)})
        matrix[a][a] = 1.0

    pairs.sort(key=lambda p: abs(p["corr"]), reverse=True)
    avg_off_diag: float | None = None
    flat: list[float] = []
    for n, row in matrix.items():
        for k, v in row.items():
            if k == n or v is None:
                continue
            flat.append(abs(v))
    if flat:
        avg_off_diag = round(sum(flat) / len(flat), 4)

    return {
        "names": names,
        "matrix": matrix,
        "highly_correlated_pairs": pairs,
        "avg_abs_off_diagonal": avg_off_diag,
    }


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "A/B compare two databanks (typically: same project before/after a "
            "Builder re-run, or two competing Builder configs). Reports per-side "
            "summaries (fitness/DD/profitability), overlap by trades_hash (Jaccard), "
            "and a verdict line for each metric. Read-only."
        )
    )
    async def portfolio_ab_test(args: PortfolioAbTestArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            pa = validate_project_name(args.project_a)
            pb = validate_project_name(args.project_b)
            da = validate_databank_name(args.databank_a)
            db = validate_databank_name(args.databank_b)
            dir_a = eng.config.projects_dir / pa / "databanks" / da
            dir_b = eng.config.projects_dir / pb / "databanks" / db
            if not dir_a.exists():
                return {"ok": False, "error": f"databank A not found: {dir_a}"}
            if not dir_b.exists():
                return {"ok": False, "error": f"databank B not found: {dir_b}"}

            rows_a, bad_a = _scan_databank(dir_a)
            rows_b, bad_b = _scan_databank(dir_b)
            rows_a = _filter_min_trades(rows_a, args.min_trades)
            rows_b = _filter_min_trades(rows_b, args.min_trades)

            sum_a = _ab_summarize_side(rows_a)
            sum_b = _ab_summarize_side(rows_b)
            overlap = _overlap_by_hash(rows_a, rows_b)

            verdicts = {
                "fitness_oos_mean": _verdict_on_metric(
                    sum_a["fitness_oos"]["mean"],
                    sum_b["fitness_oos"]["mean"],
                    higher_is_better=True,
                ),
                "drawdown_pct_mean": _verdict_on_metric(
                    sum_a["drawdown_pct"]["mean"],
                    sum_b["drawdown_pct"]["mean"],
                    higher_is_better=False,
                ),
                "profit_to_dd_mean": _verdict_on_metric(
                    sum_a["profit_to_dd_ratio"]["mean"],
                    sum_b["profit_to_dd_ratio"]["mean"],
                    higher_is_better=True,
                ),
                "profitable_count": _verdict_on_metric(
                    sum_a["profitable_count"],
                    sum_b["profitable_count"],
                    higher_is_better=True,
                ),
                "diversity_unique_hashes": _verdict_on_metric(
                    sum_a["unique_trades_hashes"],
                    sum_b["unique_trades_hashes"],
                    higher_is_better=True,
                ),
            }

            return {
                "ok": True,
                "a": {"project": pa, "databank": da, "unparseable": len(bad_a)},
                "b": {"project": pb, "databank": db, "unparseable": len(bad_b)},
                "summary_a": sum_a,
                "summary_b": sum_b,
                "overlap_by_trades_hash": overlap,
                "verdicts": verdicts,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Pearson correlation matrix on the equity curves of the top-N strategies "
            "in a databank. Pre-ranks by rank_mode (defensive composite by default), "
            "parses MEC sparklines from each .sqx, converts to per-step returns, "
            "and flags pairs whose |corr| >= threshold. Use this before exporting "
            "a portfolio so you don't ship 10 nearly-identical strategies. Read-only."
        )
    )
    async def databank_correlation_matrix(
        args: DatabankCorrelationArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            project = validate_project_name(args.project)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            if args.curve not in ("full", "is", "oos"):
                return {"ok": False, "error": "curve must be one of: full, is, oos"}

            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            ranked = _rank_rows(rows, args.rank_mode)
            top = ranked[: args.top_n]

            name_to_curve: dict[str, list[float]] = {}
            skipped: list[dict[str, str]] = []
            for r in top:
                try:
                    curve = _load_curve(Path(r["file"]), args.curve)
                except (ValueError, OSError) as exc:
                    skipped.append({"rel": r["rel"], "reason": str(exc)})
                    continue
                if len(curve) < 4:
                    skipped.append({"rel": r["rel"], "reason": "curve too short"})
                    continue
                name_to_curve[r["rel"]] = curve

            mat = _correlation_matrix(name_to_curve, threshold=args.threshold)

            return {
                "ok": True,
                "project": project,
                "databank": databank,
                "curve": args.curve,
                "rank_mode": args.rank_mode,
                "top_n_requested": args.top_n,
                "top_considered": len(top),
                "included_in_matrix": len(name_to_curve),
                "skipped_count": len(skipped),
                "skipped": skipped[:20],
                "unparseable_databank_rows": len(bad),
                "threshold": args.threshold,
                "highly_correlated_pairs_count": len(mat["highly_correlated_pairs"]),
                "highly_correlated_pairs": mat["highly_correlated_pairs"],
                "avg_abs_off_diagonal": mat["avg_abs_off_diagonal"],
                "matrix": mat["matrix"],
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


# Used in tests
__all__ = [
    "DatabankCorrelationArgs",
    "PortfolioAbTestArgs",
    "_ab_summarize_side",
    "_correlation_matrix",
    "_curve_to_returns",
    "_load_curve",
    "_overlap_by_hash",
    "_pearson",
    "_verdict_on_metric",
    "register",
]


# derive_metrics is re-exported only to keep mypy happy in tests that import
# both this module and the parser through it; deliberately unused here.
_ = derive_metrics
