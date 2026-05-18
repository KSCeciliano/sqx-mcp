"""Unit tests for strategy_inspect helpers."""

from __future__ import annotations

from sq_mcp.tools.strategy_inspect import (
    _equity_drawdown_stats,
    _hit_ratio,
    _side_by_side,
    _summarize_text,
)

# ---- _equity_drawdown_stats -----------------------------------------------


def test_drawdown_stats_handles_empty() -> None:
    s = _equity_drawdown_stats([])
    assert s["samples"] == 0
    assert s["max_drawdown_abs"] is None
    assert s["ends_at_new_high"] is None


def test_drawdown_stats_handles_single_value() -> None:
    s = _equity_drawdown_stats([100.0])
    assert s["samples"] == 1
    # Still nothing to compute
    assert s["max_drawdown_abs"] is None


def test_drawdown_stats_no_drawdown_monotonic() -> None:
    s = _equity_drawdown_stats([100.0, 105.0, 110.0, 120.0])
    assert s["max_drawdown_abs"] == 0
    assert s["max_drawdown_pct_of_peak"] == 0
    assert s["longest_underwater"] == 0
    assert s["ends_at_new_high"] is True


def test_drawdown_stats_with_dip_then_recovery() -> None:
    # Peak 110 → drop to 90 → recover to 130
    # Max abs DD = 20, max pct = 20/110 ≈ 0.1818
    s = _equity_drawdown_stats([100.0, 110.0, 100.0, 95.0, 90.0, 130.0])
    assert s["max_drawdown_abs"] == 20
    assert abs(s["max_drawdown_pct_of_peak"] - 20.0 / 110.0) < 1e-6
    # 110→100→95→90 = 3 underwater samples
    assert s["longest_underwater"] == 3
    # Recovers to 130, which is the all-time high
    assert s["ends_at_new_high"] is True
    # Recovery factor = final 130 / max DD 20 = 6.5
    assert s["recovery_factor"] == 6.5


def test_drawdown_stats_with_open_drawdown() -> None:
    s = _equity_drawdown_stats([100.0, 110.0, 100.0, 90.0])
    # Underwater all the way to the end
    assert s["longest_underwater"] == 2
    assert s["ends_at_new_high"] is False


# ---- _hit_ratio -----------------------------------------------------------


def test_hit_ratio_all_positive() -> None:
    # [100, 105, 110, 120] → returns [+, +, +] → hit ratio = 1.0
    assert _hit_ratio([100.0, 105.0, 110.0, 120.0]) == 1.0


def test_hit_ratio_mixed() -> None:
    # [100, 110, 100, 110] → returns [+, -, +] → hit ratio = 2/3 ≈ 0.6667
    hr = _hit_ratio([100.0, 110.0, 100.0, 110.0])
    assert hr is not None
    assert abs(hr - 2 / 3) < 1e-3


def test_hit_ratio_short_curve_returns_none() -> None:
    assert _hit_ratio([]) is None
    assert _hit_ratio([100.0]) is None


# ---- _side_by_side --------------------------------------------------------


def test_side_by_side_picks_curated_metrics() -> None:
    a = {
        "trades": 100,
        "net_profit": 500.0,
        "fitness_oos": 0.5,
        "symbol": "BTCUSDT",
        "ignored_field": "nope",
    }
    b = {
        "trades": 150,
        "net_profit": 600.0,
        "fitness_oos": 0.6,
        "symbol": "BTCUSDT",
        "another_ignored": "nope",
    }
    out = _side_by_side(a, b)
    # Only curated metrics, ignored fields not included
    assert "ignored_field" not in out
    assert "another_ignored" not in out
    assert out["trades"]["a"] == 100
    assert out["trades"]["b"] == 150
    assert out["trades"]["delta"] == 50
    assert out["trades"]["delta_pct"] == 0.5


def test_side_by_side_handles_none_values() -> None:
    out = _side_by_side({"trades": None}, {"trades": 100})
    assert out["trades"]["a"] is None
    assert out["trades"]["b"] == 100
    # No delta when one side is None
    assert "delta" not in out["trades"]


