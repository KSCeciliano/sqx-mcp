"""Unit tests for mt5_set_files helpers."""

from __future__ import annotations

from sq_mcp.tools.mt5_set_files import (
    PROFILE_MAP,
    _apply_profile,
    _diff,
    _merge,
    _parse_set_text,
    _render_set,
)

SAMPLE_SET = """\
; Comment line
LotSize=0.1
MaxRiskPercent=2.0
MagicNumber=12345
[Trading]
StopLoss=50
TakeProfit=100
"""


def test_parse_set_basic() -> None:
    parsed = _parse_set_text(SAMPLE_SET)
    assert "default" in parsed["sections"]
    assert parsed["sections"]["default"]["LotSize"] == "0.1"
    assert parsed["sections"]["default"]["MagicNumber"] == "12345"
    assert parsed["sections"]["Trading"]["StopLoss"] == "50"


def test_parse_set_handles_comments() -> None:
    parsed = _parse_set_text("; only comments\n; another\n")
    assert parsed["n_keys"] == 0


def test_render_round_trip() -> None:
    parsed = _parse_set_text(SAMPLE_SET)["sections"]
    rendered = _render_set(parsed)
    reparsed = _parse_set_text(rendered)["sections"]
    assert reparsed == parsed


def test_diff_identifies_changes() -> None:
    a = {"default": {"LotSize": "0.1", "MaxRisk": "2.0"}}
    b = {"default": {"LotSize": "0.2", "MaxRisk": "2.0", "NewKey": "x"}}
    result = _diff(a, b)
    assert result["n_changed"] == 1
    assert result["n_added"] == 1
    assert result["changed"]["default"]["LotSize"]["from"] == "0.1"
    assert result["changed"]["default"]["LotSize"]["to"] == "0.2"


def test_diff_identifies_removals() -> None:
    a = {"default": {"X": "1", "Y": "2"}}
    b = {"default": {"X": "1"}}
    result = _diff(a, b)
    assert result["n_removed"] == 1


def test_merge_overlay_wins() -> None:
    base = {"default": {"LotSize": "0.1", "Old": "y"}}
    overlay = {"default": {"LotSize": "0.5", "New": "z"}}
    merged = _merge(base, overlay, overlay_wins=True)
    assert merged["default"]["LotSize"] == "0.5"
    assert merged["default"]["New"] == "z"
    assert merged["default"]["Old"] == "y"


def test_merge_base_wins() -> None:
    base = {"default": {"LotSize": "0.1"}}
    overlay = {"default": {"LotSize": "0.5"}}
    merged = _merge(base, overlay, overlay_wins=False)
    assert merged["default"]["LotSize"] == "0.1"


def test_apply_profile_overrides_target_section() -> None:
    sections = _parse_set_text(SAMPLE_SET)["sections"]
    result = _apply_profile(sections, "conservative", magic_number=999)
    assert result["ok"] is True
    assert result["applied_overrides"]["LotSize"] == PROFILE_MAP["conservative"]["LotSize"]
    assert result["applied_overrides"]["MagicNumber"] == "999"
    assert "LotSize=" in result["rendered"]


def test_apply_profile_unknown() -> None:
    sections = _parse_set_text(SAMPLE_SET)["sections"]
    result = _apply_profile(sections, "yolo", magic_number=0)
    assert result["ok"] is False
