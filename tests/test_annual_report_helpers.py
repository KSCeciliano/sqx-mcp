"""Unit tests for annual_report helpers."""

from __future__ import annotations

from sq_mcp.tools.annual_report import (
    DriftOutcome,
    ReportComposeArgs,
    TopStrategy,
    _compose_report,
    _fmt,
    _format_drift_summary,
    _format_recent_activity,
    _format_top_strategies,
)

# ---- _fmt ------------------------------------------------------------------


def test_fmt_none() -> None:
    assert _fmt(None) == "—"


def test_fmt_int() -> None:
    assert _fmt(42) == "42"


def test_fmt_float() -> None:
    # Floats truncated to 4 significant
    assert "1.235" in _fmt(1.234567)


# ---- _format_top_strategies ------------------------------------------------


def test_top_strategies_empty() -> None:
    out = _format_top_strategies([])
    assert "No strategies" in out


def test_top_strategies_table_rows() -> None:
    rows = [
        {"rank": 1, "name": "A", "fitness": 1.5, "drawdown_pct": 10.0, "trades": 200, "profit_factor": 1.8, "tags": ["prod"]},
        {"rank": 2, "name": "B", "fitness": 1.2, "drawdown_pct": 15.0, "trades": 100, "profit_factor": 1.4, "tags": []},
    ]
    out = _format_top_strategies(rows)
    assert "A" in out
    assert "B" in out
    assert "prod" in out
    assert "| Rank | Name |" in out


def test_top_strategies_handles_missing_fields() -> None:
    rows = [{"rank": 1, "name": "X", "tags": []}]
    out = _format_top_strategies(rows)
    # Should render dashes for missing fields
    assert "—" in out


# ---- _format_drift_summary -------------------------------------------------


def test_drift_summary_empty() -> None:
    out = _format_drift_summary([])
    assert "No drift data" in out


def test_drift_summary_counts() -> None:
    outcomes = [
        {"strategy_name": "A", "verdict": "green"},
        {"strategy_name": "B", "verdict": "red"},
        {"strategy_name": "C", "verdict": "yellow"},
        {"strategy_name": "D", "verdict": "yellow"},
    ]
    out = _format_drift_summary(outcomes)
    assert "green=1" in out
    assert "red=1" in out
    assert "yellow=2" in out


def test_drift_summary_highlights_reds_and_yellows() -> None:
    outcomes = [
        {"strategy_name": "RedOne", "verdict": "red", "note": "high DD"},
        {"strategy_name": "YellowOne", "verdict": "yellow", "note": "small drift"},
    ]
    out = _format_drift_summary(outcomes)
    assert "flagged red" in out
    assert "RedOne" in out
    assert "high DD" in out
    assert "flagged yellow" in out
    assert "YellowOne" in out


def test_drift_summary_only_red_no_yellow() -> None:
    outcomes = [
        {"strategy_name": "A", "verdict": "red"},
    ]
    out = _format_drift_summary(outcomes)
    assert "flagged red" in out
    assert "flagged yellow" not in out


# ---- _format_recent_activity -----------------------------------------------


def test_recent_activity_empty() -> None:
    out = _format_recent_activity([])
    assert "No activity" in out


def test_recent_activity_sorted_newest_first() -> None:
    entries = [
        {"when": "2026-01-01", "what": "first"},
        {"when": "2026-05-01", "what": "newest"},
        {"when": "2026-03-01", "what": "middle"},
    ]
    out = _format_recent_activity(entries)
    # "newest" should appear before "first"
    assert out.index("newest") < out.index("middle") < out.index("first")


def test_recent_activity_escapes_pipe_in_details() -> None:
    entries = [{"when": "2026-05-01", "what": "x", "details": "a | b | c"}]
    out = _format_recent_activity(entries)
    assert "a \\| b" in out


# ---- _compose_report -------------------------------------------------------


def test_compose_report_includes_title_and_period() -> None:
    args = ReportComposeArgs(
        title="Annual 2026",
        period_start="2026-01-01",
        period_end="2026-12-31",
    )
    md = _compose_report(args)
    assert "Annual 2026" in md
    assert "2026-01-01" in md
    assert "2026-12-31" in md


def test_compose_report_includes_portfolio_summary() -> None:
    args = ReportComposeArgs(
        title="X",
        period_start="2026-01-01",
        period_end="2026-12-31",
        portfolio_summary={"strategies": 12, "total_return_pct": 22.5},
    )
    md = _compose_report(args)
    assert "strategies" in md
    assert "12" in md


def test_compose_report_includes_all_sections() -> None:
    args = ReportComposeArgs(
        title="X",
        period_start="2026-01-01",
        period_end="2026-12-31",
        top_strategies=[
            TopStrategy(rank=1, name="A", fitness=1.5),
        ],
        drift_outcomes=[
            DriftOutcome(strategy_name="A", verdict="green"),
        ],
        recent_activity=[],
        notes=["Be cautious next quarter."],
    )
    md = _compose_report(args)
    assert "## Top strategies" in md
    assert "## Drift summary" in md
    assert "## Recent activity" in md
    assert "## Notes" in md
    assert "Be cautious next quarter" in md


def test_compose_report_omits_summary_when_none() -> None:
    args = ReportComposeArgs(
        title="X", period_start="2026-01-01", period_end="2026-12-31"
    )
    md = _compose_report(args)
    assert "## Portfolio summary" not in md
