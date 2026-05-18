"""Unit tests for data_quality helpers."""

from __future__ import annotations

from sq_mcp.tools.data_quality import (
    _expected_bars_for_window,
    _ms_for_yyyymmdd,
)

# ---- _expected_bars_for_window -------------------------------------------


def test_expected_bars_zero_for_inverted_window() -> None:
    assert _expected_bars_for_window(
        timeframe="M1", ms_from=10, ms_to=10, market_hours_per_week=168
    ) == 0
    assert _expected_bars_for_window(
        timeframe="M1", ms_from=20, ms_to=10, market_hours_per_week=168
    ) == 0


def test_expected_bars_M1_24_7() -> None:
    one_day_ms = 86_400_000
    out = _expected_bars_for_window(
        timeframe="M1", ms_from=0, ms_to=one_day_ms, market_hours_per_week=168
    )
    # 24h × 60 = 1440 minutes = 1440 bars
    assert out == 1440


def test_expected_bars_H1_24_7() -> None:
    one_week_ms = 7 * 86_400_000
    out = _expected_bars_for_window(
        timeframe="H1", ms_from=0, ms_to=one_week_ms, market_hours_per_week=168
    )
    # 168 hours / 60 minutes per H1 bar
    assert out == 168


def test_expected_bars_M1_forex_hours_scales_down() -> None:
    one_week_ms = 7 * 86_400_000
    # 120-hour forex week vs 168-hour 24/7 → 120/168 ratio
    out = _expected_bars_for_window(
        timeframe="M1", ms_from=0, ms_to=one_week_ms, market_hours_per_week=120
    )
    # 10080 minutes × (120/168) = 7200 bars
    assert out == 7200


def test_expected_bars_unknown_tf_returns_none() -> None:
    out = _expected_bars_for_window(
        timeframe="UNKNOWN", ms_from=0, ms_to=1000, market_hours_per_week=168
    )
    assert out is None


# ---- _ms_for_yyyymmdd ----------------------------------------------------


def test_ms_for_yyyymmdd_round_trip() -> None:
    # 2024-01-01 UTC = 1704067200000
    ms = _ms_for_yyyymmdd("2024.01.01")
    assert ms == 1_704_067_200_000


def test_ms_for_yyyymmdd_epoch() -> None:
    assert _ms_for_yyyymmdd("1970.01.01") == 0
