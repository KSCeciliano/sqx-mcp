"""Unit tests for pipeline orchestration helpers in tools/projects.py.

Focuses on the pure-Python helpers that don't need a live engine:
  * _count_sqx
  * _existing_dest_hashes
  * _wait_result composition
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from sq_mcp.tools.projects import (
    _count_sqx,
    _existing_dest_hashes,
    _wait_result,
)


def _stub_sqx(path: Path, hash_value: str = "h1") -> None:
    settings = f"""<?xml version="1.0"?>
<ResultsGroup ResultName="x"><ResultsMap><Results><Result resultKey="x">
  <Fitnesses IS="0.5"/></Result></Results></ResultsMap>
<SpecialValuesMap><SettingsMap>
  <Fingerprint type="com.strategyquant.tradinglib.results.StrategyFingerprint">
    <Fingerprint strategyName="x" trades="100" profit="1000" drawdown="200" fitness="0.5" tradesHash="{hash_value}"/>
  </Fingerprint>
</SettingsMap></SpecialValuesMap></ResultsGroup>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version.txt", "1")
        z.writestr("settings.xml", settings)
    path.write_bytes(buf.getvalue())


def test_count_sqx_missing_dir(tmp_path: Path) -> None:
    assert _count_sqx(tmp_path / "missing") == 0


def test_count_sqx_counts_recursive(tmp_path: Path) -> None:
    db = tmp_path / "bank"
    db.mkdir()
    _stub_sqx(db / "a.sqx")
    _stub_sqx(db / "b.sqx")
    sub = db / "sub"
    sub.mkdir()
    _stub_sqx(sub / "c.sqx")
    # Non-sqx file ignored
    (db / "ignore.txt").write_text("x")
    assert _count_sqx(db) == 3


def test_existing_dest_hashes_includes_fingerprint(tmp_path: Path) -> None:
    db = tmp_path / "bank"
    db.mkdir()
    _stub_sqx(db / "a.sqx", hash_value="111")
    _stub_sqx(db / "b.sqx", hash_value="222")
    assert _existing_dest_hashes(db) == {"111", "222"}


def test_existing_dest_hashes_skips_unparseable(tmp_path: Path) -> None:
    db = tmp_path / "bank"
    db.mkdir()
    _stub_sqx(db / "a.sqx", hash_value="111")
    (db / "broken.sqx").write_text("not a zip")
    assert _existing_dest_hashes(db) == {"111"}


def test_wait_result_completed_payload() -> None:
    res = _wait_result(
        ok=True,
        reason="completion_marker",
        elapsed=12.3456,
        final_count=42,
        snapshots=[{"i": i} for i in range(15)],
    )
    assert res["ok"] is True
    assert res["completed"] is True
    assert res["timed_out"] is False
    assert res["reason"] == "completion_marker"
    assert res["elapsed_seconds"] == 12.3
    assert res["final_strategy_count"] == 42
    assert res["polls"] == 15
    assert len(res["tail_snapshots"]) == 10


def test_wait_result_timeout_payload() -> None:
    res = _wait_result(
        ok=False,
        reason="timeout",
        elapsed=999.0,
        final_count=0,
        snapshots=[],
        timed_out=True,
    )
    assert res["ok"] is False
    assert res["completed"] is False
    assert res["timed_out"] is True
    assert res["polls"] == 0
    assert res["tail_snapshots"] == []
