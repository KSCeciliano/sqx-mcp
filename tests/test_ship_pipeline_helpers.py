"""Unit tests for ship_pipeline helpers."""

from __future__ import annotations

from sq_mcp.tools.promotion import PromotionInputs
from sq_mcp.tools.ship_pipeline import _run_pipeline, _status_summary


def _state() -> dict:
    return {"version": 1, "data": {}}


def _good_inputs() -> PromotionInputs:
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


def _bad_inputs() -> PromotionInputs:
    return PromotionInputs(
        trades=50,  # fails min_trades
        oos_is_ratio=0.6,
        drawdown_pct=8.0,
        risk_profile="moderate",
    )


# ---- _run_pipeline (dry run + approved) ----------------------------------


def test_pipeline_approved_dry_run() -> None:
    state = _state()
    manifest = _run_pipeline(
        state,
        strategy_name="S1",
        strategy_key="s1.sqx",
        promotion_inputs=_good_inputs(),
        tags_if_approved=["prod"],
        parent_lineage_id=None,
        dry_run=True,
    )
    assert manifest["verdict"] == "approved_dry_run"
    # Tags / lineage should be skipped
    assert manifest["steps"]["tagging"]["skipped"] is True
    assert manifest["steps"]["lineage"]["skipped"] is True
    # State should NOT be mutated
    assert "tags" not in state["data"]
    assert "lineage" not in state["data"]


def test_pipeline_approved_real_run() -> None:
    state = _state()
    manifest = _run_pipeline(
        state,
        strategy_name="S1",
        strategy_key="s1.sqx",
        promotion_inputs=_good_inputs(),
        tags_if_approved=["prod", "btc"],
        parent_lineage_id=None,
        dry_run=False,
    )
    assert manifest["verdict"] == "shipped"
    # Tags persisted
    assert "tags" in state["data"]
    assert sorted(state["data"]["tags"]["s1.sqx"]) == ["btc", "prod"]
    # Lineage persisted
    assert "lineage" in state["data"]
    assert "s1.sqx" in state["data"]["lineage"]


def test_pipeline_blocked_skips_persist() -> None:
    state = _state()
    manifest = _run_pipeline(
        state,
        strategy_name="bad",
        strategy_key="bad.sqx",
        promotion_inputs=_bad_inputs(),
        tags_if_approved=["prod"],
        parent_lineage_id=None,
        dry_run=False,
    )
    assert manifest["verdict"] == "blocked"
    # Nothing persisted
    assert "tags" not in state["data"]
    assert "lineage" not in state["data"]


def test_pipeline_lineage_with_parent() -> None:
    state = _state()
    # Pre-register a parent
    state["data"]["lineage"] = {
        "parent.sqx": {
            "node_id": "parent.sqx",
            "parent_id": None,
            "label": "parent",
            "metadata": {},
        }
    }
    manifest = _run_pipeline(
        state,
        strategy_name="Child",
        strategy_key="child.sqx",
        promotion_inputs=_good_inputs(),
        tags_if_approved=[],
        parent_lineage_id="parent.sqx",
        dry_run=False,
    )
    assert manifest["verdict"] == "shipped"
    assert state["data"]["lineage"]["child.sqx"]["parent_id"] == "parent.sqx"


def test_pipeline_alerts_evaluated() -> None:
    state = _state()
    manifest = _run_pipeline(
        state,
        strategy_name="S1",
        strategy_key="s1.sqx",
        promotion_inputs=_good_inputs(),
        tags_if_approved=[],
        parent_lineage_id=None,
        dry_run=True,
    )
    # Alerts should have been evaluated regardless of approval
    assert "alerts" in manifest["steps"]
    assert "n_triggered" in manifest["steps"]["alerts"]
    assert "summary_markdown" in manifest["steps"]["alerts"]


# ---- _status_summary ----------------------------------------------------


def test_status_summary_shipped() -> None:
    manifest = {
        "strategy_name": "S",
        "verdict": "shipped",
        "steps": {
            "promotion": {"blocked_by": []},
            "alerts": {"n_triggered": 0},
        },
    }
    out = _status_summary(manifest)
    assert "shipped" in out
    assert "none" in out


def test_status_summary_blocked_with_reasons() -> None:
    manifest = {
        "strategy_name": "S",
        "verdict": "blocked",
        "steps": {
            "promotion": {"blocked_by": ["min_trades", "audit_clean"]},
            "alerts": {"n_triggered": 3},
        },
    }
    out = _status_summary(manifest)
    assert "blocked" in out
    assert "min_trades" in out
    assert "audit_clean" in out


def test_status_summary_invalid_input() -> None:
    out = _status_summary("not a dict")
    assert "unrecognised" in out
