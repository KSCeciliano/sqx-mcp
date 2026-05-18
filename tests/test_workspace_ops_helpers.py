"""Unit tests for workspace_ops helpers."""

from __future__ import annotations

import zipfile
from pathlib import Path

from sq_mcp.tools.workspace_ops import (
    _archive_strategies,
    _batch_rename,
    _clone_strategy,
    _restore_archive,
)


def test_clone_strategy_dry_run(tmp_path: Path) -> None:
    src = tmp_path / "orig.sqx"
    src.write_bytes(b"x" * 100)
    result = _clone_strategy(src, "copy.sqx", overwrite=False, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert not (tmp_path / "copy.sqx").exists()


def test_clone_strategy_actual_copy(tmp_path: Path) -> None:
    src = tmp_path / "orig.sqx"
    src.write_bytes(b"x" * 100)
    result = _clone_strategy(src, "copy.sqx", overwrite=False, dry_run=False)
    assert result["ok"] is True
    target = tmp_path / "copy.sqx"
    assert target.exists()
    assert target.read_bytes() == b"x" * 100


def test_clone_strategy_refuses_overwrite(tmp_path: Path) -> None:
    src = tmp_path / "orig.sqx"
    src.write_bytes(b"x")
    existing = tmp_path / "copy.sqx"
    existing.write_bytes(b"existing")
    result = _clone_strategy(src, "copy.sqx", overwrite=False, dry_run=False)
    assert result["ok"] is False
    # Existing untouched
    assert existing.read_bytes() == b"existing"


def test_archive_strategies_creates_zip(tmp_path: Path) -> None:
    f1 = tmp_path / "a.sqx"
    f1.write_bytes(b"aaa")
    f2 = tmp_path / "b.sqx"
    f2.write_bytes(b"bbb")
    archive = tmp_path / "out.zip"
    result = _archive_strategies([f1, f2], archive, overwrite=False, dry_run=False)
    assert result["ok"] is True
    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        assert sorted(zf.namelist()) == ["a.sqx", "b.sqx"]


def test_archive_strategies_dry_run(tmp_path: Path) -> None:
    f1 = tmp_path / "a.sqx"
    f1.write_bytes(b"aaa")
    archive = tmp_path / "out.zip"
    result = _archive_strategies([f1], archive, overwrite=False, dry_run=True)
    assert result["ok"] is True
    assert not archive.exists()


def test_archive_strategies_missing_input(tmp_path: Path) -> None:
    result = _archive_strategies(
        [tmp_path / "missing.sqx"], tmp_path / "out.zip",
        overwrite=False, dry_run=False,
    )
    assert result["ok"] is False
    assert "missing" in result


def test_restore_archive_round_trip(tmp_path: Path) -> None:
    f1 = tmp_path / "a.sqx"
    f1.write_bytes(b"original-aaa")
    archive = tmp_path / "out.zip"
    _archive_strategies([f1], archive, overwrite=False, dry_run=False)
    f1.unlink()
    target = tmp_path / "restored"
    result = _restore_archive(archive, target, overwrite_existing=False, dry_run=False)
    assert result["ok"] is True
    assert (target / "a.sqx").read_bytes() == b"original-aaa"


def test_restore_archive_path_traversal_filtered(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.sqx", b"evil")
        zf.writestr("legit.sqx", b"ok")
    target = tmp_path / "restored"
    result = _restore_archive(archive, target, overwrite_existing=True, dry_run=False)
    assert result["ok"] is True
    # Only legit.sqx should be extracted
    assert (target / "legit.sqx").exists()
    assert not (tmp_path / "escape.sqx").exists()


def test_batch_rename_dry_run(tmp_path: Path) -> None:
    (tmp_path / "old1.sqx").write_bytes(b"x")
    (tmp_path / "old2.sqx").write_bytes(b"x")
    result = _batch_rename(tmp_path, r"^old", "new", "*.sqx", dry_run=True)
    assert result["ok"] is True
    assert result["n_renames"] == 2
    assert (tmp_path / "old1.sqx").exists()


def test_batch_rename_actual(tmp_path: Path) -> None:
    (tmp_path / "old1.sqx").write_bytes(b"x")
    result = _batch_rename(tmp_path, r"^old", "new", "*.sqx", dry_run=False)
    assert result["ok"] is True
    assert not (tmp_path / "old1.sqx").exists()
    assert (tmp_path / "new1.sqx").exists()


def test_batch_rename_collision_refused(tmp_path: Path) -> None:
    (tmp_path / "old1.sqx").write_bytes(b"x")
    (tmp_path / "new1.sqx").write_bytes(b"existing")
    result = _batch_rename(tmp_path, r"^old", "new", "*.sqx", dry_run=False)
    assert result["ok"] is False
    assert "conflicts" in result
    # Existing untouched
    assert (tmp_path / "new1.sqx").read_bytes() == b"existing"
