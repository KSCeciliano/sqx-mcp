"""Unit tests for promotion helpers."""

from __future__ import annotations

from sq_mcp.tools.promotion import (
    PROFILE_DD_CAPS,
    PromotionInputs,
    _evaluate,
    _explain_failure,
    _required_gates,
)

# ---- _evaluate -------------------------------------------------------------


def test_evaluate_clean_strategy_approved() -> None:
    out = _evaluate(
        PromotionInputs(
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
    )
    assert out["approved"] is True
    assert out["verdict"] == "approved"
    assert out["blocked_by"] == []


def test_evaluate_blocks_on_low_trades() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=50,
            oos_is_ratio=0.6,
            drawdown_pct=8.0,
            risk_profile="moderate",
        )
    )
    assert out["approved"] is False
    assert "min_trades" in out["blocked_by"]


def test_evaluate_blocks_on_high_drawdown_for_conservative() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=500,
            oos_is_ratio=0.6,
            drawdown_pct=8.0,  # > conservative cap of 5
            risk_profile="conservative",
        )
    )
    assert out["approved"] is False
    assert "drawdown_cap" in out["blocked_by"]


def test_evaluate_passes_same_drawdown_for_aggressive() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=500,
            oos_is_ratio=0.6,
            drawdown_pct=15.0,  # below aggressive cap of 20
            risk_profile="aggressive",
        )
    )
    assert "drawdown_cap" not in out["blocked_by"]


def test_evaluate_blocks_on_low_oos_ratio() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=500,
            oos_is_ratio=0.2,  # below moderate min of 0.4
            drawdown_pct=8.0,
            risk_profile="moderate",
        )
    )
    assert "oos_evidence" in out["blocked_by"]


def test_evaluate_brittle_blocks() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=500,
            oos_is_ratio=0.6,
            drawdown_pct=8.0,
            brittle_verdict="very_brittle",
            risk_profile="moderate",
        )
    )
    assert "brittleness" in out["blocked_by"]


def test_evaluate_stress_fragile_blocks() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=500,
            oos_is_ratio=0.6,
            drawdown_pct=8.0,
            stress_verdict="fragile",
            risk_profile="moderate",
        )
    )
    assert "stress" in out["blocked_by"]


def test_evaluate_drift_red_blocks() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=500,
            oos_is_ratio=0.6,
            drawdown_pct=8.0,
            drift_verdict="red",
            risk_profile="moderate",
        )
    )
    assert "drift" in out["blocked_by"]


def test_evaluate_drift_insufficient_sample_does_not_block() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=500,
            oos_is_ratio=0.6,
            drawdown_pct=8.0,
            drift_verdict="insufficient_sample",
            risk_profile="moderate",
        )
    )
    # Drift gate is not even added when sample is insufficient
    assert "drift" not in out["blocked_by"]


def test_evaluate_critical_audit_blocks() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=500,
            oos_is_ratio=0.6,
            drawdown_pct=8.0,
            audit_critical_count=1,
            risk_profile="moderate",
        )
    )
    assert "audit_clean" in out["blocked_by"]


def test_evaluate_old_build_blocks() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=500,
            oos_is_ratio=0.6,
            drawdown_pct=8.0,
            days_since_build=500.0,
            risk_profile="moderate",
        )
    )
    assert "freshness" in out["blocked_by"]


def test_evaluate_low_pf_is_soft_warning_not_block() -> None:
    out = _evaluate(
        PromotionInputs(
            trades=500,
            oos_is_ratio=0.6,
            drawdown_pct=8.0,
            profit_factor=1.0,  # below soft threshold
            risk_profile="moderate",
        )
    )
    # PF is a soft gate — doesn't block
    assert "profit_factor" in out["warnings"]
    assert "profit_factor" not in out["blocked_by"]


def test_evaluate_profile_caps_increasing() -> None:
    # Sanity check on caps ordering
    assert PROFILE_DD_CAPS["ultra_conservative"] < PROFILE_DD_CAPS["aggressive"]


# ---- _required_gates -----------------------------------------------------


def test_required_gates_includes_all_expected() -> None:
    rg = _required_gates()
    names = {g["name"] for g in rg}
    required = {
        "min_trades",
        "oos_evidence",
        "drawdown_cap",
        "brittleness",
        "stress",
        "drift",
        "freshness",
        "audit_clean",
    }
    assert required.issubset(names)


# ---- _explain_failure ----------------------------------------------------


def test_explain_failure_lists_blocks() -> None:
    result = {
        "gates": [
            {"name": "min_trades", "passed": False, "required": True, "reason": "need 100"},
            {"name": "audit_clean", "passed": True, "required": True, "reason": "ok"},
        ]
    }
    out = _explain_failure(result)
    assert "min_trades" in out["explanation"]
    assert "audit_clean" not in out["explanation"]


def test_explain_failure_no_failures() -> None:
    result = {
        "gates": [{"name": "x", "passed": True, "required": True, "reason": "ok"}]
    }
    out = _explain_failure(result)
    assert "no gate failures" in out["explanation"]


def test_explain_failure_invalid_payload() -> None:
    out = _explain_failure("not a dict")
    assert "unrecognised" in out["explanation"]
