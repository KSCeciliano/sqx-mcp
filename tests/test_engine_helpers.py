"""Unit tests for engine.py pure-function helpers — no subprocess needed."""

from __future__ import annotations

import pytest

from sq_mcp.engine import (
    DEFAULT_CALL_TIMEOUT,
    EngineClient,
    EngineError,
    _encode_cmd,
    _is_noise,
    _quote_arg,
    _strip_noise,
)

# ---- _is_noise / _strip_noise ------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "16:07:04.916 [main] DEBUG oshi.util.FileUtil - Reading file /proc/self/auxv",
        "DEBUG o.s.os.linux.LinuxOperatingSystem - Fedora release 43",
        "Reading file /sys/devices/system/cpu/cpu0/topology/core_id",
        "WARNING: Invalid cookie header: \"set-cookie: ...\"",
        "",
        "    ",
    ],
)
def test_is_noise_drops_known_patterns(line):
    assert _is_noise(line)


@pytest.mark.parametrize(
    "line",
    [
        "Server started on port 5050",
        "SQX version: 143.2708",
        "All tasks completed",
        "EURUSD_H1_1101227115",
    ],
)
def test_is_noise_keeps_real_output(line):
    assert not _is_noise(line)


def test_strip_noise_preserves_data():
    text = "\n".join(
        [
            "DEBUG oshi.util.FileUtil - blah",
            "Server started on port 5050",
            "Some real output",
            "DEBUG oshi.util.FileUtil - more",
            "Another line",
            "",
        ]
    )
    cleaned = _strip_noise(text)
    assert "DEBUG oshi" not in cleaned
    assert "Server started on port 5050" in cleaned
    assert "Some real output" in cleaned
    assert "Another line" in cleaned


# ---- _quote_arg --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("simple", "simple"),
        ("with-dash", "with-dash"),
        ("path/to/file.csv", "path/to/file.csv"),
        ("has space", "'has space'"),
        ('has"quote', "'has\"quote'"),
        ("has;semi", "'has;semi'"),
        ("has&amp", "'has&amp'"),
        ("has|pipe", "'has|pipe'"),
        ("has\nnewline", "'has\nnewline'"),
    ],
)
def test_quote_arg(raw, expected):
    assert _quote_arg(raw) == expected


# ---- _encode_cmd -------------------------------------------------------------


def test_encode_cmd_leaves_equals_and_dash_literal():
    # sqcli's HTTP server does NOT URL-decode, so `=` and `-` must pass through.
    out = _encode_cmd("-project action=list")
    assert "=" in out
    assert "-" in out
    assert "%3D" not in out
    assert out.startswith("-project")


def test_encode_cmd_encodes_space_as_percent_20():
    # Space must be %20, not `+` (sqcli does not decode `+`).
    out = _encode_cmd("-databank name=Last generation")
    assert "%20" in out
    assert "+" not in out  # `+` is in the safe set so a literal `+` would survive,
                            # but the input has none — encoded space must be %20.


def test_encode_cmd_windows_path_backslashes_become_forward_slashes():
    # SQ X is Java-based and accepts `/` on Windows; backslashes would be
    # %5C-encoded by httpx and sqcli would not decode them.
    out = _encode_cmd(r"-data action=import filepath=C:\Users\me\file.csv")
    assert "C:/Users/me/file.csv" in out
    assert "%5C" not in out
    assert "\\" not in out


def test_encode_cmd_preserves_quoted_strategy_list():
    # Quotes are in the safe set — strategies list with comma must round-trip
    out = _encode_cmd('-databank action=save strategies="Strategy 0.1,Strategy 0.2"')
    assert '"Strategy%200.1,Strategy%200.2"' in out


# ---- EngineClient API surface -----------------------------------------------


def test_default_call_timeout_is_sane():
    assert 30 <= DEFAULT_CALL_TIMEOUT <= 600


def test_engine_client_constructor_does_not_spawn():
    eng = EngineClient()
    assert not eng.is_running
    assert eng.recent_log == []


@pytest.mark.asyncio
async def test_engine_call_with_empty_string_raises():
    eng = EngineClient()
    with pytest.raises(EngineError, match="non-empty string"):
        await eng.call("")
    with pytest.raises(EngineError, match="non-empty string"):
        await eng.call("   ")
