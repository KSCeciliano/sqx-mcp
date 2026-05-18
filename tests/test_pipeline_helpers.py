"""Unit tests for pipeline (strategy_export_pipeline) helpers."""

from __future__ import annotations

from sq_mcp.tools.audit import Finding
from sq_mcp.tools.pipeline import _finding_to_dict, _verdict_from_findings


def _f(code: str, severity: str) -> Finding:
    return Finding(code=code, severity=severity, title=code, message="x", suggestion="y")


# ---- _verdict_from_findings -----------------------------------------------


def test_verdict_green_when_no_findings() -> None:
    v = _verdict_from_findings([], block_on=["critical", "high"])
    assert v["traffic_light"] == "green"
    assert v["block_deploy"] is False
    assert v["worst_severity"] == "none"
    assert v["finding_codes"] == []


def test_verdict_yellow_when_findings_below_block_threshold() -> None:
    findings = [_f("X", "medium"), _f("Y", "low")]
    v = _verdict_from_findings(findings, block_on=["critical", "high"])
    assert v["traffic_light"] == "yellow"
    assert v["block_deploy"] is False
    assert v["worst_severity"] == "medium"
    assert v["severity_counts"] == {"medium": 1, "low": 1}


def test_verdict_red_when_blocking_severity_present() -> None:
    findings = [_f("Z", "high"), _f("Q", "low")]
    v = _verdict_from_findings(findings, block_on=["critical", "high"])
    assert v["traffic_light"] == "red"
    assert v["block_deploy"] is True


def test_verdict_treats_custom_block_list() -> None:
    findings = [_f("info_only", "info")]
    # If the agent says "even info findings should block", honor it
    v = _verdict_from_findings(findings, block_on=["info"])
    assert v["traffic_light"] == "red"
    assert v["block_deploy"] is True


def test_verdict_worst_severity_picks_highest_rank() -> None:
    findings = [_f("a", "low"), _f("b", "critical"), _f("c", "medium")]
    v = _verdict_from_findings(findings, block_on=["critical"])
    assert v["worst_severity"] == "critical"


def test_verdict_accepts_dict_inputs() -> None:
    # The verdict helper also tolerates plain dicts, so tests / future callers
    # don't all have to construct Finding instances
    findings = [
        {"code": "X", "severity": "high", "title": "t", "message": "m", "suggestion": "s"},
    ]
    v = _verdict_from_findings(findings, block_on=["high"])
    assert v["traffic_light"] == "red"
    assert v["finding_codes"] == ["X"]


# ---- _finding_to_dict -----------------------------------------------------


def test_finding_to_dict_from_dataclass() -> None:
    d = _finding_to_dict(_f("X", "high"))
    assert d["code"] == "X"
    assert d["severity"] == "high"


def test_finding_to_dict_passthrough_dict() -> None:
    src = {"code": "X", "severity": "info", "title": "t", "message": "m", "suggestion": "s"}
    out = _finding_to_dict(src)
    assert out == src
    assert out is not src  # shallow copy, not the same object
