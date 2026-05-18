"""Unit tests for analytics helpers."""

from __future__ import annotations

from sq_mcp.tools.analytics import (
    _bucket_curve_by_count,
    _combine_curves,
    _monthly_returns_estimate,
    _normalize_weights,
)

# ---- _normalize_weights ---------------------------------------------------


def test_normalize_weights_sums_to_one() -> None:
    out = _normalize_weights([1.0, 2.0, 1.0])
    assert sum(out) == 1.0
    assert out == [0.25, 0.5, 0.25]


def test_normalize_weights_zero_total_falls_back_to_uniform() -> None:
    out = _normalize_weights([0.0, 0.0, 0.0])
    assert out == [1 / 3, 1 / 3, 1 / 3]


def test_normalize_weights_clips_negatives() -> None:
    # Negative weights would invert allocation — clip to 0 and renormalize
    out = _normalize_weights([1.0, -1.0, 2.0])
    assert out == [1 / 3, 0.0, 2 / 3]


# ---- _combine_curves ------------------------------------------------------


def test_combine_curves_uniform_weights() -> None:
    a = [100.0, 110.0, 120.0]
    b = [200.0, 220.0, 240.0]
    out = _combine_curves([a, b], [1.0, 1.0])  # uniform → 0.5 each
    assert out == [150.0, 165.0, 180.0]


def test_combine_curves_truncates_to_shortest() -> None:
    a = [100.0, 110.0, 120.0, 130.0]
    b = [100.0, 110.0]
    out = _combine_curves([a, b], [1.0, 1.0])
    # Truncated to b's length
    assert len(out) == 2
    assert out == [100.0, 110.0]


def test_combine_curves_empty_returns_empty() -> None:
    assert _combine_curves([], []) == []
    assert _combine_curves([[]], [1.0]) == []


def test_combine_curves_weighted() -> None:
    a = [100.0, 200.0]
    b = [0.0, 0.0]
    # 70% A, 30% B → [70, 140]
    out = _combine_curves([a, b], [7.0, 3.0])
    assert out == [70.0, 140.0]


# ---- _bucket_curve_by_count -----------------------------------------------


def test_bucket_curve_by_count_returns_n_samples() -> None:
    curve = list(range(100))
    out = _bucket_curve_by_count(curve, 10)
    assert len(out) == 10
    # First sample is curve[0]; last bucket starts at ~curve[90]
    assert out[0] == 0
    assert out[-1] in (90, 89, 91)  # depends on rounding


def test_bucket_curve_by_count_passthrough_when_n_geq_len() -> None:
    curve = [1.0, 2.0, 3.0]
    out = _bucket_curve_by_count(curve, 100)
    assert out == curve


def test_bucket_curve_by_count_empty_returns_empty() -> None:
    assert _bucket_curve_by_count([], 5) == []
    assert _bucket_curve_by_count([1.0, 2.0], 0) == []


# ---- _monthly_returns_estimate -------------------------------------------


def test_monthly_returns_estimate_uniform_growth() -> None:
    # 13 points across the curve (one for month-start + 12 months)
    curve = [100.0 * (1.10) ** i for i in range(13)]
    rows = _monthly_returns_estimate(curve, months=12)
    assert len(rows) == 12
    # Each step should be ~+10%
    for r in rows:
        assert r["return"] is not None
        assert abs(r["return"] - 0.10) < 0.01


def test_monthly_returns_estimate_handles_zero_prev() -> None:
    curve = [0.0, 100.0, 110.0]
    rows = _monthly_returns_estimate(curve, months=2)
    # When prev==0 the return is None (avoid div-by-zero)
    # Bucket index 0 = 0.0, index 1 = 100.0, index 2 = 110.0
    # First row: prev=0 → return=None ; second row: prev=100 curr=110 → 0.10
    assert rows[0]["return"] is None
    assert rows[1]["return"] == 0.10


def test_monthly_returns_estimate_empty_curve() -> None:
    assert _monthly_returns_estimate([], months=12) == []


