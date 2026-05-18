"""Unit tests for workflows helpers."""

from __future__ import annotations

from sq_mcp.tools.promotion import PromotionInputs
from sq_mcp.tools.workflows import (
    CompareTwoArgs,
    _compare_two_strategies,
    _promote_strategy,
)


def _good_promotion_inputs() -> PromotionInputs:
    return PromotionInputs(
        trades=500,
        oos_is_ratio=0.6,
        drawdown_pct=8.0,
        profit_factor=1.5,
        brittle_verdict="robust",
        drift_verdict="green",
        stress_verdict="robust",
        risk_profile="moderate",
        days_since_build=30.0,
        audit_critical_count=0,
    )


# ---- _promote_strategy -----------------------------------------------------


def test_promote_strategy_approved() -> None:
    state = {"version": 1, "data": {}}
    manifest = _promote_strategy(
        state,
        strategy_name="S",
        strategy_key="s.sqx",
        promotion_inputs=_good_promotion_inputs(),
        tags_if_approved=["prod"],
        parent_lineage_id=None,
        dry_run=False,
    )
    assert manifest["verdict"] == "shipped"


def test_promote_strategy_dry_run_skips_persist() -> None:
    state = {"version": 1, "data": {}}
    manifest = _promote_strategy(
        state,
        strategy_name="S",
        strategy_key="s.sqx",
        promotion_inputs=_good_promotion_inputs(),
        tags_if_approved=["prod"],
        parent_lineage_id=None,
        dry_run=True,
    )
    assert manifest["verdict"] == "approved_dry_run"
    assert state["data"] == {}  # No mutation


# ---- _compare_two_strategies -----------------------------------------------


def test_compare_two_strategy_a_clearly_better() -> None:
    # A has higher mean PnL with realistic variance in differences
    a = [5.0 + ((-1) ** i) * 0.5 for i in range(30)]
    b = [1.0 + ((-1) ** (i + 1)) * 0.3 for i in range(30)]
    out = _compare_two_strategies(CompareTwoArgs(name_a="A", name_b="B", trades_a=a, trades_b=b))
    assert "A > B" in out["summary"]


def test_compare_two_with_returns_includes_alpha_beta() -> None:
    a = [1.0] * 20
    b = [0.5] * 20
    returns_a = [0.02] * 20
    returns_b = [0.01] * 20
    out = _compare_two_strategies(
        CompareTwoArgs(
            name_a="A",
            name_b="B",
            trades_a=a,
            trades_b=b,
            returns_a=returns_a,
            returns_b=returns_b,
        )
    )
    assert out["alpha_beta"] is not None
    assert out["outperformance"] is not None


def test_compare_two_no_significant_difference() -> None:
    # Same trades → no difference
    a = [1.0, -1.0] * 10
    b = [1.0, -1.0] * 10
    out = _compare_two_strategies(
        CompareTwoArgs(name_a="A", name_b="B", trades_a=a, trades_b=b)
    )
    assert "no significant" in out["summary"]


def test_compare_two_includes_brittle_and_stress() -> None:
    a = [1.0] * 30
    b = [1.0] * 30
    out = _compare_two_strategies(
        CompareTwoArgs(name_a="A", name_b="B", trades_a=a, trades_b=b)
    )
    assert "brittle_a" in out
    assert "stress_a" in out
    assert "brittle_b" in out
