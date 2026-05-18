"""Unit tests for pivots helpers."""

from __future__ import annotations

from sq_mcp.tools.pivots import (
    _camarilla,
    _classic,
    _demark,
    _fibonacci,
    _woodie,
)


def test_classic_known_values() -> None:
    r = _classic(110.0, 90.0, 100.0)
    # PP = (110 + 90 + 100) / 3 = 100
    assert r["PP"] == 100.0
    # R1 = 2·100 - 90 = 110
    assert r["R1"] == 110.0
    # S1 = 2·100 - 110 = 90
    assert r["S1"] == 90.0
    # R2 = 100 + (110-90) = 120
    assert r["R2"] == 120.0
    # S2 = 100 - 20 = 80
    assert r["S2"] == 80.0


def test_fibonacci_includes_three_levels() -> None:
    r = _fibonacci(110.0, 90.0, 100.0)
    assert all(k in r for k in ("PP", "R1", "R2", "R3", "S1", "S2", "S3"))
    # R3 should equal PP + full range
    assert r["R3"] == 120.0
    # S3 should equal PP - full range
    assert r["S3"] == 80.0


def test_camarilla_eight_levels() -> None:
    r = _camarilla(110.0, 90.0, 100.0)
    assert all(k in r for k in ("R4", "R3", "R2", "R1", "S1", "S2", "S3", "S4"))
    # R levels descend toward close
    assert r["R4"] > r["R3"] > r["R2"] > r["R1"] > 100.0
    # S levels descend below close
    assert 100.0 > r["S1"] > r["S2"] > r["S3"] > r["S4"]


def test_woodie_pp_weights_close() -> None:
    """Woodie PP = (H+L+2C)/4 — different from classic."""
    r = _woodie(110.0, 90.0, 100.0)
    # PP = (110+90+200)/4 = 100
    assert r["PP"] == 100.0


def test_demark_bullish_close() -> None:
    # Close > Open → bullish branch: X = 2·H + L + C
    r = _demark(o=95.0, h=110.0, low=90.0, c=105.0)
    # X = 220 + 90 + 105 = 415; PP = 415/4 = 103.75
    assert abs(r["PP"] - 103.75) < 1e-6


def test_demark_bearish_close() -> None:
    # Close < Open → bearish branch: X = H + 2·L + C
    r = _demark(o=105.0, h=110.0, low=90.0, c=95.0)
    # X = 110 + 180 + 95 = 385; PP = 385/4 = 96.25
    assert abs(r["PP"] - 96.25) < 1e-6


def test_demark_inside_close() -> None:
    # Close == Open → inside branch: X = H + L + 2·C
    r = _demark(o=100.0, h=110.0, low=90.0, c=100.0)
    # X = 110 + 90 + 200 = 400; PP = 100
    assert r["PP"] == 100.0


def test_pivots_args_reject_non_positive() -> None:
    import pytest
    from pydantic import ValidationError as PydanticVE

    from sq_mcp.tools.pivots import HLCArgs
    with pytest.raises(PydanticVE):
        HLCArgs(high=110.0, low=0.0, close=100.0)
    with pytest.raises(PydanticVE):
        HLCArgs(high=-1.0, low=90.0, close=100.0)
