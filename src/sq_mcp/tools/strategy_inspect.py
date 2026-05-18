"""Per-strategy inspection tools — comparison, summarization, equity-curve stats.

These tools answer "what does this single .sqx actually look like?" and "how
does it compare to that other one?". They reuse:

- ``parsers.sqx.derive_metrics`` for raw numbers
- ``tools.audit._audit_strategy_metrics`` for risk findings
- ``tools.comparison._pearson`` for curve correlation

Tools:

- ``strategy_compare_two`` — side-by-side metric diff between two .sqx files,
  including a Pearson on their equity curves so the agent can tell if two
  high-fitness strategies are actually trading the same edge.
- ``strategy_summarize`` — Markdown-friendly one-shot summary of a strategy:
  headline numbers, audit traffic-light, equity-curve geometry, a recommendation.
- ``strategy_equity_curve_stats`` — pure numeric stats on the MEC sparkline
  (max drawdown, longest underwater run, recovery factor, hit ratio).
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp._validation import ValidationError, resolve_safe_path
from sq_mcp.parsers.sqx import derive_metrics, parse_sqx
from sq_mcp.tools._common import safe_error_payload
from sq_mcp.tools.audit import _audit_strategy_metrics
from sq_mcp.tools.comparison import _curve_to_returns, _pearson
from sq_mcp.tools.pipeline import _finding_to_dict, _verdict_from_findings

# ---- argument schemas ------------------------------------------------------


class StrategyCompareTwoArgs(BaseModel):
    sqx_a: str = Field(..., description="Path to .sqx file A.")
    sqx_b: str = Field(..., description="Path to .sqx file B.")
    curve: str = Field("full", description="Which embedded curve to correlate: full | is | oos.")


class StrategySummarizeArgs(BaseModel):
    sqx_path: str
    block_on: list[str] = Field(default_factory=lambda: ["critical", "high"])


class StrategyEquityStatsArgs(BaseModel):
    sqx_path: str
    curve: str = Field("full")


class StrategyQualityScoreArgs(BaseModel):
    sqx_path: str


class StrategyNamingArgs(BaseModel):
    sqx_path: str
    prefix: str | None = Field(
        None,
        max_length=32,
        description="Optional prefix (e.g. account name).",
    )


class StrategyRecommendationArgs(BaseModel):
    sqx_path: str


# ---- helpers ---------------------------------------------------------------


def _equity_drawdown_stats(curve: list[float]) -> dict[str, Any]:
    """Compute drawdown geometry from an equity curve (cumulative values).

    Returns max drawdown (absolute + percentage of peak), longest underwater
    run (samples), recovery factor (final equity / max DD), and whether the
    curve ends at a new high. Empty/short curves return an all-None dict.
    """
    if len(curve) < 2:
        return {
            "samples": len(curve),
            "max_drawdown_abs": None,
            "max_drawdown_pct_of_peak": None,
            "longest_underwater": None,
            "recovery_factor": None,
            "ends_at_new_high": None,
        }

    peak = curve[0]
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    longest_uw = 0
    current_uw = 0

    for v in curve:
        if v >= peak:
            # At-or-above peak is "not underwater"; new max resets the run.
            peak = v
            current_uw = 0
        else:
            dd_abs = peak - v
            if dd_abs > max_dd_abs:
                max_dd_abs = dd_abs
            if peak > 0:
                dd_pct = dd_abs / peak
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
            current_uw += 1
            if current_uw > longest_uw:
                longest_uw = current_uw

    final = curve[-1]
    overall_high = max(curve)
    rec_factor: float | None = None
    if max_dd_abs > 0 and math.isfinite(final):
        rec_factor = final / max_dd_abs

    return {
        "samples": len(curve),
        "max_drawdown_abs": round(max_dd_abs, 6),
        "max_drawdown_pct_of_peak": round(max_dd_pct, 6),
        "longest_underwater": longest_uw,
        "recovery_factor": round(rec_factor, 4) if rec_factor is not None else None,
        "ends_at_new_high": math.isclose(final, overall_high, rel_tol=1e-9),
        "start": curve[0],
        "end": curve[-1],
        "high": overall_high,
        "low": min(curve),
    }


def _hit_ratio(curve: list[float]) -> float | None:
    """Fraction of positive-return steps. None if curve too short."""
    rets = _curve_to_returns(curve)
    if not rets:
        return None
    pos = sum(1 for r in rets if r > 0)
    return round(pos / len(rets), 4)


def _select_curve(info: Any, which: str) -> list[float]:
    meta = info.meta
    if meta is None:
        return []
    if which == "is":
        return list(meta.equity_curve_is)
    if which == "oos":
        return list(meta.equity_curve_oos)
    return list(meta.equity_curve_full)


def _side_by_side(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Diff two derived-metrics dicts on a curated metric list."""
    metrics = (
        "trades",
        "net_profit",
        "drawdown_abs",
        "drawdown_pct",
        "fitness_is",
        "fitness_oos",
        "fitness_full",
        "oos_is_ratio",
        "profit_to_dd_ratio",
        "return_pct",
        "avg_trade",
        "trades_per_year",
        "history_years",
        "symbol",
        "timeframe",
    )
    out: dict[str, dict[str, Any]] = {}
    for m in metrics:
        va = a.get(m)
        vb = b.get(m)
        out[m] = {"a": va, "b": vb}
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            out[m]["delta"] = round(vb - va, 6)
            if va != 0:
                out[m]["delta_pct"] = round((vb - va) / abs(va), 4)
    return out


