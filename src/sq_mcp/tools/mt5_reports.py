"""Parse MetaTrader 5 Strategy Tester HTML reports.

When you backtest an EA in MT5, the Strategy Tester emits a Report.htm with a
fixed table layout containing per-test metrics. This module parses those
HTML files so the agent can compare an MT5 backtest result against the .sqx
that SQ produced for the same strategy.

Tools:

- ``mt5_parse_backtest_report`` — extract metrics (net profit, profit
  factor, drawdown, total trades, Sharpe, recovery factor) from a Report.htm.
- ``mt5_compare_with_sqx`` — pair an MT5 report with the .sqx the EA was
  generated from, report relative diff on each metric. Flags >10% disagreement
  as 'WARN' — useful for catching simulation-vs-platform discrepancies.

Both read-only. No engine call, no Wine.
"""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp._validation import ValidationError, resolve_safe_path
from sq_mcp.parsers.sqx import derive_metrics, parse_sqx
from sq_mcp.tools._common import safe_error_payload

# Keys SQ X uses → corresponding MT5 row labels. MT5 labels are case-sensitive
# and language-dependent (English-default below).
_MT5_LABEL_MAP = {
    "total_net_profit": ("Total Net Profit", "Net Profit"),
    "gross_profit": ("Gross Profit",),
    "gross_loss": ("Gross Loss",),
    "profit_factor": ("Profit Factor",),
    "expected_payoff": ("Expected Payoff",),
    "recovery_factor": ("Recovery Factor",),
    "sharpe_ratio": ("Sharpe Ratio",),
    "total_trades": ("Total Trades", "Total Deals"),
    "balance_drawdown_abs": ("Balance Drawdown Absolute",),
    "balance_drawdown_pct": ("Balance Drawdown Relative", "Balance Drawdown Maximal"),
    "winning_trades_pct": ("Profit Trades (% of total)", "Profit trades (% of total)"),
}


class Mt5ParseReportArgs(BaseModel):
    report_path: str = Field(..., description="Path to MT5 Report.htm.")


class Mt5CompareWithSqxArgs(BaseModel):
    report_path: str
    sqx_path: str
    warn_threshold: float = Field(
        0.10,
        ge=0.01,
        le=1.0,
        description="Relative diff threshold for WARN (default 10%).",
    )


def _strip_html(html: str) -> str:
    """Cheap HTML → text: drop tags, collapse whitespace."""
    no_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", no_tags).strip()


def _extract_metric_value(text: str, label: str) -> str | None:
    """Look for 'Label: value' or 'Label\\s+value' patterns in flattened report text."""
    # MT5 layouts: "Profit Factor: 1.45" OR "Profit Factor 1.45" (col-separated)
    # We accept both; use \\s+ as separator.
    pattern = re.escape(label) + r"\s*:?\s*([A-Za-z0-9\.\-\(\)\s%\/,]+?)(?=\s{2}|\s[A-Z][a-z]|$)"
    m = re.search(pattern, text)
    if m:
        return m.group(1).strip()
    return None


def _coerce_metric(value: str | None) -> float | None:
    """Best-effort numeric coercion. Strips currency symbols, percentage signs."""
    if value is None:
        return None
    # Pull out the first numeric token (handles "1234.56 (4.50%)" → 1234.56)
    m = re.search(r"-?[\d]+(?:[.,]\d+)?", value.replace(" ", ""))
    if not m:
        return None
    raw = m.group(0)
    # Some locales use comma as decimal — try both
    try:
        return float(raw)
    except ValueError:
        try:
            return float(raw.replace(",", "."))
        except ValueError:
            return None


def _parse_mt5_report(html_bytes: bytes) -> dict[str, Any]:
    """Extract metrics from the flattened text of an MT5 Report.htm."""
    text = _strip_html(html_bytes.decode("utf-8", errors="replace"))
    out: dict[str, Any] = {}
    for sq_key, mt5_labels in _MT5_LABEL_MAP.items():
        for label in mt5_labels:
            raw = _extract_metric_value(text, label)
            if raw is not None:
                out[sq_key] = {
                    "raw": raw,
                    "value": _coerce_metric(raw),
                    "label_used": label,
                }
                break
        else:
            out[sq_key] = {"raw": None, "value": None, "label_used": None}
    return out


