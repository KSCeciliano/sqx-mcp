"""Security hardening tests: input validation rejects path traversal & overlong inputs."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from sq_mcp.tools.diagnostics import WorkspaceCleanupArgs
from sq_mcp.tools.mt5 import Mt5DeployEaArgs

# ---- Mt5DeployEaArgs --------------------------------------------------------


def test_mt5_subdir_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        Mt5DeployEaArgs(source_path="/tmp/x.mq5", subdir="/etc/passwd")


def test_mt5_subdir_rejects_backslash() -> None:
    with pytest.raises(ValidationError):
        Mt5DeployEaArgs(source_path="/tmp/x.mq5", subdir="Experts\\..\\..\\Windows")


def test_mt5_subdir_rejects_traversal() -> None:
    for bad in ("..", "a/../b", "./x", "..//x", "x/.."):
        with pytest.raises(ValidationError):
            Mt5DeployEaArgs(source_path="/tmp/x.mq5", subdir=bad)


def test_mt5_subdir_allows_nested_safe_path() -> None:
    args = Mt5DeployEaArgs(source_path="/tmp/x.mq5", subdir="SQ/BTC/H1")
    assert args.subdir == "SQ/BTC/H1"


def test_mt5_subdir_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        Mt5DeployEaArgs(source_path="/tmp/x.mq5", subdir="a" * 200)


def test_mt5_rename_rejects_path_separators() -> None:
    for bad in ("a/b.mq5", "a\\b.mq5", "../escape.mq5"):
        with pytest.raises(ValidationError):
            Mt5DeployEaArgs(source_path="/tmp/x.mq5", rename_to=bad)


def test_mt5_rename_requires_valid_extension() -> None:
    with pytest.raises(ValidationError):
        Mt5DeployEaArgs(source_path="/tmp/x.mq5", rename_to="garbage.txt")


def test_mt5_rename_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        Mt5DeployEaArgs(source_path="/tmp/x.mq5", rename_to="a" * 130 + ".mq5")


def test_mt5_rename_accepts_bare_filename() -> None:
    args = Mt5DeployEaArgs(source_path="/tmp/x.mq5", rename_to="my_ea.mq5")
    assert args.rename_to == "my_ea.mq5"


# ---- WorkspaceCleanupArgs --------------------------------------------------


def test_workspace_cleanup_pattern_rejects_path_separator() -> None:
    for bad in ("foo/bar", "foo\\bar"):
        with pytest.raises(ValidationError):
            WorkspaceCleanupArgs(pattern=bad)


def test_workspace_cleanup_pattern_rejects_traversal() -> None:
    with pytest.raises(ValidationError):
        WorkspaceCleanupArgs(pattern="..")
    with pytest.raises(ValidationError):
        WorkspaceCleanupArgs(pattern="../*")


def test_workspace_cleanup_pattern_rejects_control_chars() -> None:
    with pytest.raises(ValidationError):
        WorkspaceCleanupArgs(pattern="abc\nxyz")
    with pytest.raises(ValidationError):
        WorkspaceCleanupArgs(pattern="abc\x00xyz")


def test_workspace_cleanup_pattern_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkspaceCleanupArgs(pattern="x" * 250)


def test_workspace_cleanup_keep_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkspaceCleanupArgs(pattern="*", keep=["proj"] * 200)


def test_workspace_cleanup_accepts_glob_patterns() -> None:
    args = WorkspaceCleanupArgs(pattern="TEST_*")
    assert args.pattern == "TEST_*"
    assert args.dry_run is True  # default safety


# ---- atomic write recovery --------------------------------------------------


def test_robustness_apply_to_cfx_atomic_recovery(tmp_path: Path) -> None:
    """When the patcher itself raises, the original .cfx must remain untouched."""
    from sq_mcp.tools.robustness import _apply_to_cfx
    # Build a minimal valid .cfx with one Retest task
    cfx_path = tmp_path / "project.cfx"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "config.xml",
            b"<?xml version='1.0'?><Project name='x' version='3'>"
            b"<Tasks><Task taskXMLFile='Retest-Task1.xml' type='Retest'/></Tasks></Project>",
        )
        z.writestr(
            "Retest-Task1.xml",
            b"<?xml version='1.0'?><Settings><CrossChecks>"
            b"<MonteCarloRetest use='false'/></CrossChecks></Settings>",
        )
    cfx_path.write_bytes(buf.getvalue())
    original_bytes = cfx_path.read_bytes()

    def _broken_patcher(_raw: bytes):
        raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError, match="synthetic"):
        _apply_to_cfx(cfx_path, target_first_only=False, patcher=_broken_patcher)

    # Original .cfx still intact, no .tmp left behind
    assert cfx_path.read_bytes() == original_bytes
    assert not (cfx_path.with_suffix(".cfx.tmp")).exists()