def test_side_by_side_string_metric_unchanged() -> None:
    out = _side_by_side({"symbol": "BTCUSDT"}, {"symbol": "EURUSD"})
    # No delta computed for non-numeric metrics
    assert "delta" not in out["symbol"]
    assert out["symbol"]["a"] == "BTCUSDT"
    assert out["symbol"]["b"] == "EURUSD"


# ---- _summarize_text -----------------------------------------------------


def test_summarize_text_includes_verdict_line() -> None:
    metrics = {
        "strategy_name": "demo",
        "symbol": "BTCUSDT",
        "timeframe": "H1",
        "trades": 100,
        "net_profit": 500.0,
        "drawdown_pct": 10.0,
        "profit_to_dd_ratio": 5.0,
        "return_pct": 50.0,
        "fitness_is": 0.6,
        "fitness_oos": 0.5,
        "oos_is_ratio": 0.83,
        "trades_per_year": 30,
        "history_years": 3.0,
    }
    verdict = {
        "traffic_light": "green",
        "worst_severity": "none",
        "finding_codes": [],
    }
    lines = _summarize_text(metrics, verdict)
    assert any("demo on BTCUSDT/H1" in line for line in lines)
    assert any("GREEN" in line for line in lines)


# ---- _quality_score ------------------------------------------------------

from sq_mcp.tools.strategy_inspect import _quality_score  # noqa: E402


def test_quality_score_perfect_inputs() -> None:
    metrics = {
        "fitness_oos": 1.0,
        "oos_is_ratio": 0.8,
        "drawdown_pct": 5.0,
        "trades": 500,
        "history_years": 5.0,
    }
    out = _quality_score(metrics, findings=[])
    # Strong on all fronts → high score
    assert out["score"] >= 80
    assert out["tier"] in ("excellent", "good")


def test_quality_score_zero_inputs() -> None:
    out = _quality_score({}, findings=[])
    # No data → 0
    assert out["score"] == 0
    assert out["tier"] == "poor"


def test_quality_score_drawdown_penalizes() -> None:
    base = {
        "fitness_oos": 1.0,
        "oos_is_ratio": 0.8,
        "drawdown_pct": 5.0,
        "trades": 500,
        "history_years": 5.0,
    }
    deep_dd = dict(base, drawdown_pct=50.0)
    s_base = _quality_score(base, findings=[])
    s_deep = _quality_score(deep_dd, findings=[])
    assert s_deep["score"] < s_base["score"]


def test_quality_score_critical_findings_penalize() -> None:
    from sq_mcp.tools.audit import Finding
    metrics = {
        "fitness_oos": 1.0,
        "oos_is_ratio": 0.8,
        "drawdown_pct": 5.0,
        "trades": 500,
        "history_years": 5.0,
    }
    clean = _quality_score(metrics, findings=[])
    with_critical = _quality_score(
        metrics,
        findings=[
            Finding(code="X", severity="critical", title="t", message="m", suggestion=""),
        ],
    )
    # Critical finding subtracts 10
    assert with_critical["score"] == clean["score"] - 10


def test_quality_score_clipped_to_0_100() -> None:
    # Lots of critical findings + low metrics → would go negative, clipped to 0
    from sq_mcp.tools.audit import Finding
    out = _quality_score(
        {"fitness_oos": 0.05},
        findings=[
            Finding(code=f"X{i}", severity="critical", title="t", message="m", suggestion="")
            for i in range(20)
        ],
    )
    assert out["score"] == 0
    # Conversely, can't go above 100
    out_max = _quality_score(
        {"fitness_oos": 10.0, "oos_is_ratio": 10.0, "drawdown_pct": 0.0, "trades": 99999, "history_years": 999},
        findings=[],
    )
    assert out_max["score"] <= 100


# ---- _drawdown_periods ---------------------------------------------------

from sq_mcp.tools.strategy_inspect import _drawdown_periods  # noqa: E402


def test_drawdown_periods_empty_curve() -> None:
    assert _drawdown_periods([]) == []
    assert _drawdown_periods([100.0]) == []


def test_drawdown_periods_monotonic_no_dd() -> None:
    out = _drawdown_periods([100.0, 110.0, 120.0, 130.0])
    assert out == []


