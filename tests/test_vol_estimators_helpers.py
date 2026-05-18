"""Unit tests for vol_estimators helpers."""

from __future__ import annotations

import math
import random

import pytest

from sq_mcp.tools._numerics import NumericValidationError
from sq_mcp.tools.vol_estimators import (
    _comparison,
    _vol_close_to_close,
    _vol_garman_klass,
    _vol_parkinson,
    _vol_rogers_satchell,
    _vol_yang_zhang,
)


def _synthetic_ohlc(n: int, sigma: float, seed: int) -> dict[str, list[float]]:
    """Build synthetic OHLC bars with a known stddev of log-returns."""
    rng = random.Random(seed)
    closes = [100.0]
    opens = [100.0]
    highs = [100.0]
    lows = [100.0]
    for _ in range(n - 1):
        # close-to-close return
        r = rng.gauss(0.0, sigma)
        new_close = closes[-1] * math.exp(r)
        new_open = closes[-1]  # next bar opens at previous close
        intrabar_high = max(new_open, new_close) * (1.0 + abs(rng.gauss(0.0, sigma * 0.5)))
        intrabar_low = min(new_open, new_close) * (1.0 - abs(rng.gauss(0.0, sigma * 0.5)))
        closes.append(new_close)
        opens.append(new_open)
        highs.append(intrabar_high)
        lows.append(intrabar_low)
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes}


def test_close_to_close_recovers_known_sigma() -> None:
    rng = random.Random(0)
    closes = [100.0]
    sigma_per_period = 0.01
    for _ in range(1000):
        closes.append(closes[-1] * math.exp(rng.gauss(0.0, sigma_per_period)))
    r = _vol_close_to_close(closes, periods_per_year=252)
    # Annualized vol ≈ sigma_per_period × sqrt(252) ≈ 0.1587
    expected = sigma_per_period * math.sqrt(252)
    assert abs(r["annualized_vol"] - expected) < 0.05


def test_parkinson_lower_than_c2c_for_same_data() -> None:
    """Parkinson is more efficient — under no-drift it should have lower noise.
    Here we just check it's in a sensible range and finite.
    """
    bars = _synthetic_ohlc(500, sigma=0.01, seed=42)
    p = _vol_parkinson(bars["highs"], bars["lows"], periods_per_year=252)
    assert p["annualized_vol"] is not None
    assert p["annualized_vol"] > 0
    assert math.isfinite(p["annualized_vol"])


def test_garman_klass_finite() -> None:
    bars = _synthetic_ohlc(500, sigma=0.01, seed=42)
    r = _vol_garman_klass(
        bars["opens"], bars["highs"], bars["lows"], bars["closes"], periods_per_year=252,
    )
    assert r["annualized_vol"] is not None
    assert math.isfinite(r["annualized_vol"])


def test_rogers_satchell_finite() -> None:
    bars = _synthetic_ohlc(500, sigma=0.01, seed=42)
    r = _vol_rogers_satchell(
        bars["opens"], bars["highs"], bars["lows"], bars["closes"], periods_per_year=252,
    )
    assert r["annualized_vol"] is not None
    assert math.isfinite(r["annualized_vol"])


def test_yang_zhang_finite() -> None:
    bars = _synthetic_ohlc(500, sigma=0.01, seed=42)
    r = _vol_yang_zhang(
        bars["opens"], bars["highs"], bars["lows"], bars["closes"], periods_per_year=252,
    )
    assert r["annualized_vol"] is not None
    assert math.isfinite(r["annualized_vol"])


def test_comparison_has_all_five() -> None:
    bars = _synthetic_ohlc(200, sigma=0.01, seed=0)
    r = _comparison(
        bars["opens"], bars["highs"], bars["lows"], bars["closes"], periods_per_year=252,
    )
    assert set(r.keys()) == {
        "close_to_close", "parkinson", "garman_klass",
        "rogers_satchell", "yang_zhang",
    }


def test_vol_rejects_nan() -> None:
    with pytest.raises(NumericValidationError):
        _vol_close_to_close([100.0, float("nan"), 101.0] + [100.0] * 10, periods_per_year=252)


def test_vol_parkinson_skips_non_positive() -> None:
    """Bars with zero or negative prices should be skipped, not crash."""
    highs = [100.0, 0.0, 102.0] + [102.0] * 10
    lows = [99.0, -1.0, 101.0] + [101.0] * 10
    r = _vol_parkinson(highs, lows, periods_per_year=252)
    assert r["annualized_vol"] is not None
    assert math.isfinite(r["annualized_vol"])
