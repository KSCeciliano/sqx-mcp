"""Unit tests for data_health helpers."""

from __future__ import annotations

from sq_mcp.tools.data_health import (
    _bar_count_check,
    _freshness_gate,
    _gap_report,
    _monotonicity,
)


def test_gap_report_no_gaps() -> None:
    ts = list(range(0, 1000, 60))  # 1m bars
    result = _gap_report(ts, 60)
    assert result["n_gaps"] == 0
    assert result["total_missing_bars"] == 0


def test_gap_report_detects_missing() -> None:
    # 10 minutes of M1 bars, then a 30-min gap, then 5 more
    ts = list(range(0, 600, 60))
    ts += list(range(2400, 2700, 60))
    result = _gap_report(ts, 60)
    assert result["n_gaps"] >= 1
    assert result["total_missing_bars"] > 20


def test_gap_report_within_threshold_no_gap() -> None:
    # 60s expected, 90s seen — should NOT count (1.5× cap)
    ts = [0, 90, 180]
    result = _gap_report(ts, 60)
    assert result["n_gaps"] == 0


def test_freshness_gate_fresh() -> None:
    result = _freshness_gate(1000, 1100, max_age_minutes=10)
    assert result["passed"] is True
    assert result["verdict"] == "fresh"


def test_freshness_gate_stale() -> None:
    result = _freshness_gate(0, 100_000, max_age_minutes=10)
    assert result["passed"] is False
    assert result["verdict"] == "stale"


def test_monotonicity_clean_series() -> None:
    result = _monotonicity([1, 2, 3, 4, 5])
    assert result["monotonic"] is True
    assert result["n_anomalies"] == 0


def test_monotonicity_duplicate() -> None:
    result = _monotonicity([1, 2, 2, 3])
    assert result["monotonic"] is False
    assert result["n_anomalies"] == 1


def test_monotonicity_out_of_order() -> None:
    result = _monotonicity([1, 3, 2, 4])
    assert result["monotonic"] is False


def test_bar_count_check_full_coverage() -> None:
    # 1 day, M1 bars, market_hours=1.0 — expect 1440 bars
    result = _bar_count_check(0, 86400, 60, 1440, 1.0)
    assert result["coverage_pct"] >= 99.0
    assert result["verdict"] == "complete"


def test_bar_count_check_sparse() -> None:
    result = _bar_count_check(0, 86400, 60, 100, 1.0)
    assert result["coverage_pct"] < 80
    assert result["verdict"] == "sparse"


def test_bar_count_check_market_hours_partial() -> None:
    # Forex: 24/5 = 71.4% of total time
    result = _bar_count_check(0, 86400, 60, 1028, 0.714)
    assert result["coverage_pct"] >= 95.0
