"""Unit tests for alerts helpers."""

from __future__ import annotations

from sq_mcp.tools.alerts import (
    _compare,
    _evaluate_batch,
    _evaluate_rule,
    _format_summary,
    _get_path,
    _workspace_defaults,
)

# ---- _get_path -------------------------------------------------------------


def test_get_path_flat() -> None:
    payload = {"a": 1, "b": 2}
    v, found = _get_path(payload, "a")
    assert v == 1 and found is True


def test_get_path_dotted() -> None:
    payload = {"outer": {"inner": 42}}
    v, found = _get_path(payload, "outer.inner")
    assert v == 42 and found is True


def test_get_path_missing() -> None:
    v, found = _get_path({}, "ghost")
    assert v is None and found is False


def test_get_path_partial_miss() -> None:
    v, found = _get_path({"a": {"b": 1}}, "a.c")
    assert found is False


# ---- _compare --------------------------------------------------------------


def test_compare_eq() -> None:
    assert _compare("==", "x", "x") is True
    assert _compare("==", 1, 2) is False


def test_compare_gt_numeric() -> None:
    assert _compare(">", 10, 5) is True
    assert _compare(">", 5, 10) is False


def test_compare_in() -> None:
    assert _compare("in", "red", ["red", "yellow"]) is True
    assert _compare("in", "green", ["red"]) is False


def test_compare_not_in() -> None:
    assert _compare("not_in", "green", ["red"]) is True


def test_compare_contains() -> None:
    assert _compare("contains", [1, 2, 3], 2) is True
    assert _compare("contains", "hello", "lo") is True


# ---- _evaluate_rule --------------------------------------------------------


def test_evaluate_rule_triggers_on_threshold() -> None:
    rule = {
        "name": "high_dd",
        "metric_key": "drawdown_pct",
        "op": ">",
        "threshold": 25,
        "severity": "high",
        "message": "high dd",
    }
    out = _evaluate_rule(rule, {"drawdown_pct": 30})
    assert out["triggered"] is True
    assert out["severity"] == "high"


def test_evaluate_rule_does_not_trigger_when_under() -> None:
    rule = {
        "name": "high_dd",
        "metric_key": "drawdown_pct",
        "op": ">",
        "threshold": 25,
        "severity": "high",
    }
    out = _evaluate_rule(rule, {"drawdown_pct": 20})
    assert out["triggered"] is False


def test_evaluate_rule_missing_key_no_trigger() -> None:
    rule = {
        "name": "any",
        "metric_key": "ghost",
        "op": ">",
        "threshold": 5,
        "severity": "low",
    }
    out = _evaluate_rule(rule, {})
    assert out["triggered"] is False
    assert "not found" in out["note"]


def test_evaluate_rule_missing_op_triggers_when_missing() -> None:
    rule = {
        "name": "must_have_x",
        "metric_key": "x",
        "op": "missing",
        "threshold": None,
        "severity": "high",
    }
    out = _evaluate_rule(rule, {"y": 1})
    assert out["triggered"] is True


def test_evaluate_rule_dotted_path() -> None:
    rule = {
        "name": "inner_dd",
        "metric_key": "metrics.dd",
        "op": ">",
        "threshold": 10,
        "severity": "medium",
    }
    out = _evaluate_rule(rule, {"metrics": {"dd": 15}})
    assert out["triggered"] is True


# ---- _evaluate_batch -------------------------------------------------------


def test_evaluate_batch_counts() -> None:
    rules = [
        {"name": "a", "metric_key": "x", "op": ">", "threshold": 5, "severity": "high"},
        {"name": "b", "metric_key": "x", "op": "<", "threshold": 5, "severity": "low"},
    ]
    out = _evaluate_batch(rules, {"x": 10})
    assert out["n_triggered"] == 1
    assert out["severity_counts"]["high"] == 1


def test_evaluate_batch_no_alerts() -> None:
    rules = [{"name": "a", "metric_key": "x", "op": ">", "threshold": 100, "severity": "low"}]
    out = _evaluate_batch(rules, {"x": 10})
    assert out["n_triggered"] == 0
    assert out["alerts"] == []


# ---- _workspace_defaults ---------------------------------------------------


def test_workspace_defaults_count_and_severities() -> None:
    rules = _workspace_defaults()
    assert len(rules) >= 5
    severities = {r["severity"] for r in rules}
    assert "critical" in severities
    assert "high" in severities


def test_workspace_defaults_match_drift_red() -> None:
    rules = _workspace_defaults()
    out = _evaluate_batch(rules, {"drift_verdict": "red"})
    triggered_names = {a["name"] for a in out["alerts"]}
    assert "drift_red" in triggered_names


# ---- _format_summary -------------------------------------------------------


def test_format_summary_empty() -> None:
    out = _format_summary([], "Test")
    assert "No alerts" in out


def test_format_summary_sorts_by_severity() -> None:
    alerts = [
        {"name": "low_a", "severity": "low", "message": "low"},
        {"name": "crit_b", "severity": "critical", "message": "critical"},
        {"name": "med_c", "severity": "medium", "message": "medium"},
    ]
    out = _format_summary(alerts, "Alerts")
    crit_idx = out.index("crit_b")
    med_idx = out.index("med_c")
    low_idx = out.index("low_a")
    assert crit_idx < med_idx < low_idx
