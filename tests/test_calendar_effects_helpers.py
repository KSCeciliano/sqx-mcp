"""Unit tests for calendar_effects helpers."""

from __future__ import annotations

from sq_mcp.tools.calendar_effects import (
    _dayofweek_table,
    _empty_bucket,
    _hour_in_session,
    _hour_table,
    _month_table,
    _parse_when,
    _quarter_table,
    _session_split,
)

# ---- _parse_when -----------------------------------------------------------


def test_parse_when_iso() -> None:
    out = _parse_when("2026-01-15T12:00:00+00:00")
    assert out is not None
    assert out.hour == 12


def test_parse_when_zulu() -> None:
    out = _parse_when("2026-01-15T12:00:00Z")
    assert out is not None


def test_parse_when_invalid() -> None:
    assert _parse_when("oops") is None


# ---- _empty_bucket / accumulation ------------------------------------------


def test_empty_bucket_zeros() -> None:
    b = _empty_bucket()
    assert b["trades"] == 0
    assert b["wins"] == 0
    assert b["best"] is None


# ---- _hour_table -----------------------------------------------------------


def test_hour_table_all_hours_present() -> None:
    trades = [{"when": "2026-01-15T12:00:00Z", "pnl": 5.0}]
    out = _hour_table(trades)
    assert len(out["by_hour"]) == 24
    assert out["by_hour"][12]["trades"] == 1
    assert out["best_hour"] == 12


def test_hour_table_best_and_worst() -> None:
    trades = [
        {"when": "2026-01-15T08:00:00Z", "pnl": 10.0},  # h=8
        {"when": "2026-01-15T16:00:00Z", "pnl": -20.0},  # h=16
        {"when": "2026-01-15T08:00:00Z", "pnl": 5.0},
    ]
    out = _hour_table(trades)
    assert out["best_hour"] == 8
    assert out["worst_hour"] == 16


def test_hour_table_skipped_invalid() -> None:
    trades = [
        {"when": "bad", "pnl": 1.0},
        {"when": "2026-01-15T12:00:00Z", "pnl": 2.0},
    ]
    out = _hour_table(trades)
    assert out["skipped_unparseable"] == 1


# ---- _dayofweek_table ------------------------------------------------------


def test_dayofweek_table_named_buckets() -> None:
    # 2026-01-15 is a Thursday
    trades = [{"when": "2026-01-15T12:00:00Z", "pnl": 10.0}]
    out = _dayofweek_table(trades)
    assert out["by_day_of_week"]["Thu"]["trades"] == 1
    assert out["best_day"] == "Thu"


def test_dayofweek_table_all_days_present() -> None:
    trades = [{"when": "2026-01-15T12:00:00Z", "pnl": 10.0}]
    out = _dayofweek_table(trades)
    for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
        assert d in out["by_day_of_week"]


# ---- _month_table ----------------------------------------------------------


def test_month_table_january_assigned() -> None:
    trades = [
        {"when": "2026-01-15T00:00:00Z", "pnl": 100.0},
        {"when": "2026-05-15T00:00:00Z", "pnl": -50.0},
    ]
    out = _month_table(trades)
    assert out["best_month"] == 1
    assert out["worst_month"] == 5
    assert len(out["by_month"]) == 12


# ---- _quarter_table --------------------------------------------------------


def test_quarter_table_correct_mapping() -> None:
    # Jan = Q1, Apr = Q2, Jul = Q3, Oct = Q4
    trades = [
        {"when": "2026-01-15T00:00:00Z", "pnl": 1.0},
        {"when": "2026-04-15T00:00:00Z", "pnl": 2.0},
        {"when": "2026-07-15T00:00:00Z", "pnl": 3.0},
        {"when": "2026-10-15T00:00:00Z", "pnl": 4.0},
    ]
    out = _quarter_table(trades)
    assert out["by_quarter"][1]["trades"] == 1
    assert out["by_quarter"][2]["trades"] == 1
    assert out["by_quarter"][3]["trades"] == 1
    assert out["by_quarter"][4]["trades"] == 1
    assert out["best_quarter"] == 4


# ---- _hour_in_session ------------------------------------------------------


def test_hour_in_session_simple_range() -> None:
    assert _hour_in_session(10, 8, 16)
    assert _hour_in_session(8, 8, 16)
    assert not _hour_in_session(16, 8, 16)  # end is exclusive
    assert not _hour_in_session(7, 8, 16)


def test_hour_in_session_wraps_midnight() -> None:
    # Session 22:00 → 04:00 next day
    assert _hour_in_session(23, 22, 4)
    assert _hour_in_session(2, 22, 4)
    assert not _hour_in_session(10, 22, 4)


def test_hour_in_session_zero_length_is_empty() -> None:
    assert not _hour_in_session(5, 5, 5)


# ---- _session_split --------------------------------------------------------


def test_session_split_categorizes_correctly() -> None:
    sessions = [
        {"name": "asia", "start_hour": 0, "end_hour": 8},
        {"name": "us", "start_hour": 13, "end_hour": 20},
    ]
    trades = [
        {"when": "2026-01-15T03:00:00Z", "pnl": 10.0},  # asia
        {"when": "2026-01-15T15:00:00Z", "pnl": 20.0},  # us
        {"when": "2026-01-15T10:00:00Z", "pnl": -5.0},  # uncategorized
    ]
    out = _session_split(trades, sessions)
    assert out["by_session"]["asia"]["trades"] == 1
    assert out["by_session"]["us"]["trades"] == 1
    assert out["by_session"]["uncategorized"]["trades"] == 1
    assert out["best_session"] == "us"


def test_session_split_wrap_midnight() -> None:
    sessions = [{"name": "overnight", "start_hour": 22, "end_hour": 6}]
    trades = [
        {"when": "2026-01-15T23:00:00Z", "pnl": 5.0},
        {"when": "2026-01-15T02:00:00Z", "pnl": 3.0},
        {"when": "2026-01-15T12:00:00Z", "pnl": 1.0},  # uncategorized
    ]
    out = _session_split(trades, sessions)
    assert out["by_session"]["overnight"]["trades"] == 2
    assert out["by_session"]["uncategorized"]["trades"] == 1
