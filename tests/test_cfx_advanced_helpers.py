"""Unit tests for cfx_advanced patchers."""

from __future__ import annotations

from lxml import etree

from sq_mcp.tools.cfx_advanced import (
    _bool_or_none,
    _fmt,
    _fmt_int,
    _list_building_blocks,
    _patch_blocks_toggle,
    _patch_buildmode,
    _patch_max_strategies,
    _patch_options_params,
    _patch_slpt,
)

# A minimal Settings XML that includes the elements each patcher targets.
_XML = b"""<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<Settings>
  <Options customSettings="false">
    <BuildTradingOptions>
      <Params>
        <Param key="ExitAtEndOfDay" className="ExitAtEndOfDay">false</Param>
        <Param key="ExitOnFriday" className="ExitOnFriday">true</Param>
        <Param key="LimitTimeRange" className="LimitTimeRange">false</Param>
        <Param key="SignalTimeRangeFrom" className="LimitTimeRange">28800</Param>
        <Param key="SignalTimeRangeTo" className="LimitTimeRange">57600</Param>
        <Param key="MaxTradesPerDay" className="MaxTradesPerDay">0</Param>
      </Params>
    </BuildTradingOptions>
  </Options>
  <WhatToBuild>
    <SLPTOptions>
      <SLRequired>true</SLRequired>
      <MinSLInPips>30</MinSLInPips>
      <MaxSLInPips>80</MaxSLInPips>
      <MinPTInPips>60</MinPTInPips>
      <MaxPTInPips>200</MaxPTInPips>
      <PTRequired>true</PTRequired>
    </SLPTOptions>
    <BuildMode generationType="genetic-evolution">
      <PopulationSize>100</PopulationSize>
      <MaxGenerations>100</MaxGenerations>
      <CrossoverProbability>93</CrossoverProbability>
      <MutationProbability>30</MutationProbability>
      <EvoInSamplePeriod ratio="50" />
    </BuildMode>
  </WhatToBuild>
  <Rankings type="never">
    <MaxStrategies>1000</MaxStrategies>
    <StopCondition type="databank-full" passedStrategies="1000" restartCount="5" days="0" hours="0" minutes="0" />
  </Rankings>
  <Blocks type="simple">
    <BuildingBlocks>
      <Block key="ADXChangesDown" weight="1" use="false" category="signals" />
      <Block key="ADXChangesUp" weight="1" use="false" category="signals" />
      <Block key="Stop/Limit Price Levels.Ask" weight="1" use="true" category="stopLimitBlocks" />
      <Block key="Stop/Limit Price Levels.EMA" weight="1" use="false" category="stopLimitBlocks" />
    </BuildingBlocks>
  </Blocks>
</Settings>
"""


# ---- _patch_options_params -------------------------------------------------


def test_patch_options_params_updates_existing_param() -> None:
    new_bytes, changes, present = _patch_options_params(
        _XML, updates={"MaxTradesPerDay": "5"}
    )
    assert present
    assert changes == {"BuildTradingOptions/MaxTradesPerDay": 1}
    root = etree.fromstring(new_bytes)
    val = root.find(".//Param[@key='MaxTradesPerDay']").text
    assert val == "5"


def test_patch_options_params_noop_when_already_target() -> None:
    new_bytes, changes, present = _patch_options_params(
        _XML, updates={"ExitOnFriday": "true"}  # already true
    )
    assert present
    assert changes == {}


def test_patch_options_params_ignores_unknown_keys() -> None:
    new_bytes, changes, present = _patch_options_params(
        _XML, updates={"DoesNotExist": "999"}
    )
    assert present
    assert changes == {}


def test_patch_options_params_returns_not_present_when_missing() -> None:
    no_options = b"<Settings/>"
    new_bytes, changes, present = _patch_options_params(
        no_options, updates={"MaxTradesPerDay": "5"}
    )
    assert not present
    assert changes == {}


# ---- _patch_slpt -----------------------------------------------------------


