"""Unit tests for mt5_ea_template helpers."""

from __future__ import annotations

from sq_mcp.tools.mt5_ea_template import (
    MT5EAGenerateArgs,
    MT5EAInjectArgs,
    _generate_basic,
    _generate_with_trailing,
    _inject_guardrails,
    _tick_body_basic,
)

# ---- _generate_basic -------------------------------------------------------


def test_generate_basic_contains_required_inputs() -> None:
    args = MT5EAGenerateArgs(
        ea_name="MyEA",
        symbol="BTCUSDT",
        magic_number=12345,
        sl_points=500,
        tp_points=1000,
        risk_pct=1.0,
        max_lots=5.0,
        direction="long",
    )
    src = _generate_basic(args)
    assert "input int    MagicNumber  = 12345" in src
    assert "RiskPctEquity = 1.0" in src
    assert "MaxLots       = 5.0" in src
    assert "SLPoints      = 500" in src
    assert "TPPoints      = 1000" in src
    assert "OnInit" in src
    assert "OnTick" in src
    assert "OnDeinit" in src
    assert "LotSizeFromRisk" in src
    assert "HasOpenPositionForMagic" in src


def test_generate_basic_long_only_has_no_short_block() -> None:
    args = MT5EAGenerateArgs(
        ea_name="MyEA",
        symbol="BTCUSDT",
        magic_number=12345,
        direction="long",
    )
    src = _generate_basic(args)
    assert "long entry" in src
    assert "short entry" not in src


def test_generate_basic_both_includes_long_and_short() -> None:
    args = MT5EAGenerateArgs(
        ea_name="MyEA",
        symbol="BTCUSDT",
        magic_number=12345,
        direction="both",
    )
    src = _generate_basic(args)
    assert "long entry" in src
    assert "short entry" in src


def test_generate_basic_custom_condition_in_source() -> None:
    args = MT5EAGenerateArgs(
        ea_name="MyEA",
        symbol="BTCUSDT",
        magic_number=12345,
        entry_condition_code="iRSI(_Symbol, _Period, 14, PRICE_CLOSE) < 30",
        direction="long",
    )
    src = _generate_basic(args)
    assert "iRSI(_Symbol, _Period, 14, PRICE_CLOSE) < 30" in src


# ---- _generate_with_trailing -----------------------------------------------


def test_generate_with_trailing_includes_trail_helper() -> None:
    args = MT5EAGenerateArgs(
        ea_name="EA1",
        symbol="EURUSD",
        magic_number=99999,
        trail_points=200,
        direction="long",
    )
    src = _generate_with_trailing(args)
    assert "TrailPoints" in src
    assert "ApplyTrailing" in src
    assert "200" in src


def test_generate_with_trailing_calls_apply_trailing_in_ontick() -> None:
    args = MT5EAGenerateArgs(
        ea_name="EA1", symbol="EURUSD", magic_number=99999, direction="long"
    )
    src = _generate_with_trailing(args)
    # Find OnTick and verify ApplyTrailing() is called inside
    on_tick_idx = src.index("void OnTick()")
    rest = src[on_tick_idx:]
    assert "ApplyTrailing();" in rest


# ---- _tick_body_basic ------------------------------------------------------


def test_tick_body_long_uses_buy() -> None:
    body = _tick_body_basic("long", "trueExpr")
    assert "trade.Buy" in body
    assert "trade.Sell" not in body


def test_tick_body_short_uses_sell_with_negated_condition() -> None:
    body = _tick_body_basic("short", "myCondition")
    assert "trade.Sell" in body
    assert "!(myCondition)" in body


def test_tick_body_both_emits_two_blocks() -> None:
    body = _tick_body_basic("both", "x")
    assert body.count("HasOpenPositionForMagic") == 2


# ---- _inject_guardrails ----------------------------------------------------


def test_inject_into_bare_source_adds_inputs() -> None:
    src = "void OnTick() {}"
    out = _inject_guardrails(
        MT5EAInjectArgs(source=src, risk_pct=1.0, max_lots=5.0, magic_number=42)
    )
    assert out["ok"] is True
    assert "MagicNumber" in out["source"]
    assert "RiskPctEquity" in out["source"]
    assert "MaxLots" in out["source"]
    assert any("MagicNumber" in c for c in out["changes"])


def test_inject_idempotent_when_all_present() -> None:
    # Build source that already has the guardrails
    src = """
#property strict
input int MagicNumber = 1;
input double RiskPctEquity = 1.0;
input double MaxLots = 5.0;
double LotSizeFromRisk(double x) { return 0; }
"""
    out = _inject_guardrails(
        MT5EAInjectArgs(source=src, risk_pct=1.0, max_lots=5.0, magic_number=1)
    )
    assert out["ok"] is True
    assert out["changes"] == []


def test_inject_adds_helper_if_missing() -> None:
    src = """
#property strict
input int MagicNumber = 1;
input double RiskPctEquity = 1.0;
input double MaxLots = 5.0;
void OnTick() {}
"""
    out = _inject_guardrails(
        MT5EAInjectArgs(source=src, risk_pct=1.0, max_lots=5.0, magic_number=1)
    )
    assert "LotSizeFromRisk" in out["source"]
    assert any("LotSizeFromRisk" in c for c in out["changes"])


def test_inject_after_property_strict_when_present() -> None:
    src = """#property strict
void OnTick() {}"""
    out = _inject_guardrails(
        MT5EAInjectArgs(source=src, risk_pct=1.0, max_lots=5.0, magic_number=1)
    )
    new = out["source"]
    # Inputs should appear after #property strict
    p_strict_idx = new.index("#property strict")
    on_tick_idx = new.index("void OnTick()")
    risk_idx = new.index("RiskPctEquity")
    assert p_strict_idx < risk_idx < on_tick_idx


# ---- validators ------------------------------------------------------------


def test_invalid_ea_name_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        MT5EAGenerateArgs(
            ea_name="bad name with spaces",
            symbol="X",
            magic_number=1,
        )


def test_invalid_direction_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        MT5EAGenerateArgs(
            ea_name="EA",
            symbol="X",
            magic_number=1,
            direction="sideways",
        )
