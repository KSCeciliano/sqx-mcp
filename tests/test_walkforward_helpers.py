"""Unit tests for walkforward helpers."""

from __future__ import annotations

from sq_mcp.tools.walkforward import (
    _aggregate_folds,
    _consistency_score,
    _databank_to_folds,
    _mean,
    _overfit_flags,
    _std,
)

# ---- _mean / _std ---------------------------------------------------------


def test_mean_basic() -> None:
    assert _mean([1.0, 2.0, 3.0]) == 2.0


def test_mean_empty() -> None:
    assert _mean([]) == 0.0


def test_std_sample_two_points() -> None:
    # std of [1, 3] with sample formula = sqrt(2)
    s = _std([1.0, 3.0])
    assert abs(s - 1.4142135623730951) < 1e-6


def test_std_single_value_returns_zero() -> None:
    assert _std([1.0]) == 0.0


# ---- _aggregate_folds ------------------------------------------------------


def test_aggregate_folds_basic() -> None:
    folds = [
        {"is_metric": 10.0, "oos_metric": 8.0},
        {"is_metric": 12.0, "oos_metric": 6.0},
        {"is_metric": 14.0, "oos_metric": 10.0},
    ]
    out = _aggregate_folds(folds)
    assert out["n_folds"] == 3
    assert out["n_with_oos"] == 3
    assert out["is_mean"] == 12.0
    assert out["oos_mean"] == 8.0
    assert out["walk_forward_efficiency"] == round(8.0 / 12.0, 4)


def test_aggregate_folds_some_missing_oos() -> None:
    folds = [
        {"is_metric": 10.0, "oos_metric": 5.0},
        {"is_metric": 10.0, "oos_metric": None},
    ]
    out = _aggregate_folds(folds)
    assert out["n_folds"] == 2
    assert out["n_with_oos"] == 1
    assert out["oos_mean"] == 5.0


def test_aggregate_folds_efficiency_none_if_zero_is() -> None:
    folds = [{"is_metric": 0.0, "oos_metric": 5.0}]
    out = _aggregate_folds(folds)
    assert out["walk_forward_efficiency"] is None


# ---- _consistency_score ----------------------------------------------------


def test_consistency_score_no_positive_oos() -> None:
    folds = [
        {"is_metric": 5.0, "oos_metric": -1.0},
        {"is_metric": 5.0, "oos_metric": -2.0},
    ]
    out = _consistency_score(folds)
    assert out["score"] == 0


def test_consistency_score_perfect_consistency() -> None:
    # Identical, positive folds → low variance, good IS/OOS match
    folds = [
        {"is_metric": 10.0, "oos_metric": 10.0},
        {"is_metric": 10.0, "oos_metric": 10.0},
        {"is_metric": 10.0, "oos_metric": 10.0},
        {"is_metric": 10.0, "oos_metric": 10.0},
    ]
    out = _consistency_score(folds)
    # All sub-scores should max out
    assert out["score"] >= 95
    assert out["verdict"] == "robust"


def test_consistency_score_partial_credit() -> None:
    # Half OOS positive, OOS << IS → borderline/weak
    folds = [
        {"is_metric": 100.0, "oos_metric": 10.0},
        {"is_metric": 100.0, "oos_metric": -10.0},
        {"is_metric": 100.0, "oos_metric": 20.0},
        {"is_metric": 100.0, "oos_metric": -5.0},
    ]
    out = _consistency_score(folds)
    assert 0 <= out["score"] <= 100
    assert out["verdict"] in {"weak", "borderline"}


# ---- _overfit_flags --------------------------------------------------------


def test_overfit_flags_skips_safe_folds() -> None:
    folds = [
        {"fold_id": "a", "is_metric": 10.0, "oos_metric": 8.0},  # ratio 0.8, safe
        {"fold_id": "b", "is_metric": 10.0, "oos_metric": 2.0},  # ratio 0.2, flagged
        {"fold_id": "c", "is_metric": 10.0, "oos_metric": 6.0},  # ratio 0.6, safe (>0.5)
    ]
    flagged = _overfit_flags(folds, floor_ratio=0.5)
    assert len(flagged) == 1
    assert flagged[0]["fold_id"] == "b"
    assert flagged[0]["oos_is_ratio"] == 0.2


def test_overfit_flags_sorts_worst_first() -> None:
    folds = [
        {"fold_id": "a", "is_metric": 10.0, "oos_metric": 1.0},  # 0.1
        {"fold_id": "b", "is_metric": 10.0, "oos_metric": 3.0},  # 0.3
        {"fold_id": "c", "is_metric": 10.0, "oos_metric": 0.5},  # 0.05
    ]
    flagged = _overfit_flags(folds, floor_ratio=0.5)
    assert [f["fold_id"] for f in flagged] == ["c", "a", "b"]


def test_overfit_flags_skips_zero_is() -> None:
    folds = [
        {"fold_id": "a", "is_metric": 0.0, "oos_metric": 5.0},
        {"fold_id": "b", "is_metric": 10.0, "oos_metric": 1.0},
    ]
    flagged = _overfit_flags(folds, floor_ratio=0.5)
    assert len(flagged) == 1
    assert flagged[0]["fold_id"] == "b"


def test_overfit_flags_skips_missing_oos() -> None:
    folds = [
        {"fold_id": "a", "is_metric": 10.0, "oos_metric": None},
        {"fold_id": "b", "is_metric": 10.0, "oos_metric": 1.0},
    ]
    flagged = _overfit_flags(folds, floor_ratio=0.5)
    assert len(flagged) == 1


# ---- _databank_to_folds ---------------------------------------------------


def test_databank_to_folds_full_oos_split() -> None:
    rows = [
        {"rel": "f1.sqx", "fitness_is": 10.0, "fitness_oos": 8.0},
        {"rel": "f2.sqx", "fitness_is": 12.0, "fitness_oos": 6.0},
    ]
    folds = _databank_to_folds(rows, "fitness")
    assert len(folds) == 2
    assert folds[0]["is_metric"] == 10.0
    assert folds[0]["oos_metric"] == 8.0


def test_databank_to_folds_falls_back_to_flat_metric() -> None:
    # Bare "fitness" key, no _is variant
    rows = [
        {"rel": "f1.sqx", "fitness": 7.0, "fitness_oos": 5.0},
    ]
    folds = _databank_to_folds(rows, "fitness")
    assert len(folds) == 1
    assert folds[0]["is_metric"] == 7.0


def test_databank_to_folds_skips_rows_with_no_metric() -> None:
    rows = [
        {"rel": "x.sqx"},
        {"rel": "y.sqx", "fitness_is": 5.0},
    ]
    folds = _databank_to_folds(rows, "fitness")
    assert len(folds) == 1
    assert folds[0]["fold_id"] == "y.sqx"
