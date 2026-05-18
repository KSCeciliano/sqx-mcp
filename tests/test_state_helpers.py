"""Unit tests for state.py persistent store helpers."""

from __future__ import annotations

import json
from pathlib import Path

from sq_mcp.tools.state import _read_state, _write_state_atomic

# ---- _read_state ----------------------------------------------------------


def test_read_state_missing_file_returns_empty(tmp_path: Path) -> None:
    out = _read_state(tmp_path / "no.json")
    assert out == {"version": 1, "data": {}}


def test_read_state_existing_file(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    payload = {"version": 1, "data": {"ns": {"k": "v"}}}
    p.write_text(json.dumps(payload), encoding="utf-8")
    out = _read_state(p)
    assert out == payload


def test_read_state_corrupted_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "corrupt.json"
    p.write_bytes(b"\xff\xfe not json")
    out = _read_state(p)
    assert out == {"version": 1, "data": {}}


def test_read_state_invalid_json_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{not valid json}", encoding="utf-8")
    out = _read_state(p)
    assert out == {"version": 1, "data": {}}


# ---- _write_state_atomic --------------------------------------------------


def test_write_state_atomic_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    payload = {"version": 1, "data": {"ns": {"a": 1, "b": [1, 2, 3]}}}
    _write_state_atomic(p, payload)
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8")) == payload
    # No tmp leftover
    assert not p.with_suffix(p.suffix + ".tmp").exists()


def test_write_state_atomic_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "nested" / "state.json"
    _write_state_atomic(p, {"version": 1, "data": {}})
    assert p.exists()


def test_write_state_atomic_overwrites_existing(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"version": 1, "data": {"old": True}}), encoding="utf-8")
    _write_state_atomic(p, {"version": 1, "data": {"new": True}})
    out = json.loads(p.read_text(encoding="utf-8"))
    assert out["data"] == {"new": True}
