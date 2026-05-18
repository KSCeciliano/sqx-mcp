"""Declarative alert rules.

Define alerts as ``{metric, op, threshold, severity}`` records that the
agent can evaluate against arbitrary metric payloads (a project status,
an audit result, a drift verdict, …). When a rule fires, return a
structured Alert with enough context to format a notification.

Tools:

- ``alert_rule_evaluate`` — evaluate a single rule against a metric
  payload.
- ``alert_rules_batch`` — evaluate a list of rules against a metric
  payload; return the set of triggered alerts.
- ``alert_define_workspace_defaults`` — return a curated default rule
  set for a typical workspace (drawdown > 25%, drift=red, OOS_IS < 0.3,
  stalled build, etc).
- ``alert_format_summary`` — given a list of triggered alerts, format
  a Markdown summary the user can paste into Slack / Discord / email.

Rules don't trigger side effects — they just report. The agent decides
what to do with the alerts (notify, pause, log).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

VALID_OPS = ("==", "!=", ">", ">=", "<", "<=", "in", "not_in", "contains", "missing")
VALID_SEVERITIES = ("info", "low", "medium", "high", "critical")


# ---- argument schemas ------------------------------------------------------


class AlertRule(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    metric_key: str = Field(..., min_length=1, max_length=128)
    op: str
    threshold: Any = None
    severity: str = "medium"
    message: str = Field("", max_length=512)

    @field_validator("op")
    @classmethod
    def _v_op(cls, v: str) -> str:
        if v not in VALID_OPS:
            raise ValueError(f"op must be one of {VALID_OPS}")
        return v

    @field_validator("severity")
    @classmethod
    def _v_sev(cls, v: str) -> str:
        if v not in VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {VALID_SEVERITIES}")
        return v


class AlertEvaluateArgs(BaseModel):
    rule: AlertRule
    payload: dict[str, Any]


class AlertBatchArgs(BaseModel):
    rules: list[AlertRule] = Field(..., min_length=1, max_length=200)
    payload: dict[str, Any]


class AlertSummaryArgs(BaseModel):
    alerts: list[dict[str, Any]] = Field(..., min_length=0, max_length=500)
    title: str = "Alerts"


# ---- helpers ---------------------------------------------------------------


def _get_path(payload: dict[str, Any], key: str) -> tuple[Any, bool]:
    """Resolve a dotted path within payload. Returns (value, found)."""
    cur: Any = payload
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None, False
    return cur, True


def _compare(op: str, value: Any, threshold: Any) -> bool:
    if op == "==":
        return value == threshold
    if op == "!=":
        return value != threshold
    if op == ">":
        return _num(value) > _num(threshold)
    if op == ">=":
        return _num(value) >= _num(threshold)
    if op == "<":
        return _num(value) < _num(threshold)
    if op == "<=":
        return _num(value) <= _num(threshold)
    if op == "in":
        return isinstance(threshold, (list, tuple, set)) and value in threshold
    if op == "not_in":
        return not (isinstance(threshold, (list, tuple, set)) and value in threshold)
    if op == "contains":
        return isinstance(value, (str, list, tuple, set)) and threshold in value
    if op == "missing":
        # Special — handled at the path-resolution level
        return False
    return False


def _num(x: Any) -> float:
    """Coerce x to float for numeric comparisons. NaN on failure."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def _evaluate_rule(rule: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    value, found = _get_path(payload, rule["metric_key"])
    if rule["op"] == "missing":
        triggered = not found
        return {
            "name": rule["name"],
            "triggered": triggered,
            "severity": rule["severity"],
            "value": None,
            "metric_key": rule["metric_key"],
            "message": (
                rule.get("message")
                or f"{rule['metric_key']} is missing from payload"
            ) if triggered else None,
        }
    if not found:
        return {
            "name": rule["name"],
            "triggered": False,
            "severity": rule["severity"],
            "value": None,
            "metric_key": rule["metric_key"],
            "note": "metric_key not found in payload",
        }
    try:
        is_hit = _compare(rule["op"], value, rule["threshold"])
    except (TypeError, ValueError):
        return {
            "name": rule["name"],
            "triggered": False,
            "severity": rule["severity"],
            "value": value,
            "metric_key": rule["metric_key"],
            "note": "comparison failed (type mismatch)",
        }
    msg = rule.get("message") or (
        f"{rule['metric_key']} {rule['op']} {rule['threshold']} "
        f"(actual={value})"
    )
    return {
        "name": rule["name"],
        "triggered": is_hit,
        "severity": rule["severity"],
        "value": value,
        "metric_key": rule["metric_key"],
        "message": msg if is_hit else None,
    }


