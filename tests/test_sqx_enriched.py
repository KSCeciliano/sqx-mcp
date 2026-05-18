"""Unit tests for the enriched SQX parser (Fingerprint, SpecialValuesMap, derived metrics)."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from sq_mcp.parsers.sqx import (
    SqxFile,
    _parse_kv_list,
    _parse_mec_sparkline,
    derive_metrics,
    parse_sqx,
)

# Minimal synthetic settings.xml that exercises every enriched field.
_SETTINGS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ResultsGroup ResultName="Strat_Test">
  <ResultsMap>
    <Results>
      <Result resultKey="Strat_Test">
        <Fitnesses IS="0.823" FS="0.910" IST="0.823" OOS="0.621" OOS1="0.45"/>
        <ValuesMap>
          <Symbol type="String">BTCUSDT</Symbol>
          <Timeframe type="String">H1</Timeframe>
        </ValuesMap>
        <SettingsMap>
          <MoneyManagement.InitialCapital type="Double">20000.0</MoneyManagement.InitialCapital>
          <MoneyManagement.UseFromStrategy type="Boolean">true</MoneyManagement.UseFromStrategy>
          <Slippage type="Double">0.5</Slippage>
        </SettingsMap>
      </Result>
    </Results>
  </ResultsMap>
  <SymbolsMap>
    <SymbolInfo symbolName="BTCUSDT" instrumentName="BTCUSDT_binance">
      <InstrumentInfo instrument="BTCUSDT_binance" description="Crypto"
                       pointValue="1.0" tickSize="0.01" tickStep="0.01"
                       defaultSpread="1.5" defaultSlippage="0" decimals="2"
                       dataType="3" broker="9" exchange="Binance"
                       sector="Crypto" country="" />
    </SymbolInfo>
  </SymbolsMap>
  <SpecialValuesMap>
    <SettingsMap>
      <BacktestDuration type="Double">0.05</BacktestDuration>
      <Complexity type="Integer">12</Complexity>
      <HistoryFrom type="Long">1672531200000</HistoryFrom>
      <HistoryTo type="Long">1738368000000</HistoryTo>
      <DateLastModified type="Long">1745000000000</DateLastModified>
      <AmbiguousTrades type="Integer">2</AmbiguousTrades>
      <StrategyProblems type="Integer">0</StrategyProblems>
      <TotalTicks type="Long">5000000</TotalTicks>
      <OptimizationParameters type="String">SMAPeriod=30,RSIPeriod=14,StopLoss=200,</OptimizationParameters>
      <Fingerprint type="com.strategyquant.tradinglib.results.StrategyFingerprint">
        <Fingerprint strategyName="Strat_Test" exact="111"
                     trades="120" profit="4500.5" drawdown="900.25"
                     fitness="0.823" tradesHash="abc123"/>
      </Fingerprint>
      <MEC_FULL_Main type="String">{{sparklinesWidget data='{"values":[0,1,3,5,7,11,17,20,22,25],"zeroPoint":0}'}}</MEC_FULL_Main>
      <MEC_IS_Main type="String">{{sparklinesWidget data='{"values":[0,1,3,5,7],"zeroPoint":0}'}}</MEC_IS_Main>
      <MEC_OOS_Main type="String">{{sparklinesWidget data='{"values":[],"zeroPoint":0}'}}</MEC_OOS_Main>
    </SettingsMap>
  </SpecialValuesMap>
</ResultsGroup>
"""


def _make_sqx(tmp_path: Path, *, settings_xml: bytes | None = None) -> Path:
    """Build a minimal valid .sqx zip in tmp_path."""
    out = tmp_path / "stub.sqx"
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version.txt", "1")
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        z.writestr(
            "settings.xml",
            settings_xml if settings_xml else _SETTINGS_XML.encode("utf-8"),
        )
    out.write_bytes(buf.getvalue())
    return out


# ---- parse_sqx end-to-end ----------------------------------------------------


def test_parse_sqx_extracts_fingerprint(tmp_path: Path) -> None:
    p = _make_sqx(tmp_path)
    info = parse_sqx(p)
    assert info.fingerprint is not None
    assert info.fingerprint.trades == 120
    assert info.fingerprint.net_profit == pytest.approx(4500.5)
    assert info.fingerprint.drawdown == pytest.approx(900.25)
    assert info.fingerprint.fitness == pytest.approx(0.823)
    assert info.fingerprint.trades_hash == "abc123"


def test_parse_sqx_extracts_symbol_info(tmp_path: Path) -> None:
    p = _make_sqx(tmp_path)
    info = parse_sqx(p)
    si = info.symbol_info
    assert si is not None
    assert si.symbol_name == "BTCUSDT"
    assert si.instrument_name == "BTCUSDT_binance"
    assert si.point_value == 1.0
    assert si.tick_size == 0.01
    assert si.broker_id == 9
    assert si.exchange == "Binance"


