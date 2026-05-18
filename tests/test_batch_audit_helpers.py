"""Unit tests for batch_audit helpers."""

from __future__ import annotations

from sq_mcp.tools.batch_audit import _audit_one, _batch_audit, _summary_markdown


def _good_row(rel: str = "good.sqx") -> dict:
    return {
        "rel": rel,
        "trades": 500,
        "fitness_oos": 1.5,
        "oos_is_ratio": 0.6,
        "drawdown_pct": 8.0,
        "profit_factor": 1.5,
    }


def _bad_row(rel: str = "bad.sqx") -> dict:
    return {
        "rel": rel,
        "trades": 50,
        "fitness_oos": 0.5,
        "oos_is_ratio": 0.2,
        "drawdown_pct": 30.0,
        "profit_factor": 0.9,
    }


# ---- _audit_one ------------------------------------------------------------


def test_audit_one_ready() -> None:
    out = _audit_one(_good_row(), "moderate")
    assert out["verdict"] == "ready"
    assert out["promotion_approved"] is True


def test_audit_one_blocked() -> None:
    out = _audit_one(_bad_row(), "moderate")
    assert out["verdict"] == "blocked"
    assert out["promotion_approved"] is False
    assert len(out["blocked_by"]) > 0


def test_audit_one_handles_missing_metrics() -> None:
    out = _audit_one({"rel": "x.sqx"}, "moderate")
    # Should still produce a verdict (blocked, since trades=0)
    assert out["verdict"] == "blocked"


# ---- _batch_audit ----------------------------------------------------------


def test_batch_audit_mix() -> None:
    rows = [_good_row("g1.sqx"), _good_row("g2.sqx"), _bad_row("b1.sqx")]
    out = _batch_audit(rows, "moderate", max_strategies=100)
    assert out["n_evaluated"] == 3
    assert out["n_ready"] == 2
    assert out["n_blocked"] == 1


def test_batch_audit_respects_max_strategies() -> None:
    rows = [_good_row(f"g{i}.sqx") for i in range(20)]
    out = _batch_audit(rows, "moderate", max_strategies=5)
    assert out["n_evaluated"] == 5


def test_batch_audit_profile_affects_caps() -> None:
    # Strategy with 15% drawdown — fits aggressive, blocked by conservative
    row = {
        "rel": "x.sqx",
        "trades": 500,
        "fitness_oos": 1.5,
        "oos_is_ratio": 0.6,
        "drawdown_pct": 15.0,
        "profit_factor": 1.5,
    }
    aggressive = _batch_audit([row], "aggressive", max_strategies=100)
    conservative = _batch_audit([row], "conservative", max_strategies=100)
    assert aggressive["n_ready"] == 1
    assert conservative["n_blocked"] == 1


# ---- _summary_markdown -----------------------------------------------------


def test_summary_markdown_includes_counts() -> None:
    queue = {
        "n_evaluated": 5,
        "n_ready": 2,
        "n_marginal": 1,
        "n_blocked": 2,
        "n_unparseable": 0,
        "risk_profile": "moderate",
        "ready": [{"rel": "good1.sqx", "metrics": {"trades": 500}, "blocked_by": []}],
        "marginal": [],
        "blocked": [],
    }
    out = _summary_markdown(queue, "Test")
    assert "Test" in out
    assert "Ready: **2**" in out
    assert "good1.sqx" in out


def test_summary_markdown_handles_empty_queue() -> None:
    queue = {"n_evaluated": 0, "n_ready": 0, "n_marginal": 0, "n_blocked": 0, "n_unparseable": 0}
    out = _summary_markdown(queue, "Empty")
    # No section headers when no rows
    assert "Ready to promote" not in out
    assert "Empty" in out
