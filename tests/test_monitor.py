"""Unit tests for the monitor — synthetic snapshots + a fake engine."""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from sq_mcp.monitor import (
    DEFAULT_RULES,
    MonitorManager,
    MonitorRule,
    MonitorSession,
    MonitorSnapshot,
    _extract_count,
    _safe_median,
    _safe_percentile,
)

# ---- helpers ----------------------------------------------------------------


def _snap(**overrides) -> MonitorSnapshot:
    base = dict(
        timestamp=time.time(),
        strategy_count=0,
        new_since_last=0,
        median_fitness=None,
        p75_fitness=None,
        median_fitness_oos=None,
        diversity_score=None,
    )
    base.update(overrides)
    return MonitorSnapshot(**base)


@dataclass
class _FakeEngine:
    """Minimal stand-in for EngineClient — records calls."""

    calls: list[str]

    def __init__(self):
        self.calls = []
        self.config = type("Cfg", (), {"projects_dir": _FakePath()})()

    async def call(self, cmd, *, timeout=None, **kw):
        self.calls.append(cmd)
        if "action=count" in cmd:
            return "All tasks completed\n123\n"
        if "action=status" in cmd:
            return "running 35%"
        return "OK"


class _FakePath:
    def __truediv__(self, _):
        return self
    def exists(self):
        return False


# ---- pure helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("count: 42", 42),
        ("All tasks completed\n7\n", 7),
        ("no number here", None),
        ("", None),
    ],
)
def test_extract_count(text, expected):
    assert _extract_count(text) == expected


def test_safe_median_empty():
    assert _safe_median([]) is None


def test_safe_median():
    assert _safe_median([1.0, 2.0, 3.0]) == 2.0


def test_safe_percentile_empty():
    assert _safe_percentile([], 0.5) is None


def test_safe_percentile_single():
    assert _safe_percentile([0.42], 0.75) == 0.42


def test_safe_percentile_basic():
    xs = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert _safe_percentile(xs, 0.5) == 0.3
    assert _safe_percentile(xs, 0.0) == 0.1
    assert _safe_percentile(xs, 1.0) == 0.5


# ---- rule evaluation --------------------------------------------------------


def _make_session(rules: list[MonitorRule], snapshots: list[MonitorSnapshot]) -> MonitorSession:
    sess = MonitorSession(project="X", rules=rules)
    sess.snapshots = list(snapshots)
    return sess


def test_stalled_growth_fires():
    mgr = MonitorManager(_FakeEngine())  # type: ignore[arg-type]
    rule = MonitorRule(code="STALLED_GROWTH", description="", threshold=0, window_checks=3)
    snapshots = [_snap(strategy_count=10, new_since_last=0) for _ in range(3)]
    sess = _make_session([rule], snapshots)
    alert = mgr._check_rule(sess, rule, snapshots[-1])
    assert alert is not None
    assert alert.rule_code == "STALLED_GROWTH"


def test_stalled_growth_does_not_fire_when_growing():
    mgr = MonitorManager(_FakeEngine())  # type: ignore[arg-type]
    rule = MonitorRule(code="STALLED_GROWTH", description="", threshold=0, window_checks=3)
    snapshots = [
        _snap(strategy_count=10, new_since_last=10),
        _snap(strategy_count=20, new_since_last=10),
        _snap(strategy_count=30, new_since_last=10),
    ]
    sess = _make_session([rule], snapshots)
    assert mgr._check_rule(sess, rule, snapshots[-1]) is None


def test_low_median_fitness_fires():
    mgr = MonitorManager(_FakeEngine())  # type: ignore[arg-type]
    rule = MonitorRule(
        code="LOW_MEDIAN_FITNESS", description="", threshold=0.3, window_checks=2
    )
    snapshots = [
        _snap(median_fitness=0.10),
        _snap(median_fitness=0.15),
    ]
    sess = _make_session([rule], snapshots)
    alert = mgr._check_rule(sess, rule, snapshots[-1])
    assert alert is not None
    assert "median fitness" in alert.message


def test_low_median_fitness_does_not_fire_above_threshold():
    mgr = MonitorManager(_FakeEngine())  # type: ignore[arg-type]
    rule = MonitorRule(
        code="LOW_MEDIAN_FITNESS", description="", threshold=0.3, window_checks=2
    )
    snapshots = [_snap(median_fitness=0.5), _snap(median_fitness=0.6)]
    sess = _make_session([rule], snapshots)
    assert mgr._check_rule(sess, rule, snapshots[-1]) is None


def test_oos_degradation_fires():
    mgr = MonitorManager(_FakeEngine())  # type: ignore[arg-type]
    rule = MonitorRule(
        code="OOS_DEGRADATION", description="", threshold=0.5, window_checks=2
    )
    snapshots = [
        _snap(median_fitness=1.0, median_fitness_oos=0.3),
        _snap(median_fitness=1.0, median_fitness_oos=0.2),
    ]
    sess = _make_session([rule], snapshots)
    alert = mgr._check_rule(sess, rule, snapshots[-1])
    assert alert is not None
    assert "overfit" in alert.message.lower()


def test_oos_degradation_does_not_fire_when_holding():
    mgr = MonitorManager(_FakeEngine())  # type: ignore[arg-type]
    rule = MonitorRule(
        code="OOS_DEGRADATION", description="", threshold=0.5, window_checks=2
    )
    snapshots = [
        _snap(median_fitness=1.0, median_fitness_oos=0.7),
        _snap(median_fitness=1.0, median_fitness_oos=0.65),
    ]
    sess = _make_session([rule], snapshots)
    assert mgr._check_rule(sess, rule, snapshots[-1]) is None


def test_project_not_running_detection():
    mgr = MonitorManager(_FakeEngine())  # type: ignore[arg-type]
    rule = MonitorRule(code="PROJECT_NOT_RUNNING", description="", window_checks=0)
    snap = _snap(project_status_lines=["status: idle", "task: Build", "finished"])
    sess = _make_session([rule], [snap])
    alert = mgr._check_rule(sess, rule, snap)
    assert alert is not None
    assert alert.rule_code == "PROJECT_NOT_RUNNING"


def test_project_not_running_skips_when_active():
    mgr = MonitorManager(_FakeEngine())  # type: ignore[arg-type]
    rule = MonitorRule(code="PROJECT_NOT_RUNNING", description="", window_checks=0)
    snap = _snap(project_status_lines=["task: Build running 35%"])
    sess = _make_session([rule], [snap])
    assert mgr._check_rule(sess, rule, snap) is None


# ---- manager API ------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_rejects_too_short_interval():
    mgr = MonitorManager(_FakeEngine())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await mgr.start("X", interval_seconds=1.0)


@pytest.mark.asyncio
async def test_manager_rejects_too_long_interval():
    mgr = MonitorManager(_FakeEngine())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await mgr.start("X", interval_seconds=99999.0)


def test_default_rules_are_well_formed():
    for r in DEFAULT_RULES:
        assert isinstance(r, MonitorRule)
        assert r.code
        assert r.description
        assert r.action in {"alert_only", "pause_project", "stop_project"}
