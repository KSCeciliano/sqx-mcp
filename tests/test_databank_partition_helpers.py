"""Unit tests for databank_partition helpers."""

from __future__ import annotations

from sq_mcp.tools.databank_partition import _partition_rows, _slim_row


def test_partition_rows_basic() -> None:
    rows = [
        {"rel": "a", "fitness_oos": 0.9},
        {"rel": "b", "fitness_oos": 0.5},
        {"rel": "c", "fitness_oos": 0.3},
        {"rel": "d", "fitness_oos": None},
    ]
    parts = _partition_rows(rows, metric="fitness_oos", threshold=0.5)
    assert {r["rel"] for r in parts["above"]} == {"a", "b"}
    assert {r["rel"] for r in parts["below"]} == {"c"}
    assert {r["rel"] for r in parts["missing"]} == {"d"}


def test_partition_rows_threshold_inclusive() -> None:
    rows = [{"rel": "exact", "fitness_oos": 0.5}]
    parts = _partition_rows(rows, metric="fitness_oos", threshold=0.5)
    assert parts["above"][0]["rel"] == "exact"
    assert parts["below"] == []


def test_partition_rows_empty() -> None:
    parts = _partition_rows([], metric="fitness_oos", threshold=0.5)
    assert parts == {"above": [], "below": [], "missing": []}


def test_partition_rows_lower_is_better_metric() -> None:
    # Use drawdown_pct semantically: "above" doesn't mean better here, just numeric >=
    rows = [
        {"rel": "low_dd", "drawdown_pct": 5.0},
        {"rel": "high_dd", "drawdown_pct": 40.0},
    ]
    parts = _partition_rows(rows, metric="drawdown_pct", threshold=20.0)
    assert {r["rel"] for r in parts["above"]} == {"high_dd"}
    assert {r["rel"] for r in parts["below"]} == {"low_dd"}


def test_slim_row_keeps_only_canonical_keys() -> None:
    r = {
        "rel": "x.sqx",
        "junk": "ignored",
        "fitness_oos": 0.5,
        "trades": 100,
    }
    out = _slim_row(r)
    assert "junk" not in out
    assert out["rel"] == "x.sqx"
    assert out["trades"] == 100
    # Missing keys come back as None
    assert "drawdown_pct" in out
    assert out["drawdown_pct"] is None
