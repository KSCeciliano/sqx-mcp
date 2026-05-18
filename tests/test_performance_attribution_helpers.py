"""Unit tests for performance_attribution helpers."""

from __future__ import annotations

from sq_mcp.tools.performance_attribution import (
    _attribute_by_month,
    _attribute_by_strategy,
    _attribute_regime,
    _bucket_returns,
    _classify_regime,
    _herfindahl_contribution,
)

# ---- _bucket_returns -------------------------------------------------------


def test_bucket_returns_simple_doubling() -> None:
    # Curve doubles in each half
    curve = [1.0, 2.0, 4.0]
    out = _bucket_returns(curve, 2)
    # First bucket: [1.0, 2.0] → 100%; last bucket [4.0] → 0% (only 1 sample)
    assert len(out) == 2


def test_bucket_returns_n_too_large_returns_empty() -> None:
    curve = [1.0, 2.0]
    out = _bucket_returns(curve, 10)
    assert out == []


def test_bucket_returns_monotonic_growth_all_positive() -> None:
    curve = [float(i + 1) for i in range(100)]
    out = _bucket_returns(curve, 10)
    assert len(out) == 10
    assert all(r > 0 for r in out)


def test_bucket_returns_handles_zero_starting_value() -> None:
    curve = [0.0] * 10 + [1.0] * 10
    out = _bucket_returns(curve, 2)
    # First bucket starts at 0 → fallback 0.0
    assert out[0] == 0.0


# ---- _attribute_by_strategy ------------------------------------------------


def test_attribute_by_strategy_sums_correctly() -> None:
    rows = [
        {"name": "A", "weight": 0.5, "total_return_pct": 10.0},  # 5
        {"name": "B", "weight": 0.5, "total_return_pct": 20.0},  # 10
    ]
    out = _attribute_by_strategy(rows)
    assert out["total_portfolio_return_pct"] == 15.0
    # Share of total
    by_name = {c["name"]: c for c in out["contributions"]}
    assert by_name["A"]["share_of_total_pct"] == round(5.0 / 15.0 * 100, 4)
    assert by_name["B"]["share_of_total_pct"] == round(10.0 / 15.0 * 100, 4)


def test_attribute_by_strategy_sorts_largest_first() -> None:
    rows = [
        {"name": "small", "weight": 0.1, "total_return_pct": 1.0},
        {"name": "big", "weight": 1.0, "total_return_pct": 10.0},
        {"name": "mid", "weight": 0.5, "total_return_pct": 5.0},
    ]
    out = _attribute_by_strategy(rows)
    assert out["contributions"][0]["name"] == "big"
    assert out["contributions"][-1]["name"] == "small"


def test_attribute_by_strategy_zero_total_handles_division() -> None:
    rows = [
        {"name": "A", "weight": 1.0, "total_return_pct": 5.0},
        {"name": "B", "weight": 1.0, "total_return_pct": -5.0},
    ]
    out = _attribute_by_strategy(rows)
    assert out["total_portfolio_return_pct"] == 0.0
    # share_of_total set to 0 when total is 0
    assert all(c["share_of_total_pct"] == 0.0 for c in out["contributions"])


# ---- _attribute_by_month ---------------------------------------------------


def test_attribute_by_month_smoke() -> None:
    # Two synthetic curves
    curve1 = [float(i + 1) for i in range(100)]  # monotonic up
    curve2 = [100.0 - i for i in range(100)]  # monotonic down
    rows = [
        {"name": "up", "equity_curve": curve1, "weight": 1.0},
        {"name": "down", "equity_curve": curve2, "weight": 1.0},
    ]
    out = _attribute_by_month(rows, 10)
    assert out["n_buckets"] == 10
    by_name = {p["name"]: p for p in out["per_strategy"]}
    # All buckets of `up` strategy should be positive
    assert all(r > 0 for r in by_name["up"]["bucket_returns_pct"])
    # All buckets of `down` strategy should be negative
    assert all(r < 0 for r in by_name["down"]["bucket_returns_pct"])


def test_attribute_by_month_identifies_best_bucket() -> None:
    # Curve goes flat then rallies in the last segment
    flat = [100.0] * 50
    rally = [100.0 + i for i in range(50)]
    curve = flat + rally
    rows = [{"name": "rallying", "equity_curve": curve, "weight": 1.0}]
    out = _attribute_by_month(rows, 5)
    # Best bucket should be near the end
    assert out["per_strategy"][0]["best_bucket"] >= 3


# ---- _classify_regime ------------------------------------------------------


def test_classify_regime_basic() -> None:
    bucket_returns = [0.5, 5.0, 1.0, 10.0, 0.1]
    regimes = _classify_regime(bucket_returns, threshold=2.0)
    assert regimes == ["calm", "volatile", "calm", "volatile", "calm"]


def test_classify_regime_negative_returns_volatile() -> None:
    regimes = _classify_regime([-3.0, 1.0, -10.0], threshold=2.0)
    assert regimes == ["volatile", "calm", "volatile"]


# ---- _attribute_regime -----------------------------------------------------


def test_attribute_regime_smoke() -> None:
    # Calm-only curve
    curve = [100.0 + i * 0.01 for i in range(100)]
    rows = [{"name": "low_vol", "equity_curve": curve, "weight": 1.0}]
    out = _attribute_regime(rows, n_buckets=10, threshold=0.5)
    # All buckets should be calm (very gentle slope)
    assert out["n_calm_buckets"] == 10
    assert out["n_volatile_buckets"] == 0


def test_attribute_regime_high_vol_curve() -> None:
    # Spiky curve
    base = 100.0
    curve = []
    for i in range(100):
        curve.append(base + (i % 2) * 50.0)  # alternates 100, 150
    rows = [{"name": "spiky", "equity_curve": curve, "weight": 1.0}]
    out = _attribute_regime(rows, n_buckets=10, threshold=0.001)
    # All buckets should be volatile (large per-bucket changes)
    assert out["n_volatile_buckets"] >= 1


# ---- _herfindahl_contribution ----------------------------------------------


def test_hhi_perfectly_distributed_is_zero() -> None:
    # 10 equal contributions → normalized HHI = 0
    out = _herfindahl_contribution([1.0] * 10)
    assert out["hhi_normalized"] == 0
    assert "well distributed" in out["verdict"]


def test_hhi_single_dominates_is_near_one() -> None:
    out = _herfindahl_contribution([100.0, 0.001, 0.001, 0.001])
    assert out["hhi_normalized"] > 0.9
    assert "concentrated" in out["verdict"]


def test_hhi_zero_total_returns_no_return_verdict() -> None:
    out = _herfindahl_contribution([0.0, 0.0])
    assert "no return" in out["verdict"]


def test_hhi_top_share_in_response() -> None:
    out = _herfindahl_contribution([5.0, 5.0])
    assert out["top_share_pct"] == 50.0