def _summarize_text(metrics: dict[str, Any], verdict: dict[str, Any]) -> list[str]:
    """Produce a short human-readable bullet list summarizing a strategy."""
    lines: list[str] = []
    n = metrics.get("strategy_name") or "(unnamed)"
    sym = metrics.get("symbol") or "?"
    tf = metrics.get("timeframe") or "?"
    lines.append(f"Strategy: {n} on {sym}/{tf}")
    if metrics.get("trades") is not None:
        lines.append(
            f"{metrics['trades']} trades over {metrics.get('history_years') or '?'}y "
            f"({metrics.get('trades_per_year') or '?'}/y)"
        )
    if metrics.get("net_profit") is not None:
        lines.append(
            f"Net profit {metrics['net_profit']} "
            f"({metrics.get('return_pct') or '?'}% of capital), "
            f"DD {metrics.get('drawdown_pct') or '?'}%, "
            f"profit/DD {metrics.get('profit_to_dd_ratio') or '?'}"
        )
    if metrics.get("fitness_oos") is not None or metrics.get("fitness_is") is not None:
        lines.append(
            f"Fitness IS={metrics.get('fitness_is')} OOS={metrics.get('fitness_oos')} "
            f"(ratio {metrics.get('oos_is_ratio')})"
        )
    lines.append(
        f"Verdict: {verdict['traffic_light'].upper()} "
        f"({verdict['worst_severity']} severity, "
        f"{len(verdict['finding_codes'])} finding(s))"
    )
    return lines


def _recommend_action(
    metrics: dict[str, Any], findings: list[Any]
) -> dict[str, Any]:
    """Pick a single action: SHIP / RETEST / TWEAK / DROP. Bias is conservative."""
    reasons: list[str] = []
    has_critical = any(
        (f.severity if hasattr(f, "severity") else f.get("severity")) == "critical"
        for f in findings
    )
    has_high = any(
        (f.severity if hasattr(f, "severity") else f.get("severity")) == "high"
        for f in findings
    )
    if has_critical:
        reasons.append("at least one critical audit finding")
    if has_high:
        reasons.append("at least one high-severity audit finding")

    fitness_oos = metrics.get("fitness_oos")
    dd_pct = metrics.get("drawdown_pct")
    trades = metrics.get("trades")
    oos_is = metrics.get("oos_is_ratio")

    if fitness_oos is None or fitness_oos <= 0:
        reasons.append("no OOS fitness data")
    elif fitness_oos < 0.1:
        reasons.append(f"OOS fitness {fitness_oos} is too low")
    if dd_pct is not None and dd_pct > 50:
        reasons.append(f"drawdown {dd_pct}% > 50% of capital")
    if trades is not None and trades < 30:
        reasons.append(f"only {trades} trades — too thin")
    if oos_is is not None and oos_is < 0.3:
        reasons.append(f"OOS/IS ratio {oos_is} < 0.3 — severe overfit")

    # Decision tree
    action: str
    if has_critical or (fitness_oos is not None and fitness_oos <= 0):
        action = "DROP"
    elif has_high or (oos_is is not None and oos_is < 0.4):
        action = "TWEAK"  # Rebuild with tighter constraints
    elif (
        trades is not None
        and trades < 100
        or (dd_pct is not None and dd_pct > 25)
    ):
        action = "RETEST"  # Validate with more data / scrutiny
    elif fitness_oos is not None and fitness_oos > 0.3 and (oos_is or 0) >= 0.5:
        action = "SHIP"
    else:
        action = "RETEST"

    next_calls: dict[str, list[str]] = {
        "DROP": [
            "sqx_safe_delete sqx_path=...",
            "databank_filter to drop and rebuild databank",
        ],
        "TWEAK": [
            "cfx_lint project=... (see recommendations)",
            "cfx_set_genetic_options (reduce overfit cycles)",
            "cfx_configure_robustness (add WhatIf checks)",
        ],
        "RETEST": [
            "Retest with stricter robustness or more data.",
            "cfx_set_data_range to extend the window.",
        ],
        "SHIP": [
            "strategy_ready_for_deploy (final check)",
            "mt5_assign_magic_numbers + pipeline_export_to_mt5 deploy=True",
        ],
    }
    return {
        "action": action,
        "reasons": reasons,
        "suggested_next_calls": next_calls[action],
    }


