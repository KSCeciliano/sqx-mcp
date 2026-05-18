"""Unit tests for passthrough.EngineCallArgs validation."""

from __future__ import annotations

import pytest

from sq_mcp.tools.passthrough import EngineCallArgs


def test_engine_call_accepts_valid_command() -> None:
    args = EngineCallArgs(command="-databank action=list project=Builder name=Results")
    assert args.command.startswith("-")


def test_engine_call_rejects_missing_leading_dash() -> None:
    with pytest.raises(ValueError) as exc:
        EngineCallArgs(command="databank action=list")
    assert "must start with '-'" in str(exc.value)


def test_engine_call_rejects_multiline() -> None:
    with pytest.raises(ValueError) as exc:
        EngineCallArgs(command="-databank action=list\nproject=Builder")
    assert "single line" in str(exc.value)


def test_engine_call_rejects_shell_metacharacters() -> None:
    bad_inputs = [
        "-rm something",
        "-databank action=list; rm /tmp/x",
        "-foo && bar",
        "-foo $(whoami)",
        "-foo `id`",
    ]
    for cmd in bad_inputs:
        with pytest.raises(ValueError) as exc:
            EngineCallArgs(command=cmd)
        assert "forbidden" in str(exc.value)


def test_engine_call_strips_whitespace() -> None:
    args = EngineCallArgs(command="   -h   ")
    assert args.command == "-h"


def test_engine_call_respects_max_length() -> None:
    huge = "-" + "x" * 3000
    with pytest.raises(ValueError):
        EngineCallArgs(command=huge)