def _evaluate_batch(
    rules: list[dict[str, Any]], payload: dict[str, Any]
) -> dict[str, Any]:
    results = [_evaluate_rule(r, payload) for r in rules]
    triggered = [r for r in results if r.get("triggered")]
    severity_counts: dict[str, int] = {}
    for r in triggered:
        sev = r.get("severity", "medium")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    return {
        "n_rules_evaluated": len(rules),
        "n_triggered": len(triggered),
        "severity_counts": severity_counts,
        "alerts": triggered,
        "results": results,
    }


def _workspace_defaults() -> list[dict[str, Any]]:
    """Curated default rules for a typical algo workspace."""
    return [
        {
            "name": "extreme_drawdown",
            "metric_key": "drawdown_pct",
            "op": ">",
            "threshold": 25,
            "severity": "high",
            "message": "drawdown above 25% — review strategy or pause",
        },
        {
            "name": "drift_red",
            "metric_key": "drift_verdict",
            "op": "==",
            "threshold": "red",
            "severity": "critical",
            "message": "live drift detector returned red — pause or shrink",
        },
        {
            "name": "drift_yellow",
            "metric_key": "drift_verdict",
            "op": "==",
            "threshold": "yellow",
            "severity": "medium",
            "message": "live drift detector returned yellow — investigate",
        },
        {
            "name": "weak_oos",
            "metric_key": "oos_is_ratio",
            "op": "<",
            "threshold": 0.3,
            "severity": "high",
            "message": "OOS/IS ratio below 0.3 — strategy is overfit",
        },
        {
            "name": "low_trades",
            "metric_key": "trades",
            "op": "<",
            "threshold": 100,
            "severity": "medium",
            "message": "fewer than 100 trades — statistics are unreliable",
        },
        {
            "name": "stalled_build",
            "metric_key": "status",
            "op": "==",
            "threshold": "stalled",
            "severity": "high",
            "message": "build appears stalled — consider stopping",
        },
        {
            "name": "very_brittle",
            "metric_key": "brittle_verdict",
            "op": "==",
            "threshold": "very_brittle",
            "severity": "critical",
            "message": "strategy is very brittle — most P&L from a few trades",
        },
    ]


def _format_summary(alerts: list[dict[str, Any]], title: str) -> str:
    if not alerts:
        return f"### {title}\n\nNo alerts triggered.\n"
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    sorted_alerts = sorted(
        alerts, key=lambda a: sev_order.get(a.get("severity", "medium"), 99)
    )
    lines = [f"### {title}", ""]
    for a in sorted_alerts:
        sev = (a.get("severity") or "medium").upper()
        name = a.get("name", "(unnamed)")
        msg = a.get("message", "")
        lines.append(f"- **[{sev}]** `{name}` — {msg}")
    return "\n".join(lines) + "\n"


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Evaluate a single alert rule against a metric payload. Rule is "
            "(metric_key, op, threshold, severity, message). metric_key supports "
            "dotted paths into nested dicts. Returns triggered True/False + "
            "details."
        )
    )
    async def alert_rule_evaluate(args: AlertEvaluateArgs) -> dict:
        return {
            "ok": True,
            **_evaluate_rule(args.rule.model_dump(), args.payload),
        }

    @mcp.tool(
        description=(
            "Evaluate a list of alert rules against one payload. Returns the "
            "triggered alerts sorted, plus per-severity counts."
        )
    )
    async def alert_rules_batch(args: AlertBatchArgs) -> dict:
        return {
            "ok": True,
            **_evaluate_batch([r.model_dump() for r in args.rules], args.payload),
        }

    @mcp.tool(
        description=(
            "Return a curated default rule set for a typical algo workspace: "
            "extreme drawdown, drift red/yellow, weak OOS, low trades, stalled "
            "build, very-brittle. Use as a starting point; customize the "
            "thresholds for your tolerance."
        )
    )
    async def alert_workspace_defaults() -> dict:
        return {"ok": True, "rules": _workspace_defaults()}

    @mcp.tool(
        description=(
            "Format a list of triggered alerts as a Slack/Discord-friendly "
            "Markdown block, sorted critical → info."
        )
    )
    async def alert_format_summary(args: AlertSummaryArgs) -> dict:
        return {"ok": True, "markdown": _format_summary(args.alerts, args.title)}