def test_parse_sqx_extracts_meta(tmp_path: Path) -> None:
    p = _make_sqx(tmp_path)
    info = parse_sqx(p)
    assert info.meta is not None
    assert info.meta.complexity == 12
    assert info.meta.history_from_ms == 1672531200000
    assert info.meta.history_from_iso == "2023-01-01T00:00:00Z"
    assert info.meta.ambiguous_trades == 2
    assert info.meta.optimization_parameters == {
        "SMAPeriod": "30",
        "RSIPeriod": "14",
        "StopLoss": "200",
    }
    assert info.meta.equity_curve_full == [0, 1, 3, 5, 7, 11, 17, 20, 22, 25]
    assert info.meta.equity_curve_oos == []  # empty in fixture


def test_parse_sqx_per_result_settings(tmp_path: Path) -> None:
    p = _make_sqx(tmp_path)
    info = parse_sqx(p)
    assert len(info.results) == 1
    r = info.results[0]
    assert r.name == "Strat_Test"
    assert r.symbol == "BTCUSDT"
    assert r.timeframe == "H1"
    assert r.initial_capital == pytest.approx(20000.0)
    assert r.stats.fitness_is == pytest.approx(0.823)
    assert r.stats.fitness_oos == pytest.approx(0.621)
    assert "Slippage" in r.settings


# ---- derive_metrics ----------------------------------------------------------


def test_derive_metrics_full_payload(tmp_path: Path) -> None:
    p = _make_sqx(tmp_path)
    m = derive_metrics(parse_sqx(p))
    assert m["strategy_name"] == "Strat_Test"
    assert m["symbol"] == "BTCUSDT"
    assert m["timeframe"] == "H1"
    assert m["trades"] == 120
    assert m["net_profit"] == pytest.approx(4500.5)
    assert m["drawdown_abs"] == pytest.approx(900.25)
    assert m["return_pct"] == pytest.approx(22.5025, rel=1e-4)
    assert m["drawdown_pct"] == pytest.approx(4.50125, rel=1e-4)
    # profit_to_dd_ratio = 4500.5 / 900.25 = ~4.999
    assert m["profit_to_dd_ratio"] == pytest.approx(4.9992, rel=1e-3)
    assert m["avg_trade"] == pytest.approx(4500.5 / 120, rel=1e-4)
    assert m["history_years"] == pytest.approx(
        (1738368000000 - 1672531200000) / 1000 / 86400 / 365.2425, rel=1e-3
    )
    assert m["oos_is_ratio"] == pytest.approx(0.621 / 0.823, rel=1e-3)
    assert m["complexity"] == 12


def test_derive_metrics_missing_data_returns_none(tmp_path: Path) -> None:
    """When the fingerprint/meta are absent, derived ratios should be None (not exceptions)."""
    minimal_xml = """<?xml version="1.0"?><ResultsGroup ResultName="x">
      <ResultsMap><Results><Result resultKey="x"><Fitnesses IS="0.5"/></Result></Results></ResultsMap>
    </ResultsGroup>"""
    p = _make_sqx(tmp_path, settings_xml=minimal_xml.encode("utf-8"))
    info = parse_sqx(p)
    m = derive_metrics(info)
    # No fingerprint -> trades / profit / drawdown all None
    assert m["trades"] is None
    assert m["net_profit"] is None
    assert m["drawdown_abs"] is None
    assert m["return_pct"] is None
    assert m["profit_to_dd_ratio"] is None
    # fitness still present from Result
    assert m["fitness_is"] == 0.5


# ---- helpers -----------------------------------------------------------------


def test_parse_kv_list_handles_trailing_comma_and_spaces() -> None:
    assert _parse_kv_list("a=1, b=2,c=3,") == {"a": "1", "b": "2", "c": "3"}
    assert _parse_kv_list("") == {}
    assert _parse_kv_list("no_equals,a=1") == {"a": "1"}


def test_parse_mec_sparkline_extracts_values() -> None:
    s = "{{sparklinesWidget data='{\"values\":[1,2,3,4],\"zeroPoint\":0}'}}"
    assert _parse_mec_sparkline(s) == [1.0, 2.0, 3.0, 4.0]


def test_parse_mec_sparkline_empty_or_invalid() -> None:
    assert _parse_mec_sparkline(None) == []
    assert _parse_mec_sparkline("") == []
    assert _parse_mec_sparkline("garbage") == []
    assert _parse_mec_sparkline("data='not json'") == []


def test_parse_sqx_rejects_non_zip(tmp_path: Path) -> None:
    p = tmp_path / "not_a_sqx.sqx"
    p.write_text("hello world")
    with pytest.raises(ValueError, match="not a ZIP"):
        parse_sqx(p)


def test_parse_sqx_tolerates_missing_settings(tmp_path: Path) -> None:
    """An empty zip should still produce a SqxFile (just no enrichment)."""
    p = tmp_path / "bare.sqx"
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version.txt", "1")
    p.write_bytes(buf.getvalue())
    info = parse_sqx(p)
    assert isinstance(info, SqxFile)
    assert info.fingerprint is None
    assert info.meta is None
    assert info.symbol_info is None
    assert info.results == []
