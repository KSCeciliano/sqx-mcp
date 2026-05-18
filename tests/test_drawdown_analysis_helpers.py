"""Unit tests for drawdown_analysis helpers."""

from __future__ import annotations

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.drawdown_analysis import (
    _drawdown_periods,
    _pain_decomposition,
    _recovery_distribution,
    _time_underwater,
)


def test_drawdown_periods_no_drawdown_for_increasing() -> None:
    curve = [100.0 + i for i in range(50)]
    r = _drawdown_periods(curve)
    assert r["n"] == 0


def test_drawdown_periods_detects_simple_dip() -> None:
    # Up to 110, down to 95, back to 115
    curve = ([100.0 + i for i in range(11)]  # 100..110
             + [110.0 - 1.5 * i for i in range(11)]  # 110..95
             + [95.0 + 2.0 * i for i in range(11)])  # 95..115
    r = _drawdown_periods(curve)
    assert r["n"] == 1
    p = r["periods"][0]
    assert p["depth_pct"] > 10.0  # ~13.6%
    assert p["recovery_index"] is not None


def test_drawdown_periods_ongoing_drawdown_not_recovered() -> None:
    # Up to 100, then strictly decreasing — never recovered
    curve = [100.0 + i for i in range(20)] + [120.0 - i for i in range(20)]
    r = _drawdown_periods(curve)
    assert r["n"] == 1
    assert r["periods"][0]["recovery_index"] is None


def test_drawdown_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _drawdown_periods([100.0, float("nan"), 95.0] + [100.0] * 10)


def test_time_underwater_zero_for_strictly_rising() -> None:
    curve = [100.0 + i for i in range(50)]
    r = _time_underwater(curve)
    assert r["n_underwater_bars"] == 0
    assert r["interpretation"] == "rarely_underwater"


def test_time_underwater_high_for_choppy() -> None:
    # Peak early, then oscillate below
    curve = [100.0, 110.0] + [100.0 + (i % 10) for i in range(100)]
    r = _time_underwater(curve)
    assert r["underwater_share_pct"] > 50.0


def test_recovery_distribution_handles_no_periods() -> None:
    r = _recovery_distribution([100.0 + i for i in range(50)])
    assert r["n"] == 0


def test_recovery_distribution_reports_recovered_periods() -> None:
    curve = ([100.0 + i for i in range(11)]
             + [110.0 - 1.5 * i for i in range(11)]
             + [95.0 + 2.0 * i for i in range(11)])
    r = _recovery_distribution(curve)
    assert r["n_recovered"] == 1
    assert r["mean_recovery_bars"] > 0


def test_pain_decomposition_zero_for_no_drawdown() -> None:
    r = _pain_decomposition([100.0 + i for i in range(50)], [5.0, 10.0, 20.0])
    assert r["total_pain"] == 0.0


def test_pain_decomposition_assigns_to_correct_bucket() -> None:
    # Drawdown reaches ~13% — should populate the ">=10%" bucket
    curve = ([100.0 + i for i in range(11)]
             + [110.0 - 1.5 * i for i in range(11)]
             + [95.0 + 2.0 * i for i in range(11)])
    r = _pain_decomposition(curve, [5.0, 10.0, 20.0])
    assert r["pain_by_bucket"][">=10.0%"] > 0
