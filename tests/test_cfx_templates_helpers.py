"""Unit tests for cfx_templates helpers."""

from __future__ import annotations

import zipfile
from pathlib import Path

from sq_mcp.tools.cfx_templates import _apply_template_to_cfx, _capture_task_xmls


def _make_cfx(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, content in members.items():
            z.writestr(name, content)


def test_capture_task_xmls_picks_only_task_files(tmp_path: Path) -> None:
    cfx = tmp_path / "p.cfx"
    _make_cfx(
        cfx,
        {
            "config.xml": b"<Config/>",
            "Build-Task1.xml": b"<Settings>build</Settings>",
            "Retest-Task1.xml": b"<Settings>retest</Settings>",
            "version.txt": b"143.0",
        },
    )
    dest = tmp_path / "tmpl"
    dest.mkdir()
    captured, skipped = _capture_task_xmls(cfx, dest)
    assert sorted(captured) == ["Build-Task1.xml", "Retest-Task1.xml"]
    assert (dest / "Build-Task1.xml").read_bytes() == b"<Settings>build</Settings>"
    # Non-task members aren't captured
    assert "config.xml" in skipped
    assert "version.txt" in skipped


def test_apply_template_replaces_matching_task_xmls(tmp_path: Path) -> None:
    cfx = tmp_path / "p.cfx"
    _make_cfx(
        cfx,
        {
            "config.xml": b"<Config/>",
            "Build-Task1.xml": b"<Settings>OLD</Settings>",
        },
    )
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "Build-Task1.xml").write_bytes(b"<Settings>NEW</Settings>")

    report = _apply_template_to_cfx(cfx, tpl)

    assert report["replaced_count"] == 1
    assert "Build-Task1.xml" in report["replaced"][0]
    with zipfile.ZipFile(cfx, "r") as z:
        assert z.read("Build-Task1.xml") == b"<Settings>NEW</Settings>"
        # Non-template members preserved
        assert z.read("config.xml") == b"<Config/>"


def test_apply_template_flags_unused_template_files(tmp_path: Path) -> None:
    cfx = tmp_path / "p.cfx"
    _make_cfx(cfx, {"Build-Task1.xml": b"<x/>"})
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "Build-Task1.xml").write_bytes(b"<y/>")
    # This file doesn't exist in the target cfx → should be reported unused
    (tpl / "Optimize-Task1.xml").write_bytes(b"<z/>")

    report = _apply_template_to_cfx(cfx, tpl)

    assert "Optimize-Task1.xml" in report["template_files_unused"]


def test_apply_template_noop_when_template_empty(tmp_path: Path) -> None:
    cfx = tmp_path / "p.cfx"
    _make_cfx(cfx, {"Build-Task1.xml": b"<x/>"})
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    # No xml files in tpl → nothing replaced
    report = _apply_template_to_cfx(cfx, tpl)
    assert report["replaced_count"] == 0