def _suggest_strategy_name(
    metrics: dict[str, Any], *, prefix: str | None = None
) -> str:
    """Suggest a human-readable strategy name from derive_metrics.

    Format: ``[prefix-]<symbol>_<tf>_oos<NN>_dd<NN>_t<NNN>``.
    Falls back to whatever metric info is present.
    """
    parts: list[str] = []
    if prefix:
        parts.append(prefix)
    sym = metrics.get("symbol") or "SYM"
    tf = metrics.get("timeframe") or "TF"
    parts.append(f"{sym}_{tf}")
    oos = metrics.get("fitness_oos")
    if oos is not None:
        parts.append(f"oos{int(round(oos * 100))}")
    dd_pct = metrics.get("drawdown_pct")
    if dd_pct is not None:
        parts.append(f"dd{int(round(dd_pct))}")
    trades = metrics.get("trades")
    if trades is not None:
        parts.append(f"t{int(trades)}")
    # Trade-hash short-suffix for uniqueness
    th = metrics.get("trades_hash")
    if th:
        parts.append(str(th)[:6])
    # Sanitize: ASCII safe filename
    name = "_".join(parts)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    return safe


def _drawdown_periods(curve: list[float]) -> list[dict[str, Any]]:
    """Identify discrete drawdown periods in an equity curve.

    A period starts when the curve dips below a fresh peak and ends when it
    fully recovers (reaches the prior peak again) or when the curve runs out
    (open drawdown). Returns each period's start_idx, end_idx (None if open),
    peak value, trough value, depth_abs, depth_pct, and duration in samples.
    """
    if len(curve) < 2:
        return []
    periods: list[dict[str, Any]] = []
    peak = curve[0]
    peak_idx = 0
    in_dd = False
    dd_start_idx = 0
    trough = curve[0]
    trough_idx = 0

    for i, v in enumerate(curve):
        if v >= peak:
            # New peak — close any open drawdown that recovered to >= peak
            if in_dd:
                periods.append(
                    {
                        "start_idx": dd_start_idx,
                        "end_idx": i,
                        "peak_value": peak,
                        "trough_value": trough,
                        "trough_idx": trough_idx,
                        "depth_abs": round(peak - trough, 6),
                        "depth_pct_of_peak": (
                            round((peak - trough) / peak, 6) if peak > 0 else None
                        ),
                        "duration_samples": i - dd_start_idx,
                    }
                )
                in_dd = False
            peak = v
            peak_idx = i  # noqa: F841  (kept for clarity)
        else:
            # Below peak — drawdown
            if not in_dd:
                in_dd = True
                dd_start_idx = i
                trough = v
                trough_idx = i
            elif v < trough:
                trough = v
                trough_idx = i

    if in_dd:
        # Open drawdown at end of curve
        periods.append(
            {
                "start_idx": dd_start_idx,
                "end_idx": None,  # never recovered
                "peak_value": peak,
                "trough_value": trough,
                "trough_idx": trough_idx,
                "depth_abs": round(peak - trough, 6),
                "depth_pct_of_peak": (
                    round((peak - trough) / peak, 6) if peak > 0 else None
                ),
                "duration_samples": len(curve) - 1 - dd_start_idx,
            }
        )

    return periods


