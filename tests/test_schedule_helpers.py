"""Unit tests for schedule helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sq_mcp.tools.schedule import (
    ScheduleStrategyArgs,
    _parse_iso,
    _quarterly_calendar,
    _schedule_recommendation,
    _workspace_schedule,
)

# ---- _parse_iso ------------------------------------------------------------


def test_parse_iso_date_only() -> None:
    out = _parse_iso("2026-01-15")
    assert out is not None
    assert out.year == 2026
    assert out.month == 1


def test_parse_iso_full_iso() -> None:
    out = _parse_iso("2026-01-15T12:00:00+00:00")
    assert out is not None


def test_parse_iso_zulu_suffix() -> None:
    out = _parse_iso("2026-01-15T12:00:00Z")
    assert out is not None
    assert out.tzinfo is not None


def test_parse_iso_invalid_returns_none() -> None:
    assert _parse_iso("not a date") is None


# ---- _schedule_recommendation ----------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat()


def test_schedule_no_action_when_recent_and_synced() -> None:
    now = datetime.now(timezone.utc)
    out = _schedule_recommendation(
        ScheduleStrategyArgs(
            strategy_built_at=(now - timedelta(days=5)).isoformat(),
            data_updated_at=(now - timedelta(days=4)).isoformat(),
        )
    )
    assert out["action"] == "no_action"


def test_schedule_retest_when_data_30_days_old() -> None:
    now = datetime.now(timezone.utc)
    out = _schedule_recommendation(
        ScheduleStrategyArgs(
            strategy_built_at=(now - timedelta(days=100)).isoformat(),
            data_updated_at=(now - timedelta(days=10)).isoformat(),
        )
    )
    # Data was updated 90 days after build → retest
    assert out["action"] == "retest"


def test_schedule_rebuild_when_data_180_days_old() -> None:
    now = datetime.now(timezone.utc)
    out = _schedule_recommendation(
        ScheduleStrategyArgs(
            strategy_built_at=(now - timedelta(days=400)).isoformat(),
            data_updated_at=(now - timedelta(days=10)).isoformat(),
        )
    )
    assert out["action"] == "rebuild"


def test_schedule_rebuild_when_drift_red() -> None:
    now = datetime.now(timezone.utc)
    out = _schedule_recommendation(
        ScheduleStrategyArgs(
            strategy_built_at=(now - timedelta(days=5)).isoformat(),
            data_updated_at=(now - timedelta(days=4)).isoformat(),
            drift_verdict="red",
        )
    )
    assert out["action"] == "rebuild"


def test_schedule_retest_when_drift_yellow() -> None:
    now = datetime.now(timezone.utc)
    out = _schedule_recommendation(
        ScheduleStrategyArgs(
            strategy_built_at=(now - timedelta(days=5)).isoformat(),
            data_updated_at=(now - timedelta(days=4)).isoformat(),
            drift_verdict="yellow",
        )
    )
    assert out["action"] == "retest"


def test_schedule_retest_after_year() -> None:
    now = datetime.now(timezone.utc)
    out = _schedule_recommendation(
        ScheduleStrategyArgs(
            strategy_built_at=(now - timedelta(days=400)).isoformat(),
            data_updated_at=(now - timedelta(days=395)).isoformat(),
        )
    )
    assert out["action"] in {"retest", "rebuild"}


def test_schedule_quarterly_live_retest() -> None:
    now = datetime.now(timezone.utc)
    out = _schedule_recommendation(
        ScheduleStrategyArgs(
            strategy_built_at=(now - timedelta(days=10)).isoformat(),
            data_updated_at=(now - timedelta(days=5)).isoformat(),
            live_days_since_deploy=120,
        )
    )
    assert out["action"] == "retest"


def test_schedule_invalid_iso_errors() -> None:
    out = _schedule_recommendation(
        ScheduleStrategyArgs(strategy_built_at="not_a_date", data_updated_at="2026-01-01")
    )
    assert out["ok"] is False


# ---- _workspace_schedule ---------------------------------------------------


def test_workspace_schedule_sorts_rebuild_first() -> None:
    now = datetime.now(timezone.utc)
    items = [
        {
            "name": "fresh",
            "strategy_built_at": (now - timedelta(days=5)).isoformat(),
            "data_updated_at": (now - timedelta(days=4)).isoformat(),
        },
        {
            "name": "old",
            "strategy_built_at": (now - timedelta(days=400)).isoformat(),
            "data_updated_at": (now - timedelta(days=10)).isoformat(),
        },
    ]
    out = _workspace_schedule(items)
    # Rebuild first
    assert out["queue"][0]["name"] == "old"
    assert out["queue"][0]["action"] == "rebuild"


def test_workspace_schedule_handles_bad_input() -> None:
    items = [{"strategy_built_at": "bad", "data_updated_at": "also bad"}]
    out = _workspace_schedule(items)
    assert out["queue"][0]["ok"] is False


def test_workspace_schedule_summary_counts() -> None:
    now = datetime.now(timezone.utc)
    items = [
        {
            "strategy_built_at": (now - timedelta(days=5)).isoformat(),
            "data_updated_at": (now - timedelta(days=4)).isoformat(),
        },
        {
            "strategy_built_at": (now - timedelta(days=5)).isoformat(),
            "data_updated_at": (now - timedelta(days=4)).isoformat(),
        },
    ]
    out = _workspace_schedule(items)
    assert out["summary"]["no_action_count"] == 2


# ---- _quarterly_calendar ---------------------------------------------------


def test_quarterly_calendar_crypto() -> None:
    out = _quarterly_calendar(10, "crypto")
    assert "Q1" in out["cadence"]
    assert "Q4" in out["cadence"]


def test_quarterly_calendar_forex() -> None:
    out = _quarterly_calendar(20, "forex")
    assert "Annual rebuild on 5-year window" in out["cadence"]["Q1"]


def test_quarterly_calendar_small_portfolio_note() -> None:
    out = _quarterly_calendar(3, "crypto")
    assert "small portfolio" in out["note"]


def test_quarterly_calendar_large_portfolio_note() -> None:
    out = _quarterly_calendar(50, "crypto")
    assert "large portfolio" in out["note"]


def test_quarterly_calendar_unknown_asset_class() -> None:
    out = _quarterly_calendar(5, "unknown")
    # Falls back to generic cadence
    assert "Q1" in out["cadence"]
