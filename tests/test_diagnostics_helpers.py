"""Unit tests for diagnostics helpers (workspace_cleanup token)."""

from __future__ import annotations

from sq_mcp.tools.diagnostics import _dry_run_token

_HEX = set("0123456789abcdef")


def test_dry_run_token_is_deterministic_for_same_input() -> None:
    a = _dry_run_token("TEST_*", ["TEST_1", "TEST_2"])
    b = _dry_run_token("TEST_*", ["TEST_2", "TEST_1"])  # order-independent
    assert a == b
    assert len(a) == 10
    assert all(c in _HEX for c in a)


def test_dry_run_token_differs_when_pattern_changes() -> None:
    a = _dry_run_token("TEST_*", ["TEST_1"])
    b = _dry_run_token("PROD_*", ["TEST_1"])
    assert a != b


def test_dry_run_token_differs_when_targets_change() -> None:
    a = _dry_run_token("TEST_*", ["TEST_1"])
    b = _dry_run_token("TEST_*", ["TEST_1", "TEST_2"])
    assert a != b


def test_dry_run_token_empty_targets() -> None:
    t = _dry_run_token("anything", [])
    assert len(t) == 10
    assert all(c in _HEX for c in t)


def test_dry_run_token_stable_across_processes() -> None:
    """SHA-256-based token must be deterministic across Python runs (unlike hash())."""
    expected = _dry_run_token("FOO_*", ["a", "b", "c"])
    # The hex digest of "FOO_*|a,b,c" via sha256 is stable.
    import hashlib
    raw = "FOO_*|a,b,c"
    assert expected == hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
