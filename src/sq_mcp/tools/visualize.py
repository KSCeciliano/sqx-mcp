"""ASCII visualization helpers — sparklines, histograms, distribution charts.

Most MCP clients render Markdown / monospace, so an ASCII chart embedded in
a tool response is often more useful than a JSON blob the agent then has to
describe in prose. These tools return ready-to-display strings.

Tools:

- ``strategy_ascii_equity_chart`` — Braille / block-char sparkline of a
  strategy's MEC equity curve.
- ``databank_fitness_histogram`` — text histogram of fitness_oos values
  across a databank.
- ``databank_drawdown_histogram`` — text histogram of drawdown_pct.

All read-only.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_databank_name,
    validate_project_name,
)
from sq_mcp.engine import EngineError
from sq_mcp.parsers.sqx import parse_sqx
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.portfolio import _filter_min_trades, _scan_databank
from sq_mcp.tools.strategy_inspect import _select_curve

_BLOCK_CHARS = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")


class StrategyAsciiChartArgs(BaseModel):
    sqx_path: str
    width: int = Field(60, ge=10, le=240)
    curve: str = Field("full", description="full / is / oos.")


class DatabankHistogramArgs(BaseModel):
    project: str
    databank: str = "Results"
    metric: str = Field(
        "fitness_oos",
        description=(
            "Which metric to bin. One of: fitness_oos, drawdown_pct, "
            "profit_to_dd_ratio, return_pct, trades."
        ),
    )
    bins: int = Field(20, ge=5, le=80)
    width: int = Field(60, ge=10, le=160)
    min_trades: int | None = None

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


def _block_for_fraction(frac: float) -> str:
    """Pick a block character for a value in [0, 1]."""
    if frac <= 0:
        return _BLOCK_CHARS[0]
    if frac >= 1:
        return _BLOCK_CHARS[-1]
    idx = int(round(frac * (len(_BLOCK_CHARS) - 1)))
    return _BLOCK_CHARS[max(0, min(idx, len(_BLOCK_CHARS) - 1))]


def _ascii_sparkline(values: list[float], width: int) -> str:
    """Reduce a series to <width> chars of vertical-block sparkline."""
    if not values:
        return ""
    if len(values) <= width:
        bucketed = list(values)
    else:
        step = len(values) / width
        bucketed = [values[int(i * step)] for i in range(width)]
    finite = [v for v in bucketed if math.isfinite(v)]
    if not finite:
        return " " * width
    lo, hi = min(finite), max(finite)
    if hi == lo:
        return "▄" * len(bucketed)
    chars: list[str] = []
    for v in bucketed:
        if not math.isfinite(v):
            chars.append(" ")
            continue
        frac = (v - lo) / (hi - lo)
        chars.append(_block_for_fraction(frac))
    return "".join(chars)


def _ascii_histogram(
    values: list[float], *, bins: int, width: int
) -> dict[str, Any]:
    """Render a horizontal histogram. Returns counts + a Markdown-friendly string."""
    cleaned = [v for v in values if v is not None and math.isfinite(v)]
    if not cleaned:
        return {"bins": [], "ascii": "(no data)", "min": None, "max": None}
    lo, hi = min(cleaned), max(cleaned)
    if hi == lo:
        # all values identical → one bin
        return {
            "bins": [{"lo": lo, "hi": hi, "count": len(cleaned), "bar": "█" * width}],
            "ascii": f"{lo:>10.4g} ┤ {'█' * width}  ({len(cleaned)})",
            "min": lo,
            "max": hi,
        }
    span = (hi - lo) / bins
    counts = [0] * bins
    for v in cleaned:
        idx = int((v - lo) / span)
        if idx == bins:
            idx -= 1  # right-edge inclusivity
        counts[idx] += 1
    max_count = max(counts) or 1
    lines: list[str] = []
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(counts):
        bin_lo = lo + i * span
        bin_hi = bin_lo + span
        bar_w = int(round(c / max_count * width))
        bar = "█" * bar_w
        lines.append(f"{bin_lo:>10.4g} ┤ {bar}  ({c})")
        rows.append({"lo": bin_lo, "hi": bin_hi, "count": c, "bar_width": bar_w})
    return {
        "bins": rows,
        "ascii": "\n".join(lines),
        "min": lo,
        "max": hi,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "ASCII Unicode-block sparkline of a strategy's equity curve. Use as a "
            "quick visual sanity check ('does this curve look smooth or jagged?') "
            "without a plotting library. Read-only."
        )
    )
    async def strategy_ascii_equity_chart(
        args: StrategyAsciiChartArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            info = parse_sqx(p)
            curve = _select_curve(info, args.curve)
            if not curve:
                return {
                    "ok": True,
                    "sqx_path": str(p),
                    "ascii": "(no equity sparkline embedded)",
                    "samples": 0,
                }
            return {
                "ok": True,
                "sqx_path": str(p),
                "samples": len(curve),
                "curve_first": curve[0],
                "curve_last": curve[-1],
                "min": min(curve),
                "max": max(curve),
                "ascii": _ascii_sparkline(curve, args.width),
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "ASCII histogram of a metric across every strategy in a databank "
            "(default: fitness_oos). Returns the bin counts AND a "
            "Markdown-friendly text rendering with horizontal bars. Read-only."
        )
    )
    async def databank_metric_histogram(
        args: DatabankHistogramArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / args.project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            vals = [r.get(args.metric) for r in rows]
            hist = _ascii_histogram(vals, bins=args.bins, width=args.width)
            return {
                "ok": True,
                "project": args.project,
                "databank": databank,
                "metric": args.metric,
                "sample_size": sum(1 for v in vals if v is not None),
                "unparseable_count": len(bad),
                **hist,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "DatabankHistogramArgs",
    "StrategyAsciiChartArgs",
    "_ascii_histogram",
    "_ascii_sparkline",
    "_block_for_fraction",
    "register",
]

# silence unused warning for Path; used by parse_sqx's expected signature in some envs
_ = Path