def test_patch_slpt_updates_pips() -> None:
    new_bytes, changes, present = _patch_slpt(_XML, updates={"MinSLInPips": "20"})
    assert present
    assert changes == {"SLPTOptions/MinSLInPips": 1}
    root = etree.fromstring(new_bytes)
    assert root.find(".//SLPTOptions/MinSLInPips").text == "20"


def test_patch_slpt_multiple_updates() -> None:
    new_bytes, changes, present = _patch_slpt(
        _XML,
        updates={
            "MinSLInPips": "10",
            "MaxSLInPips": "60",
            "SLRequired": "false",
        },
    )
    assert changes["SLPTOptions/MinSLInPips"] == 1
    assert changes["SLPTOptions/MaxSLInPips"] == 1
    assert changes["SLPTOptions/SLRequired"] == 1


# ---- _patch_buildmode -----------------------------------------------------


def test_patch_buildmode_text_field() -> None:
    new_bytes, changes, present = _patch_buildmode(_XML, updates={"PopulationSize": "200"})
    assert present
    root = etree.fromstring(new_bytes)
    assert root.find(".//BuildMode/PopulationSize").text == "200"
    assert changes == {"BuildMode/PopulationSize": 1}


def test_patch_buildmode_in_sample_ratio_uses_attribute() -> None:
    new_bytes, changes, _ = _patch_buildmode(_XML, updates={"in_sample_ratio": "70"})
    assert changes == {"BuildMode/EvoInSamplePeriod@ratio": 1}
    root = etree.fromstring(new_bytes)
    assert root.find(".//EvoInSamplePeriod").get("ratio") == "70"


# ---- _patch_max_strategies ------------------------------------------------


def test_patch_max_strategies_updates_both_when_sync() -> None:
    new_bytes, changes, present = _patch_max_strategies(_XML, new_max=500, sync_stop=True)
    assert present
    assert changes == {
        "Rankings/MaxStrategies": 1,
        "StopCondition/passedStrategies": 1,
    }
    root = etree.fromstring(new_bytes)
    assert root.find(".//MaxStrategies").text == "500"
    assert root.find(".//StopCondition").get("passedStrategies") == "500"


def test_patch_max_strategies_no_sync_only_touches_one() -> None:
    new_bytes, changes, _ = _patch_max_strategies(_XML, new_max=250, sync_stop=False)
    assert changes == {"Rankings/MaxStrategies": 1}


def test_patch_max_strategies_noop_when_same() -> None:
    new_bytes, changes, _ = _patch_max_strategies(_XML, new_max=1000, sync_stop=True)
    assert changes == {}


# ---- _list_building_blocks -----------------------------------------------


def test_list_building_blocks_full_listing() -> None:
    blocks = _list_building_blocks(_XML, category_prefix=None, only_enabled=False)
    assert len(blocks) == 4
    cats = {b["category"] for b in blocks}
    assert cats == {"signals", "stopLimitBlocks"}


def test_list_building_blocks_filters_by_category_prefix() -> None:
    blocks = _list_building_blocks(_XML, category_prefix="sig", only_enabled=False)
    assert all(b["category"].startswith("sig") for b in blocks)
    assert len(blocks) == 2


def test_list_building_blocks_filters_only_enabled() -> None:
    blocks = _list_building_blocks(_XML, category_prefix=None, only_enabled=True)
    keys = {b["key"] for b in blocks}
    assert keys == {"Stop/Limit Price Levels.Ask"}


# ---- _patch_blocks_toggle -------------------------------------------------


def test_patch_blocks_toggle_enables_category() -> None:
    new_bytes, changes, present = _patch_blocks_toggle(
        _XML, enable=True, key_prefix=None, category="signals"
    )
    assert present
    # Both signals blocks were use="false" -> "true" (2 flips)
    assert changes["BuildingBlocks/signals/use=true"] == 2
    assert changes["matched_blocks=2"] == 2


