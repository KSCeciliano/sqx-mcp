"""Unit tests for MT5 helpers (path resolution + file enumeration)."""

from __future__ import annotations

from pathlib import Path

from sq_mcp.tools.mt5 import (
    _enumerate_files,
    _resolve_mt5_install_root,
)


def test_enumerate_files_filters_by_extension(tmp_path: Path) -> None:
    (tmp_path / "a.mq5").write_text("// EA source")
    (tmp_path / "a.ex5").write_bytes(b"\x00\x01\x02")
    (tmp_path / "b.txt").write_text("not an EA")
    sub = tmp_path / "Examples"
    sub.mkdir()
    (sub / "c.mq5").write_text("// nested")
    (sub / "d.ex5").write_bytes(b"\x00")

    entries = _enumerate_files(tmp_path, suffixes=(".mq5", ".ex5"))
    names = sorted(e["name"] for e in entries)
    assert names == ["a.ex5", "a.mq5", "c.mq5", "d.ex5"]
    # All entries should carry the metadata fields
    for e in entries:
        assert e.keys() >= {"name", "rel_path", "abs_path", "size_bytes", "mtime"}


def test_enumerate_files_missing_root_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    assert _enumerate_files(missing, suffixes=(".mq5",)) == []


def test_resolve_mt5_install_root_respects_env(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "Fake MT5"
    (fake / "MQL5").mkdir(parents=True)
    monkeypatch.setenv("MT5_HOME", str(fake))
    resolved = _resolve_mt5_install_root()
    assert resolved == fake


def test_resolve_mt5_install_root_missing_mql5_dir_ignored(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "Has No MQL5"
    fake.mkdir(parents=True)
    monkeypatch.setenv("MT5_HOME", str(fake))
    resolved = _resolve_mt5_install_root()
    # MT5_HOME without MQL5/ should be rejected; we fall through to platform defaults
    # which may or may not exist on the test machine — accept either None or another path.
    assert resolved is None or resolved != fake


def test_resolve_mt5_install_root_picks_first_valid(tmp_path: Path, monkeypatch) -> None:
    valid = tmp_path / "Valid"
    (valid / "MQL5").mkdir(parents=True)
    monkeypatch.setenv("MT5_HOME", str(valid))
    assert _resolve_mt5_install_root() == valid