def _diff_with_sqx(
    mt5: dict[str, Any], sqx_metrics: dict[str, Any], *, warn_threshold: float
) -> dict[str, Any]:
    """Compare MT5 and SQ metrics. Returns per-metric delta + status."""
    rows: list[dict[str, Any]] = []
    # (mt5_key, sqx_key)
    pairs = (
        ("total_net_profit", "net_profit"),
        ("profit_factor", "profit_to_dd_ratio"),  # rough analog
        ("total_trades", "trades"),
        ("balance_drawdown_pct", "drawdown_pct"),
        ("balance_drawdown_abs", "drawdown_abs"),
    )
    warn_count = 0
    for mk, sk in pairs:
        mt5_val = (mt5.get(mk) or {}).get("value")
        sqx_val = sqx_metrics.get(sk)
        status = "match"
        rel_diff: float | None = None
        if mt5_val is None or sqx_val is None:
            status = "missing"
        elif sqx_val == 0:
            rel_diff = None
            status = "match" if mt5_val == 0 else "warn"
        else:
            rel_diff = abs(mt5_val - sqx_val) / abs(sqx_val)
            if rel_diff > warn_threshold:
                status = "warn"
                warn_count += 1
        rows.append(
            {
                "mt5_key": mk,
                "sqx_key": sk,
                "mt5_value": mt5_val,
                "sqx_value": sqx_val,
                "relative_diff": (
                    round(rel_diff, 4) if rel_diff is not None else None
                ),
                "status": status,
            }
        )
    return {
        "warn_threshold": warn_threshold,
        "warn_count": warn_count,
        "diffs": rows,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Parse an MT5 Strategy Tester Report.htm and extract metrics: net "
            "profit, profit factor, drawdown, total trades, Sharpe, recovery "
            "factor. Pure HTML scrape — handles English-default reports. Read-only."
        )
    )
    async def mt5_parse_backtest_report(
        args: Mt5ParseReportArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.report_path, must_exist=True)
            if p.suffix.lower() not in (".html", ".htm"):
                return {"ok": False, "error": "expected .html or .htm"}
            payload = p.read_bytes()
            metrics = _parse_mt5_report(payload)
            found = sum(1 for v in metrics.values() if (v or {}).get("value") is not None)
            return {
                "ok": True,
                "report_path": str(p),
                "size_bytes": len(payload),
                "metrics_found": found,
                "metrics_total": len(_MT5_LABEL_MAP),
                "metrics": metrics,
            }
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compare an MT5 backtest report against its source .sqx. Pairs "
            "MT5 metrics with SQ X-derived metrics, computes relative diff, "
            "and flags any pair whose |diff| > warn_threshold as 'warn'. Use "
            "to catch SQ/MT5 simulation disagreements before going live."
        )
    )
    async def mt5_compare_with_sqx(
        args: Mt5CompareWithSqxArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            report_p = resolve_safe_path(args.report_path, must_exist=True)
            sqx_p = resolve_safe_path(args.sqx_path, must_exist=True)
            if sqx_p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected .sqx"}
            mt5 = _parse_mt5_report(report_p.read_bytes())
            info = parse_sqx(sqx_p)
            sqx_metrics = derive_metrics(info)
            diff = _diff_with_sqx(
                mt5, sqx_metrics, warn_threshold=args.warn_threshold
            )
            return {
                "ok": True,
                "report_path": str(report_p),
                "sqx_path": str(sqx_p),
                "sqx_strategy_name": sqx_metrics.get("strategy_name"),
                "mt5_metrics": mt5,
                "diff": diff,
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "Mt5CompareWithSqxArgs",
    "Mt5ParseReportArgs",
    "_coerce_metric",
    "_diff_with_sqx",
    "_extract_metric_value",
    "_parse_mt5_report",
    "_strip_html",
    "register",
]
