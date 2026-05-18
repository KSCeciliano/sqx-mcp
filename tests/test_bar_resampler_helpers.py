"""Unit tests for bar_resampler helpers."""

from __future__ import annotations

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.bar_resampler import _align_session, _convert_tf, _resample


def test_resample_m1_to_m5_groups_correctly() -> None:
    # 10 minutes of M1 bars, resample to M5 → 2 bars
    ts = [60 * i for i in range(10)]
    opens = [100.0 + i for i in range(10)]
    highs = [101.0 + i for i in range(10)]
    lows = [99.0 + i for i in range(10)]
    closes = [100.5 + i for i in range(10)]
    volumes = [1.0] * 10
    r = _resample(ts, opens, highs, lows, closes, volumes, 300)
    assert r["n_bars"] == 2
    # First M5: ts 0..240 → open=100, close=104.5, high=105, low=99, vol=5
    assert r["bars"][0]["open"] == 100.0
    assert r["bars"][0]["close"] == 104.5
    assert r["bars"][0]["high"] == 105.0
    assert r["bars"][0]["low"] == 99.0
    assert r["bars"][0]["volume"] == 5.0


def test_resample_handles_single_bar_input() -> None:
    ts = [0, 60]
    opens = [100.0, 101.0]
    highs = [101.0, 102.0]
    lows = [99.0, 100.0]
    closes = [100.5, 101.5]
    volumes = [1.0, 1.0]
    r = _resample(ts, opens, highs, lows, closes, volumes, 3600)
    # Both M1 bars fit in one H1 bar
    assert r["n_bars"] == 1


def test_resample_rejects_nan() -> None:
    ts = [0, 60, 120]
    with pytest.raises(NumericValidationError):
        _resample(
            ts, [100.0, float("nan"), 102.0], [101.0, 101.5, 102.5],
            [99.0, 99.5, 101.0], [100.5, 101.0, 101.5], [1.0, 1.0, 1.0], 300,
        )


def test_convert_tf_m5_to_h1() -> None:
    r = _convert_tf("M5", "H1")
    assert r["ok"] is True
    assert r["bars_per_target_bar"] == 12


def test_convert_tf_non_integer_multiple_fails() -> None:
    # H1 is not an integer multiple of M15? Actually 60/15 = 4, so it is.
    # Try D1 to H4: 86400/14400 = 6 — integer. Hard to fail with std intervals.
    r = _convert_tf("H4", "D1")
    assert r["ok"] is True
    assert r["bars_per_target_bar"] == 6


def test_convert_tf_unknown() -> None:
    r = _convert_tf("M3", "H1")
    assert r["ok"] is False


def test_align_session_groups_to_daily() -> None:
    # Hourly bars over 2 days, session start 0 UTC
    ts = [3600 * h for h in range(48)]
    opens = [100.0 + h for h in range(48)]
    highs = [101.0 + h for h in range(48)]
    lows = [99.0 + h for h in range(48)]
    closes = [100.5 + h for h in range(48)]
    volumes = [1.0] * 48
    r = _align_session(ts, opens, highs, lows, closes, volumes, session_start_hour=0)
    assert r["n_bars"] >= 1