# ---- Sharpe / Sortino / Calmar -------------------------------------------

from sq_mcp.tools.analytics import (  # noqa: E402
    _annualized_return_from_curve,
    _calmar_ratio,
    _sharpe_ratio,
    _sortino_ratio,
)


def test_sharpe_handles_too_short() -> None:
    assert _sharpe_ratio([]) is None
    assert _sharpe_ratio([0.05]) is None


def test_sharpe_zero_variance_returns_none() -> None:
    assert _sharpe_ratio([0.01, 0.01, 0.01, 0.01]) is None


def test_sharpe_positive_for_positive_excess_return() -> None:
    # Steady 1%/mo with small variance → positive Sharpe
    returns = [0.01, 0.012, 0.008, 0.011, 0.009, 0.013, 0.01]
    s = _sharpe_ratio(returns, periods_per_year=12, risk_free_rate=0.0)
    assert s is not None
    assert s > 0


def test_sortino_returns_none_when_no_downside() -> None:
    assert _sortino_ratio([0.05, 0.03, 0.07]) is None


def test_sortino_positive_when_mostly_positive_with_one_down() -> None:
    returns = [0.05, 0.03, -0.02, 0.04, 0.06]
    s = _sortino_ratio(returns, periods_per_year=12)
    assert s is not None
    assert s > 0


def test_calmar_handles_none_inputs() -> None:
    assert _calmar_ratio(annualized_return=None, max_drawdown_pct_of_peak=0.1) is None
    assert _calmar_ratio(annualized_return=0.1, max_drawdown_pct_of_peak=None) is None
    # Zero DD → undefined
    assert _calmar_ratio(annualized_return=0.1, max_drawdown_pct_of_peak=0.0) is None


def test_calmar_basic_ratio() -> None:
    out = _calmar_ratio(annualized_return=0.20, max_drawdown_pct_of_peak=0.10)
    assert out == 2.0


def test_annualized_return_compounds_correctly() -> None:
    # 100 → 121 over 2 years should give CAGR of 10%
    out = _annualized_return_from_curve([100.0, 110.0, 121.0], history_years=2.0)
    assert out is not None
    assert abs(out - 0.10) < 0.001


def test_annualized_return_handles_short_curves() -> None:
    assert _annualized_return_from_curve([], history_years=2.0) is None
    assert _annualized_return_from_curve([100.0], history_years=2.0) is None
    assert _annualized_return_from_curve([100.0, 110.0], history_years=0) is None


# ---- _contribution_analysis ---------------------------------------------

from sq_mcp.tools.analytics import _contribution_analysis  # noqa: E402


def test_contribution_analysis_uniform_weights() -> None:
    curves = [
        [100.0, 110.0, 120.0],  # +20% return, max DD 0
        [100.0, 90.0, 100.0],   # 0% return, max DD 10
    ]
    out = _contribution_analysis(curves, [1.0, 1.0], ["a", "b"])
    # Sorted by return contribution desc
    assert out["by_strategy"][0]["label"] == "a"
    # Equal weights → each gets w=0.5
    assert out["by_strategy"][0]["weight"] == 0.5
    # a returns 20% w=0.5 → contribution = 0.10
    assert abs(out["by_strategy"][0]["return_contribution"] - 0.10) < 1e-3
    # b max DD = 10 with weight 0.5 → contribution = 5
    b_row = next(r for r in out["by_strategy"] if r["label"] == "b")
    assert b_row["dd_contribution"] == 5.0


def test_contribution_analysis_handles_empty_curve() -> None:
    curves = [[]]
    out = _contribution_analysis(curves, [1.0], ["empty"])
    row = out["by_strategy"][0]
    assert row["individual_return"] is None
    assert row["return_contribution"] is None


def test_contribution_analysis_handles_zero_baseline() -> None:
    # c[0] == 0 → can't compute return ratio
    curves = [[0.0, 10.0]]
    out = _contribution_analysis(curves, [1.0], ["zero_start"])
    assert out["by_strategy"][0]["individual_return"] is None