def test_patch_blocks_toggle_disables_by_key_prefix() -> None:
    new_bytes, changes, _ = _patch_blocks_toggle(
        _XML, enable=False, key_prefix="Stop/Limit Price Levels.Ask", category=None
    )
    # Matches "Stop/Limit Price Levels.Ask" only — already false would noop;
    # but it was true → 1 flip
    assert changes["BuildingBlocks/stopLimitBlocks/use=false"] == 1


def test_patch_blocks_toggle_combined_filters_intersect() -> None:
    # category=signals AND prefix that matches one signal block
    new_bytes, changes, _ = _patch_blocks_toggle(
        _XML, enable=True, key_prefix="ADXChangesUp", category="signals"
    )
    assert changes["matched_blocks=1"] == 1


def test_patch_blocks_toggle_no_matches_is_safe() -> None:
    new_bytes, changes, present = _patch_blocks_toggle(
        _XML, enable=True, key_prefix="NoMatchAtAll", category=None
    )
    assert present
    assert changes["matched_blocks=0"] == 0


# ---- tiny formatters -----------------------------------------------------


def test_fmt_strips_trailing_zero() -> None:
    assert _fmt(30) == "30"
    assert _fmt(1.5) == "1.5"
    assert _fmt(None) is None


def test_fmt_int_handles_none() -> None:
    assert _fmt_int(None) is None
    assert _fmt_int(0) == "0"
    assert _fmt_int(42) == "42"


def test_bool_or_none() -> None:
    assert _bool_or_none(True) == "true"
    assert _bool_or_none(False) == "false"
    assert _bool_or_none(None) is None


# ---- _patch_setup_attrs --------------------------------------------------

from sq_mcp.tools.cfx_advanced import _patch_setup_attrs  # noqa: E402

_SETUP_XML = b"""<?xml version='1.0' encoding='UTF-8'?>
<Settings>
  <Data>
    <Setups>
      <Setup dateFrom="2023.01.01" dateTo="2026.01.01" testPrecision="1" session="No Session" slippage="1" minDist="0" engine="MetaTrader4">
        <Chart symbol="BTCUSDT_M1" timeframe="H1" spread="3" />
      </Setup>
    </Setups>
  </Data>
</Settings>
"""


def test_patch_setup_attrs_updates_session() -> None:
    new_bytes, changes, present = _patch_setup_attrs(
        _SETUP_XML,
        setup_updates={"session": "NewYork", "slippage": None, "minDist": None, "engine": None},
        chart_spread=None,
        test_precision=None,
    )
    assert present
    assert changes == {"Setup/@session": 1}
    assert b'session="NewYork"' in new_bytes


def test_patch_setup_attrs_updates_chart_spread() -> None:
    new_bytes, changes, _ = _patch_setup_attrs(
        _SETUP_XML,
        setup_updates={"session": None, "slippage": None, "minDist": None, "engine": None},
        chart_spread="5",
        test_precision=None,
    )
    assert changes == {"Chart/@spread": 1}
    assert b'spread="5"' in new_bytes


def test_patch_setup_attrs_test_precision() -> None:
    new_bytes, changes, _ = _patch_setup_attrs(
        _SETUP_XML,
        setup_updates={"session": None, "slippage": None, "minDist": None, "engine": None},
        chart_spread=None,
        test_precision="2",
    )
    assert changes == {"Setup/@testPrecision": 1}
    assert b'testPrecision="2"' in new_bytes


def test_patch_setup_attrs_noop_when_already_matching() -> None:
    new_bytes, changes, _ = _patch_setup_attrs(
        _SETUP_XML,
        setup_updates={"session": "No Session", "slippage": None, "minDist": None, "engine": None},
        chart_spread=None,
        test_precision=None,
    )
    assert changes == {}


def test_patch_setup_attrs_not_present_when_no_setup() -> None:
    no_setup = b"<Settings/>"
    new_bytes, changes, present = _patch_setup_attrs(
        no_setup,
        setup_updates={"session": "Asia", "slippage": None, "minDist": None, "engine": None},
        chart_spread=None,
        test_precision=None,
    )
    assert not present
    assert changes == {}
