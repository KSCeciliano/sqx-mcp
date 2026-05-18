"""Unit tests for wf_matrix helpers."""

from __future__ import annotations

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.wf_matrix import (
    _anchored_vs_rolling,
    _wf_efficiency,
    _wf_matrix_summary,
    _wf_recommendation,
)


def test_efficiency_excellent_when_oos_matches_is() -> None:
    is_m = [1.0, 1.0, 1.0]
    oos_m = [0.9, 0.95, 0.85]  # OOS ~90% of IS
    r = _wf_efficiency(is_m, oos_m)
    assert r["efficiency"] >= 0.8
    assert r["verdict"] == "excellent"


def test_efficiency_overfit_when_oos_collapses() -> None:
    is_m = [2.0, 2.0, 2.0]
    oos_m = [0.5, 0.5, 0.5]  # OOS only 25% of IS
    r = _wf_efficiency(is_m, oos_m)
    assert r["efficiency"] < 0.4
    assert r["verdict"] == "overfit"


def test_efficiency_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _wf_efficiency([1.0, float("nan")], [0.5, 0.5])


def test_matrix_summary_sorted_by_efficiency() -> None:
    entries = [
        {"is_period_bars": 1000, "oos_period_bars": 200, "is_sharpe": 2.0, "oos_sharpe": 0.5, "n_folds": 5},
        {"is_period_bars": 2000, "oos_period_bars": 400, "is_sharpe": 1.5, "oos_sharpe": 1.3, "n_folds": 5},
        {"is_period_bars": 500, "oos_period_bars": 100, "is_sharpe": 3.0, "oos_sharpe": 0.3, "n_folds": 5},
    ]
    r = _wf_matrix_summary(entries)
    assert r["n_combinations"] == 3
    # Best is the 2nd entry (efficiency = 0.867)
    assert r["best_combo"]["is_bars"] == 2000
    assert "markdown" in r and "Efficiency" in r["markdown"]


def test_anchored_vs_rolling_anchored_wins() -> None:
    anchored = [1.0, 1.1, 1.2]
    rolling = [0.5, 0.4, 0.3]
    r = _anchored_vs_rolling(anchored, rolling)
    assert r["verdict"] == "anchored_wins"


def test_anchored_vs_rolling_comparable() -> None:
    anchored = [1.0, 1.0, 1.0]
    rolling = [1.0, 1.0, 1.0]
    r = _anchored_vs_rolling(anchored, rolling)
    assert r["verdict"] == "comparable"


def test_recommendation_picks_best_meeting_criteria() -> None:
    entries = [
        {"is_period_bars": 1000, "oos_period_bars": 50, "is_sharpe": 1.0, "oos_sharpe": 0.9, "n_folds": 5},
        {"is_period_bars": 2000, "oos_period_bars": 200, "is_sharpe": 1.0, "oos_sharpe": 0.6, "n_folds": 5},
        {"is_period_bars": 2000, "oos_period_bars": 400, "is_sharpe": 1.0, "oos_sharpe": 0.7, "n_folds": 5},
    ]
    r = _wf_recommendation(entries, min_eff=0.5, min_oos=100)
    # First entry rejected (oos<100), others qualify. Best score is the one with longest OOS.
    assert r["recommendation"] is not None
    assert r["recommendation"]["oos_bars"] == 400


def test_recommendation_no_candidates() -> None:
    entries = [
        {"is_period_bars": 1000, "oos_period_bars": 50, "is_sharpe": 1.0, "oos_sharpe": 0.1, "n_folds": 5},
    ]
    r = _wf_recommendation(entries, min_eff=0.5, min_oos=100)
    assert r["recommendation"] is None
    assert r["n_candidates"] == 0
