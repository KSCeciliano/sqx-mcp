"""Unit tests for backup module helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sq_mcp.tools.backup import _gather_project_manifest, _sha256_of_file

# ---- _sha256_of_file ------------------------------------------------------


def test_sha256_of_file(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    assert _sha256_of_file(p) == hashlib.sha256(b"abc").hexdigest()


def test_sha256_of_file_missing_returns_none(tmp_path: Path) -> None:
    assert _sha256_of_file(tmp_path / "nope") is None


# ---- _gather_project_manifest --------------------------------------------


def test_gather_project_manifest_empty_project(tmp_path: Path) -> None:
    out = _gather_project_manifest(tmp_path)
    # No project.cfx → cfx is None, no databanks
    assert out["cfx"] is None
    assert out["databanks"] == []


def test_gather_project_manifest_with_cfx_and_databanks(tmp_path: Path) -> None:
    (tmp_path / "project.cfx").write_bytes(b"fake cfx contents")
    db = tmp_path / "databanks"
    (db / "Results").mkdir(parents=True)
    (db / "Results" / "a.sqx").write_bytes(b"x")
    (db / "Results" / "b.sqx").write_bytes(b"x")
    (db / "Best").mkdir()
    (db / "Best" / "c.sqx").write_bytes(b"x")
    out = _gather_project_manifest(tmp_path)
    assert out["cfx"] is not None
    assert out["cfx"]["size_bytes"] == len(b"fake cfx contents")
    assert {db["name"] for db in out["databanks"]} == {"Results", "Best"}
    by_name = {db["name"]: db["sqx_count"] for db in out["databanks"]}
    assert by_name == {"Results": 2, "Best": 1}