def test_drawdown_periods_single_dip_recovered() -> None:
    # Peak 110, dip to 90, recover to 120 → one closed DD period
    out = _drawdown_periods([100.0, 110.0, 100.0, 90.0, 120.0])
    assert len(out) == 1
    p = out[0]
    assert p["start_idx"] == 2
    assert p["end_idx"] == 4
    assert p["peak_value"] == 110.0
    assert p["trough_value"] == 90.0
    assert p["depth_abs"] == 20.0


def test_drawdown_periods_multiple() -> None:
    # Two distinct drawdowns separated by a new peak
    # Curve: 100, 90 (DD1 starts), 110 (DD1 ends, new peak), 80 (DD2 starts), 130 (DD2 ends)
    out = _drawdown_periods([100.0, 90.0, 110.0, 80.0, 130.0])
    assert len(out) == 2


def test_drawdown_periods_open_drawdown() -> None:
    # Curve ends in a drawdown — end_idx must be None
    out = _drawdown_periods([100.0, 110.0, 100.0, 90.0])
    assert len(out) == 1
    assert out[0]["end_idx"] is None
    assert out[0]["trough_value"] == 90.0
    assert out[0]["depth_abs"] == 20.0


def test_drawdown_periods_depth_pct_uses_peak() -> None:
    out = _drawdown_periods([100.0, 200.0, 100.0, 250.0])
    # Peak 200 → trough 100 → 50% of peak
    p = out[0]
    assert p["depth_pct_of_peak"] == 0.5


# ---- _suggest_strategy_name ----------------------------------------------

from sq_mcp.tools.strategy_inspect import _suggest_strategy_name  # noqa: E402


def test_suggest_name_includes_components() -> None:
    metrics = {
        "symbol": "BTCUSDT",
        "timeframe": "H1",
        "fitness_oos": 0.75,
        "drawdown_pct": 12.3,
        "trades": 250,
        "trades_hash": "abcdef1234",
    }
    name = _suggest_strategy_name(metrics)
    assert "BTCUSDT_H1" in name
    assert "oos75" in name
    assert "dd12" in name
    assert "t250" in name
    assert "abcdef" in name


def test_suggest_name_with_prefix() -> None:
    name = _suggest_strategy_name({"symbol": "BTCUSDT"}, prefix="acct-A")
    assert name.startswith("acct-A_")


def test_suggest_name_sanitizes_unsafe_characters() -> None:
    name = _suggest_strategy_name({"symbol": "BTC/USDT", "timeframe": "H1"})
    assert "/" not in name


def test_suggest_name_fallback_when_metrics_missing() -> None:
    name = _suggest_strategy_name({})
    # Falls back to SYM_TF defaults
    assert "SYM_TF" in name


# ---- _recommend_action ---------------------------------------------------

from sq_mcp.tools.strategy_inspect import _recommend_action  # noqa: E402


def test_recommend_drop_on_critical_finding() -> None:
    from sq_mcp.tools.audit import Finding
    findings = [
        Finding(code="X", severity="critical", title="t", message="m", suggestion="s")
    ]
    out = _recommend_action({"fitness_oos": 0.5}, findings)
    assert out["action"] == "DROP"


def test_recommend_drop_when_no_oos_fitness() -> None:
    out = _recommend_action({"fitness_oos": 0}, findings=[])
    assert out["action"] == "DROP"


def test_recommend_tweak_on_high_finding() -> None:
    from sq_mcp.tools.audit import Finding
    findings = [
        Finding(code="X", severity="high", title="t", message="m", suggestion="s")
    ]
    out = _recommend_action(
        {"fitness_oos": 0.5, "oos_is_ratio": 0.8, "trades": 200, "drawdown_pct": 15},
        findings,
    )
    assert out["action"] == "TWEAK"


def test_recommend_ship_on_clean_metrics() -> None:
    out = _recommend_action(
        {
            "fitness_oos": 0.6,
            "oos_is_ratio": 0.75,
            "trades": 250,
            "drawdown_pct": 15.0,
        },
        findings=[],
    )
    assert out["action"] == "SHIP"


def test_recommend_retest_on_thin_sample() -> None:
    out = _recommend_action(
        {
            "fitness_oos": 0.5,
            "oos_is_ratio": 0.7,
            "trades": 50,
            "drawdown_pct": 15,
        },
        findings=[],
    )
    assert out["action"] == "RETEST"
