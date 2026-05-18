"""Unit tests for pinescript_gen helpers."""

from __future__ import annotations

from sq_mcp.tools.pinescript_gen import (
    IndicatorArgs,
    StrategyArgs,
    _alert_template,
    _indicator_source,
    _strategy_source,
    _translate_mt5,
)


def test_indicator_sma_crossover_contains_keywords() -> None:
    args = IndicatorArgs(
        name="TestSMA",
        indicator_type="sma_crossover",
        fast_period=10,
        slow_period=20,
        rsi_overbought=70,
        rsi_oversold=30,
    )
    src = _indicator_source(args)
    assert "//@version=5" in src
    assert "ta.sma(close, 10)" in src
    assert "ta.sma(close, 20)" in src
    assert "ta.crossover" in src
    assert 'title="TestSMA"' in src


def test_indicator_rsi_contains_thresholds() -> None:
    args = IndicatorArgs(
        name="RSI",
        indicator_type="rsi",
        fast_period=14,
        slow_period=14,
        rsi_overbought=75,
        rsi_oversold=25,
    )
    src = _indicator_source(args)
    assert "ta.rsi(close, 14)" in src
    assert "75" in src and "25" in src


def test_indicator_macd() -> None:
    args = IndicatorArgs(
        name="MACD",
        indicator_type="macd",
        fast_period=12,
        slow_period=26,
        rsi_overbought=70,
        rsi_oversold=30,
    )
    src = _indicator_source(args)
    assert "ta.macd" in src
    assert "12" in src and "26" in src


def test_strategy_emits_strategy_directive() -> None:
    args = StrategyArgs(
        name="MyStrat",
        indicator_type="ema_crossover",
        fast_period=10,
        slow_period=20,
        rsi_overbought=70,
        rsi_oversold=30,
        risk_per_trade_pct=1.0,
        take_profit_pct=2.0,
        stop_loss_pct=1.0,
        date_from="2023.01.01",
    )
    src = _strategy_source(args)
    assert "strategy(" in src
    assert "default_qty_type=strategy.percent_of_equity" in src
    assert "strategy.entry" in src
    assert "strategy.exit" in src
    assert "2023.01.01" in src


def test_alert_template_contains_alertcondition() -> None:
    from sq_mcp.tools.pinescript_gen import AlertArgs
    args = AlertArgs(
        symbol="BTCUSDT",
        indicator_name="Test",
        webhook_message_template='{"action":"buy"}',
    )
    src = _alert_template(args)
    assert "alertcondition" in src
    assert '{"action":"buy"}' in src or "buy" in src


def test_translate_mt5_returns_review_flag() -> None:
    summary = {
        "name": "EA1",
        "direction": "long",
        "indicators": ["sma", "rsi"],
        "risk_per_trade_pct": 1.5,
        "take_profit_pct": 3.0,
        "stop_loss_pct": 1.0,
    }
    result = _translate_mt5(summary)
    assert result["manual_review_required"] is True
    assert "strategy(" in result["pine_source"]
    assert "sma" in result["indicators_in_source"]
