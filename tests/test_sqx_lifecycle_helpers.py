"""Unit tests for sqx_lifecycle args validation."""

from __future__ import annotations

import pytest

from sq_mcp.tools.sqx_lifecycle import SqxRenameArgs


def test_rename_accepts_valid_name() -> None:
    args = SqxRenameArgs(sqx_path="/tmp/foo.sqx", new_name="btcusdt_h1_001.sqx")
    assert args.new_name == "btcusdt_h1_001.sqx"


def test_rename_rejects_path_separators() -> None:
    with pytest.raises(ValueError):
        SqxRenameArgs(sqx_path="/tmp/foo.sqx", new_name="sub/name.sqx")
    with pytest.raises(ValueError):
        SqxRenameArgs(sqx_path="/tmp/foo.sqx", new_name="..\\evil.sqx")


def test_rename_rejects_path_traversal() -> None:
    with pytest.raises(ValueError):
        SqxRenameArgs(sqx_path="/tmp/foo.sqx", new_name="..foo.sqx")


def test_rename_rejects_non_sqx_extension() -> None:
    with pytest.raises(ValueError):
        SqxRenameArgs(sqx_path="/tmp/foo.sqx", new_name="rename.txt")


def test_rename_rejects_special_characters() -> None:
    with pytest.raises(ValueError):
        SqxRenameArgs(sqx_path="/tmp/foo.sqx", new_name="bad chars in name.sqx")
    with pytest.raises(ValueError):
        SqxRenameArgs(sqx_path="/tmp/foo.sqx", new_name="$shell.sqx")


def test_rename_accepts_alphanumeric_with_dots_dashes_underscores() -> None:
    args = SqxRenameArgs(sqx_path="/tmp/foo.sqx", new_name="A-B_C.1.sqx")
    assert args.new_name == "A-B_C.1.sqx"
