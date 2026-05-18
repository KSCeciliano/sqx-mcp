"""Unit tests for cfx_diff helpers."""

from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from sq_mcp.tools.cfx_diff import (
    _detect_archetype,
    _diff_curated_fields,
    _diff_xml_blobs,
    _flatten_xml,
)


def _make_cfx(tmp_path: Path, members: dict[str, bytes], name: str = "test.cfx") -> Path:
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        for n, content in members.items():
            z.writestr(n, content)
    return p


# ---- _detect_archetype ---------------------------------------------------


def test_detect_archetype_builder(tmp_path: Path) -> None:
    cfx = _make_cfx(tmp_path, {
        "config.xml": b"<?xml version='1.0'?><Config/>",
        "Build-Task1.xml": b"<?xml version='1.0'?><Settings/>",
    })
    out = _detect_archetype(cfx)
    assert out["primary_archetype"] == "builder"
    assert out["by_archetype"] == {"builder": 1}
    assert out["archetype_label"] == "builder"
    assert "Build-Task1.xml" in out["task_files"]


def test_detect_archetype_walkforward_plus_retest(tmp_path: Path) -> None:
    cfx = _make_cfx(tmp_path, {
        "config.xml": b"<Config/>",
        "WalkForward-Task1.xml": b"<Settings/>",
        "Retest-Task1.xml": b"<Settings/>",
    })
    out = _detect_archetype(cfx)
    # Both are present — primary is "max count" but both kinds reported
    assert out["task_count"] == 2
    assert set(out["by_archetype"]) == {"walkforward", "retester"}
    assert out["archetype_label"] in ("retester+walkforward", "walkforward+retester")


def test_detect_archetype_unknown_when_no_tasks(tmp_path: Path) -> None:
    cfx = _make_cfx(tmp_path, {"config.xml": b"<Config/>"})
    out = _detect_archetype(cfx)
    assert out["primary_archetype"] == "unknown"
    assert out["task_count"] == 0


# ---- _flatten_xml + _diff_xml_blobs --------------------------------------


def test_flatten_xml_picks_up_attributes_and_text() -> None:
    root = etree.fromstring(b"<r><a x='1' y='2'>hello</a></r>")
    out = _flatten_xml(root)
    assert "/r/a=hello" in out
    assert "/r/a@x=1" in out
    assert "/r/a@y=2" in out


def test_diff_xml_blobs_reports_only_differences() -> None:
    a = b"<r><x>1</x></r>"
    b = b"<r><x>2</x></r>"
    d = _diff_xml_blobs(a, b)
    assert d["only_in_a_count"] == 1
    assert d["only_in_b_count"] == 1
    assert "/r/x=1" in d["only_in_a_sample"]
    assert "/r/x=2" in d["only_in_b_sample"]


def test_diff_xml_blobs_identical_returns_empty() -> None:
    src = b"<r><x>1</x></r>"
    d = _diff_xml_blobs(src, src)
    assert d["only_in_a_count"] == 0
    assert d["only_in_b_count"] == 0


def test_diff_xml_blobs_handles_invalid_xml() -> None:
    d = _diff_xml_blobs(b"<r><x>", b"<r/>")
    assert "error" in d


# ---- _diff_curated_fields ------------------------------------------------


def test_diff_curated_fields_picks_only_differences() -> None:
    a = {"fitness": "ReturnDDRatio", "mm": "FixedSize", "capital": 10000}
    b = {"fitness": "NetProfit", "mm": "FixedSize", "capital": 5000}
    out = _diff_curated_fields(a, b)
    # 'fitness' and 'capital' differ; 'mm' matches → excluded
    assert "fitness" in out
    assert "capital" in out
    assert "mm" not in out
    assert out["fitness"] == {"a": "ReturnDDRatio", "b": "NetProfit"}


def test_diff_curated_fields_handles_missing_keys() -> None:
    a = {"fitness": "X"}
    b = {"mm": "Y"}
    out = _diff_curated_fields(a, b)
    assert "fitness" in out
    assert "mm" in out
    assert out["fitness"] == {"a": "X", "b": None}
    assert out["mm"] == {"a": None, "b": "Y"}