def _quality_score(metrics: dict[str, Any], findings: list[Any]) -> dict[str, Any]:
    """0-100 composite quality score with sub-component breakdown.

    Components (each contributes a fraction of the final 100):

    - Fitness (30 pts): OOS fitness × 30, capped at 30.
    - Robustness (25 pts): OOS/IS ratio mapped linearly; 0.5+ → full credit.
    - Capital protection (20 pts): max(0, 20 × (1 - drawdown_pct/50)).
    - Sample size (15 pts): trades / 200 × 15, capped at 15.
    - History coverage (10 pts): history_years / 3 × 10, capped at 10.
    - Penalty: subtract 10 per critical finding, 5 per high.

    Final score is clipped to [0, 100].
    """
    fitness_oos = metrics.get("fitness_oos")
    oos_is = metrics.get("oos_is_ratio")
    dd_pct = metrics.get("drawdown_pct")
    trades = metrics.get("trades")
    history_years = metrics.get("history_years")

    # Missing metric → no credit (more conservative than treating as 0).
    pts_fitness = (
        min(30.0, max(0.0, float(fitness_oos)) * 30.0)
        if fitness_oos is not None else 0.0
    )
    pts_robust = (
        min(25.0, max(0.0, float(oos_is) / 0.5) * 25.0)
        if oos_is is not None else 0.0
    )
    pts_cap = (
        max(0.0, 20.0 * (1.0 - min(1.0, float(dd_pct) / 50.0)))
        if dd_pct is not None else 0.0
    )
    pts_sample = (
        min(15.0, float(trades) / 200.0 * 15.0)
        if trades is not None else 0.0
    )
    pts_history = (
        min(10.0, float(history_years) / 3.0 * 10.0)
        if history_years is not None else 0.0
    )

    critical_penalty = sum(
        10 for f in findings
        if (f.severity if hasattr(f, "severity") else f.get("severity")) == "critical"
    )
    high_penalty = sum(
        5 for f in findings
        if (f.severity if hasattr(f, "severity") else f.get("severity")) == "high"
    )
    raw = pts_fitness + pts_robust + pts_cap + pts_sample + pts_history
    final = max(0.0, min(100.0, raw - critical_penalty - high_penalty))
    return {
        "score": round(final, 1),
        "components": {
            "fitness": round(pts_fitness, 2),
            "robustness": round(pts_robust, 2),
            "capital_protection": round(pts_cap, 2),
            "sample_size": round(pts_sample, 2),
            "history_coverage": round(pts_history, 2),
        },
        "penalties": {
            "critical": critical_penalty,
            "high": high_penalty,
        },
        "tier": (
            "excellent" if final >= 75
            else "good" if final >= 60
            else "marginal" if final >= 40
            else "poor"
        ),
    }


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Side-by-side comparison of two .sqx files: metric-by-metric diff "
            "(delta + delta_pct where numeric), plus Pearson correlation on "
            "their equity-curve returns. Use this to decide if two top-ranked "
            "strategies are diverse or trading the same edge. Read-only."
        )
    )
    async def strategy_compare_two(
        args: StrategyCompareTwoArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            pa = resolve_safe_path(args.sqx_a, must_exist=True)
            pb = resolve_safe_path(args.sqx_b, must_exist=True)
            if pa.suffix.lower() != ".sqx" or pb.suffix.lower() != ".sqx":
                return {"ok": False, "error": "both paths must point to .sqx files"}
            info_a = parse_sqx(pa)
            info_b = parse_sqx(pb)
            m_a = derive_metrics(info_a)
            m_b = derive_metrics(info_b)
            side = _side_by_side(m_a, m_b)
            curve_a = _select_curve(info_a, args.curve)
            curve_b = _select_curve(info_b, args.curve)
            corr = _pearson(_curve_to_returns(curve_a), _curve_to_returns(curve_b))
            return {
                "ok": True,
                "a": {"path": str(pa), "strategy_name": m_a.get("strategy_name")},
                "b": {"path": str(pb), "strategy_name": m_b.get("strategy_name")},
                "curve_correlation": (
                    round(corr, 4) if corr is not None else None
                ),
                "curve_used": args.curve,
                "metrics_side_by_side": side,
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "One-shot human-readable summary of a strategy .sqx: headline metrics, "
            "audit verdict (green/yellow/red), equity-curve geometry (max DD, "
            "longest underwater run, hit ratio, recovery factor), and a bullet "
            "list. Use as a quick triage tool before deeper analysis. Read-only."
        )
    )
    async def strategy_summarize(
        args: StrategySummarizeArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            info = parse_sqx(p)
            m = derive_metrics(info)
            findings = _audit_strategy_metrics(m)
            verdict = _verdict_from_findings(findings, list(args.block_on))
            curve = _select_curve(info, "full")
            stats = _equity_drawdown_stats(curve)
            stats["hit_ratio"] = _hit_ratio(curve)
            lines = _summarize_text(m, verdict)
            return {
                "ok": True,
                "sqx_path": str(p),
                "metrics": m,
                "verdict": verdict,
                "equity_stats": stats,
                "findings": [_finding_to_dict(f) for f in findings],
                "summary_lines": lines,
                "summary_markdown": "- " + "\n- ".join(lines),
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compute drawdown / underwater / hit-ratio / recovery-factor stats on "
            "the embedded equity sparkline of a .sqx (no Python plotting). Read-only."
        )
    )
    async def strategy_equity_curve_stats(
        args: StrategyEquityStatsArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            info = parse_sqx(p)
            curve = _select_curve(info, args.curve)
            stats = _equity_drawdown_stats(curve)
            stats["hit_ratio"] = _hit_ratio(curve)
            return {
                "ok": True,
                "sqx_path": str(p),
                "curve": args.curve,
                "curve_present": bool(curve),
                "stats": stats,
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "List every discrete drawdown period in a strategy's equity curve: "
            "start/end index, peak/trough values, depth (abs + % of peak), "
            "duration in samples. Open drawdowns (curve never recovered) have "
            "end_idx=None. Use to see how *frequently* a strategy goes underwater, "
            "not just its single worst DD. Read-only."
        )
    )
    async def strategy_drawdown_periods(
        args: StrategyEquityStatsArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            info = parse_sqx(p)
            curve = _select_curve(info, args.curve)
            periods = _drawdown_periods(curve)
            open_dd = [p for p in periods if p["end_idx"] is None]
            return {
                "ok": True,
                "sqx_path": str(p),
                "curve": args.curve,
                "curve_samples": len(curve),
                "drawdown_period_count": len(periods),
                "open_drawdown_count": len(open_dd),
                "deepest_period": (
                    max(periods, key=lambda x: x["depth_abs"]) if periods else None
                ),
                "longest_period": (
                    max(periods, key=lambda x: x["duration_samples"]) if periods else None
                ),
                "periods": periods,
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Single 0-100 quality score for a strategy with a sub-component "
            "breakdown (fitness, robustness, capital protection, sample size, "
            "history coverage) and penalties for critical/high audit findings. "
            "Returns a tier label (excellent / good / marginal / poor) so the "
            "agent can rank strategies with one number. Read-only."
        )
    )
    async def strategy_quality_score(
        args: StrategyQualityScoreArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            info = parse_sqx(p)
            m = derive_metrics(info)
            findings = _audit_strategy_metrics(m)
            score = _quality_score(m, findings)
            return {
                "ok": True,
                "sqx_path": str(p),
                "strategy_name": m.get("strategy_name"),
                **score,
                "metrics": {
                    "fitness_oos": m.get("fitness_oos"),
                    "oos_is_ratio": m.get("oos_is_ratio"),
                    "drawdown_pct": m.get("drawdown_pct"),
                    "trades": m.get("trades"),
                    "history_years": m.get("history_years"),
                },
                "finding_count": len(findings),
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Recommend a single action for a strategy — SHIP / RETEST / TWEAK / "
            "DROP — based on audit findings + headline metrics. Returns the "
            "action, reasons, and suggested next tool calls. Use when you need "
            "a one-shot decision per strategy."
        )
    )
    async def strategy_recommendation(
        args: StrategyRecommendationArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            info = parse_sqx(p)
            m = derive_metrics(info)
            findings = _audit_strategy_metrics(m)
            rec = _recommend_action(m, findings)
            return {
                "ok": True,
                "sqx_path": str(p),
                "strategy_name": m.get("strategy_name"),
                **rec,
                "finding_codes": [f.code for f in findings],
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Suggest a human-readable strategy name from a .sqx's metrics. "
            "Format: '[prefix_]<symbol>_<tf>_oos<NN>_dd<NN>_t<NNN>_<hashprefix>'. "
            "Use to rename .sqx files for clarity before shipping to MT5. "
            "Read-only — returns the suggested name, doesn't rename anything."
        )
    )
    async def strategy_naming_suggestion(
        args: StrategyNamingArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            info = parse_sqx(p)
            m = derive_metrics(info)
            name = _suggest_strategy_name(m, prefix=args.prefix)
            return {
                "ok": True,
                "sqx_path": str(p),
                "current_name": m.get("strategy_name"),
                "suggested_name": name,
                "suggested_filename": f"{name}.sqx",
            }
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "StrategyCompareTwoArgs",
    "StrategyEquityStatsArgs",
    "StrategyNamingArgs",
    "StrategyQualityScoreArgs",
    "StrategyRecommendationArgs",
    "StrategySummarizeArgs",
    "_drawdown_periods",
    "_equity_drawdown_stats",
    "_hit_ratio",
    "_quality_score",
    "_recommend_action",
    "_select_curve",
    "_side_by_side",
    "_suggest_strategy_name",
    "_summarize_text",
    "register",
]
