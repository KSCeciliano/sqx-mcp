"""Unit tests for regression-detection helpers."""

from __future__ import annotations

import json
from pathlib import Path

from sq_mcp.tools.regression import (
    _SNAPSHOT_VERSION,
    _classify,
    _compare_entries,
    _index_snapshot,
    _scrape_for_snapshot,
    _write_snapshot_atomically,
)

# ---- _scrape_for_snapshot --------------------------------------------------


def test_scrape_for_snapshot_keeps_only_tracked_keys() -> None:
    rows = [
        {
            "rel": "foo.sqx",
            "trades": 100,
            "fitness_oos": 0.5,
            "drawdown_pct": 10.0,
            "trades_hash": "abc",
            "fingerprint_exact": "def",
            "junk_field_we_dont_care_about": "ignored",
            "another_junk": [1, 2, 3],
        }
    ]
    out = _scrape_for_snapshot(rows)
    assert out[0]["rel"] == "foo.sqx"
    assert out[0]["trades_hash"] == "abc"
    assert "junk_field_we_dont_care_about" not in out[0]


# ---- _classify -------------------------------------------------------------


def test_classify_higher_is_better_regression() -> None:
    klass, pct = _classify(1.0, 0.5, threshold=0.10, direction="higher_is_better")
    assert klass == "regressed"
    assert pct == -0.5


def test_classify_higher_is_better_improvement() -> None:
    klass, pct = _classify(1.0, 1.5, threshold=0.10, direction="higher_is_better")
    assert klass == "improved"
    assert pct == 0.5


def test_classify_higher_is_better_stable_within_threshold() -> None:
    klass, _ = _classify(1.0, 1.05, threshold=0.10, direction="higher_is_better")
    assert klass == "stable"


def test_classify_lower_is_better_regression_when_value_grows() -> None:
    klass, pct = _classify(10.0, 20.0, threshold=0.10, direction="lower_is_better")
    # 100% jump → regression for "lower is better"
    assert klass == "regressed"
    assert pct == 1.0


def test_classify_lower_is_better_improvement_when_value_shrinks() -> None:
    klass, pct = _classify(10.0, 5.0, threshold=0.10, direction="lower_is_better")
    assert klass == "improved"
    assert pct == -0.5


def test_classify_handles_none_baseline() -> None:
    klass, pct = _classify(None, 1.0, threshold=0.10, direction="higher_is_better")
    assert klass == "missing"
    assert pct is None


def test_classify_handles_none_current() -> None:
    klass, _ = _classify(1.0, None, threshold=0.10, direction="higher_is_better")
    assert klass == "missing"


def test_classify_baseline_zero_treated_as_stable_when_current_is_zero() -> None:
    klass, pct = _classify(0, 0, threshold=0.10, direction="higher_is_better")
    assert klass == "stable"
    assert pct == 0.0


# ---- _compare_entries ------------------------------------------------------


def test_compare_entries_flags_regression_on_any_metric() -> None:
    baseline = {
        "fitness_oos": 1.0,
        "profit_to_dd_ratio": 5.0,
        "return_pct": 50.0,
        "drawdown_pct": 10.0,
    }
    current = {
        "fitness_oos": 0.4,           # >10% drop → regressed
        "profit_to_dd_ratio": 5.0,    # unchanged
        "return_pct": 50.0,
        "drawdown_pct": 10.0,
    }
    result = _compare_entries(baseline, current, threshold=0.10)
    assert result["overall"] == "regressed"
    assert result["metrics"]["fitness_oos"]["classification"] == "regressed"
    assert result["metrics"]["profit_to_dd_ratio"]["classification"] == "stable"


def test_compare_entries_overall_improved_when_no_regressions() -> None:
    baseline = {
        "fitness_oos": 0.5,
        "profit_to_dd_ratio": 1.0,
        "return_pct": 10.0,
        "drawdown_pct": 20.0,
    }
    current = {
        "fitness_oos": 0.7,           # improved
        "profit_to_dd_ratio": 1.5,    # improved
        "return_pct": 10.5,           # stable
        "drawdown_pct": 19.5,         # stable
    }
    result = _compare_entries(baseline, current, threshold=0.10)
    assert result["overall"] == "improved"


def test_compare_entries_stable_when_all_within_threshold() -> None:
    baseline = {
        "fitness_oos": 0.5,
        "profit_to_dd_ratio": 2.0,
        "return_pct": 25.0,
        "drawdown_pct": 10.0,
    }
    current = {
        "fitness_oos": 0.51,
        "profit_to_dd_ratio": 2.05,
        "return_pct": 25.5,
        "drawdown_pct": 10.1,
    }
    result = _compare_entries(baseline, current, threshold=0.10)
    assert result["overall"] == "stable"


def test_compare_entries_drawdown_regression_when_growing() -> None:
    baseline = {"fitness_oos": 0.5, "drawdown_pct": 10.0}
    current = {"fitness_oos": 0.5, "drawdown_pct": 30.0}
    result = _compare_entries(baseline, current, threshold=0.10)
    # DD grew 200% → regression
    assert result["metrics"]["drawdown_pct"]["classification"] == "regressed"
    assert result["overall"] == "regressed"


# ---- _index_snapshot -------------------------------------------------------


def test_index_snapshot_by_trades_hash() -> None:
    entries = [
        {"rel": "a", "trades_hash": "H1"},
        {"rel": "b", "trades_hash": "H2"},
        {"rel": "c", "trades_hash": None},
    ]
    idx = _index_snapshot(entries, match_on="trades_hash")
    assert set(idx) == {"H1", "H2"}
    assert idx["H1"]["rel"] == "a"


def test_index_snapshot_by_rel_path() -> None:
    entries = [
        {"rel": "foo/a.sqx", "trades_hash": "H1"},
        {"rel": "foo/b.sqx", "trades_hash": "H2"},
        {"rel": None, "trades_hash": "H3"},
    ]
    idx = _index_snapshot(entries, match_on="rel_path")
    assert set(idx) == {"foo/a.sqx", "foo/b.sqx"}


# ---- _write_snapshot_atomically -------------------------------------------


def test_write_snapshot_atomically_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "snap.json"
    payload = {
        "snapshot_version": _SNAPSHOT_VERSION,
        "label": "post-build",
        "entries": [{"rel": "x", "trades_hash": "H1"}],
    }
    _write_snapshot_atomically(p, payload)
    assert p.exists()
    read_back = json.loads(p.read_text(encoding="utf-8"))
    assert read_back == payload
    # No leftover tmp
    assert not p.with_suffix(p.suffix + ".tmp").exists()


def test_write_snapshot_atomically_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "dir" / "snap.json"
    _write_snapshot_atomically(p, {"v": 1})
    assert p.exists()
