"""Unit tests for batch.py orchestration helpers."""

from __future__ import annotations

from pathlib import Path

from sq_mcp.tools.batch import _classify_status_text, _count_databanks_for_project

# ---- _classify_status_text -------------------------------------------------


def test_classify_status_running() -> None:
    assert _classify_status_text("Project is running") == "running"
    assert _classify_status_text("Started OK") == "running"


def test_classify_status_paused() -> None:
    assert _classify_status_text("Paused at iteration 12") == "paused"


def test_classify_status_stopped() -> None:
    assert _classify_status_text("Stopped") == "stopped"
    assert _classify_status_text("Build finished") == "stopped"
    assert _classify_status_text("not running") == "stopped"


def test_classify_status_unknown_for_empty_or_alien() -> None:
    assert _classify_status_text("") == "unknown"
    assert _classify_status_text("some unexpected text") == "unknown"


def test_classify_status_handles_none_safely() -> None:
    # The helper accepts an empty/None-ish text without raising
    assert _classify_status_text(None) == "unknown"


# ---- _count_databanks_for_project ----------------------------------------


def test_count_databanks_empty_project(tmp_path: Path) -> None:
    out = _count_databanks_for_project(tmp_path)
    assert out == {"databanks": 0, "sqx_files_total": 0, "by_databank": []}


def test_count_databanks_counts_sqx(tmp_path: Path) -> None:
    # tmp/databanks/Results/a.sqx, b.sqx; tmp/databanks/Best/c.sqx
    db = tmp_path / "databanks"
    (db / "Results").mkdir(parents=True)
    (db / "Best").mkdir(parents=True)
    (db / "Results" / "a.sqx").write_bytes(b"x")
    (db / "Results" / "b.sqx").write_bytes(b"x")
    (db / "Best" / "c.sqx").write_bytes(b"x")
    out = _count_databanks_for_project(tmp_path)
    assert out["databanks"] == 2
    assert out["sqx_files_total"] == 3
    by_db = {x["name"]: x["sqx_count"] for x in out["by_databank"]}
    assert by_db == {"Results": 2, "Best": 1}


def test_count_databanks_skips_non_directories(tmp_path: Path) -> None:
    db = tmp_path / "databanks"
    db.mkdir()
    (db / "stray_file.txt").write_bytes(b"x")
    (db / "Real").mkdir()
    (db / "Real" / "ok.sqx").write_bytes(b"x")
    out = _count_databanks_for_project(tmp_path)
    assert out["databanks"] == 1
    assert out["sqx_files_total"] == 1
