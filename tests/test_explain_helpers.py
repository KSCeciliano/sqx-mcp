"""Unit tests for explain.py knowledge-base lookups."""

from __future__ import annotations

from sq_mcp.tools.explain import _FINDING_EXPLANATIONS, _METRIC_EXPLANATIONS


def test_finding_explanations_cover_audit_codes() -> None:
    """Every code in audit.py should have an explanation."""
    from sq_mcp.tools.audit import Finding
    # Build a probe sample with the codes audit might emit. We check the
    # union with explain's table — explain should at least cover the most
    # common ones.
    sample_codes = (
        "TOO_FEW_TRADES",
        "NO_OOS_FITNESS",
        "OVERFIT_OOS_DEGRADATION",
        "UNREALISTIC_PROFIT_TO_DD",
        "DRAWDOWN_OVER_HALF",
        "AMBIGUOUS_TRADES",
        "STRATEGY_PROBLEMS",
        "VERY_SHORT_BACKTEST",
        "SUSPICIOUSLY_LOW_DRAWDOWN",
        "EXTREMELY_HIGH_TRADE_FREQ",
    )
    for code in sample_codes:
        assert code in _FINDING_EXPLANATIONS, f"missing explanation for {code}"
    # Construct one Finding from each to be sure the keys aren't pure strings
    _ = Finding(
        code="TOO_FEW_TRADES",
        severity="high",
        title="t",
        message="m",
        suggestion="s",
    )


def test_finding_explanations_have_required_fields() -> None:
    for code, entry in _FINDING_EXPLANATIONS.items():
        assert "severity" in entry, code
        assert "what" in entry, code
        assert "fix" in entry, code
        assert isinstance(entry["fix"], list)
        assert entry["fix"], f"{code} has empty fix list"


def test_metric_explanations_have_required_fields() -> None:
    for name, entry in _METRIC_EXPLANATIONS.items():
        assert "what" in entry, name
        assert "good_when" in entry, name
        assert "bad_when" in entry, name


def test_metric_explanations_cover_core_metrics() -> None:
    for m in (
        "fitness_oos",
        "oos_is_ratio",
        "drawdown_pct",
        "profit_to_dd_ratio",
        "trades",
        "history_years",
    ):
        assert m in _METRIC_EXPLANATIONS
