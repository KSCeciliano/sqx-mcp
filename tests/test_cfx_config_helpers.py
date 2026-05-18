"""Unit tests for CFX configuration helpers (fitness / MM / data range)."""

from __future__ import annotations

from lxml import etree

from sq_mcp.tools.cfx_config import (
    _inspect_task_xml,
    _is_build_like_task,
    _patch_data_range_in_xml,
    _patch_fitness_in_xml,
    _patch_money_management_in_xml,
)

# Minimal Build-Task XML that exercises every patcher target.
_SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Settings>
  <RiskMoneyManagement>
    <MoneyManagement>
      <Method type="FixedSize" use="true">
        <Params>
          <Param key="Size" className="FixedSize">0.1</Param>
        </Params>
      </Method>
      <InitialCapital>10000</InitialCapital>
      <Method type="RiskFixedBalancePct" use="false">
        <Params>
          <Param key="Risk" className="RiskFixedBalancePct">5</Param>
          <Param key="Decimals" className="RiskFixedBalancePct">1</Param>
        </Params>
      </Method>
    </MoneyManagement>
  </RiskMoneyManagement>
  <Data>
    <Setups>
      <Setup dateFrom="2003.05.05" dateTo="2019.12.31" testPrecision="1" engine="MetaTrader4">
        <Chart symbol="GBPUSD" timeframe="H1" spread="3" />
      </Setup>
    </Setups>
    <OutOfSample showGraph="false" />
  </Data>
  <Rankings>
    <FitnessCriteria method="ComputeFromStrategyResult">
      <Settings>
        <Ranking type="ReturnDDRatio" />
      </Settings>
    </FitnessCriteria>
  </Rankings>
</Settings>
"""


# ---- _is_build_like_task --------------------------------------------------


def test_is_build_like_task_recognizes_build_and_optimize() -> None:
    assert _is_build_like_task("Build-Task1.xml")
    assert _is_build_like_task("Optimize-Task2.xml")
    assert _is_build_like_task("subdir/Build-Task1.xml")


def test_is_build_like_task_rejects_retest_and_others() -> None:
    assert not _is_build_like_task("Retest-Task1.xml")
    assert not _is_build_like_task("WalkForward-Task1.xml")
    assert not _is_build_like_task("MonteCarlo-Task1.xml")
    assert not _is_build_like_task("config.xml")
    assert not _is_build_like_task("Build-Task1.txt")


# ---- _patch_fitness_in_xml ------------------------------------------------


def test_patch_fitness_changes_ranking_type() -> None:
    new_bytes, changes, present = _patch_fitness_in_xml(
        _SAMPLE_XML, ranking_type="NetProfit", method=None
    )
    assert present
    assert changes["ranking_type"] == 1
    root = etree.fromstring(new_bytes)
    assert root.find(".//FitnessCriteria/Settings/Ranking").get("type") == "NetProfit"


def test_patch_fitness_changes_method_attr() -> None:
    new_bytes, changes, present = _patch_fitness_in_xml(
        _SAMPLE_XML, ranking_type="ReturnDDRatio", method="ComputeFromTrades"
    )
    assert present
    assert changes["fitness_method"] == 1
    root = etree.fromstring(new_bytes)
    assert root.find(".//FitnessCriteria").get("method") == "ComputeFromTrades"


def test_patch_fitness_noop_when_already_set() -> None:
    _, changes, present = _patch_fitness_in_xml(
        _SAMPLE_XML, ranking_type="ReturnDDRatio", method="ComputeFromStrategyResult"
    )
    assert present
    assert changes.total() == 0


def test_patch_fitness_returns_not_present_when_block_missing() -> None:
    xml = b"<?xml version='1.0'?><Settings><Data/></Settings>"
    new_bytes, changes, present = _patch_fitness_in_xml(
        xml, ranking_type="NetProfit", method=None
    )
    assert not present
    assert new_bytes == xml
    assert changes.total() == 0


# ---- _patch_money_management_in_xml ---------------------------------------


def test_patch_money_management_activates_new_method() -> None:
    new_bytes, changes, present, available = _patch_money_management_in_xml(
        _SAMPLE_XML,
        method_type="RiskFixedBalancePct",
        params={"Risk": "2"},
        initial_capital=None,
    )
    assert present
    assert "FixedSize" in available
    assert "RiskFixedBalancePct" in available

    root = etree.fromstring(new_bytes)
    methods = {m.get("type"): m.get("use") for m in root.findall(".//Method")}
    assert methods["FixedSize"] == "false"
    assert methods["RiskFixedBalancePct"] == "true"

    # Risk param was updated
    risk_param = root.find(".//Method[@type='RiskFixedBalancePct']/Params/Param[@key='Risk']")
    assert risk_param.text == "2"
    # Change counter records the use= flips and the param edit
    assert changes["method_FixedSize_use=false"] == 1
    assert changes["method_RiskFixedBalancePct_use=true"] == 1
    assert changes["param_RiskFixedBalancePct_Risk"] == 1


def test_patch_money_management_updates_initial_capital() -> None:
    new_bytes, changes, _, _ = _patch_money_management_in_xml(
        _SAMPLE_XML,
        method_type="FixedSize",
        params=None,
        initial_capital=25000,
    )
    root = etree.fromstring(new_bytes)
    assert root.find(".//MoneyManagement/InitialCapital").text == "25000"
    assert changes["initial_capital"] == 1


def test_patch_money_management_does_not_create_unknown_params() -> None:
    new_bytes, changes, present, _ = _patch_money_management_in_xml(
        _SAMPLE_XML,
        method_type="FixedSize",
        params={"NonexistentKey": "5"},
        initial_capital=None,
    )
    assert present
    root = etree.fromstring(new_bytes)
    # We didn't create the param
    assert root.find(".//Method[@type='FixedSize']/Params/Param[@key='NonexistentKey']") is None
    # The change counter records the omission
    assert any(k.startswith("params_missing_on_method:") for k in changes)


def test_patch_money_management_returns_available_when_target_missing() -> None:
    _, changes, present, available = _patch_money_management_in_xml(
        _SAMPLE_XML,
        method_type="DoesNotExist",
        params=None,
        initial_capital=None,
    )
    assert present  # block exists, just target method doesn't
    assert "DoesNotExist" not in available
    assert "FixedSize" in available
    assert changes.total() == 0


# ---- _patch_data_range_in_xml ---------------------------------------------


def test_patch_data_range_updates_both_dates() -> None:
    new_bytes, changes, present = _patch_data_range_in_xml(
        _SAMPLE_XML, date_from="2023.01.01", date_to="2025.12.31"
    )
    assert present
    root = etree.fromstring(new_bytes)
    setup = root.find(".//Data/Setups/Setup")
    assert setup.get("dateFrom") == "2023.01.01"
    assert setup.get("dateTo") == "2025.12.31"
    assert changes["dateFrom"] == 1
    assert changes["dateTo"] == 1


def test_patch_data_range_only_updates_supplied_dates() -> None:
    new_bytes, changes, present = _patch_data_range_in_xml(
        _SAMPLE_XML, date_from="2023.01.01", date_to=None
    )
    assert present
    root = etree.fromstring(new_bytes)
    setup = root.find(".//Data/Setups/Setup")
    assert setup.get("dateFrom") == "2023.01.01"
    # untouched
    assert setup.get("dateTo") == "2019.12.31"
    assert changes["dateFrom"] == 1
    assert "dateTo" not in changes


def test_patch_data_range_returns_not_present_when_no_setups() -> None:
    xml = b"<?xml version='1.0'?><Settings><Rankings/></Settings>"
    new_bytes, changes, present = _patch_data_range_in_xml(
        xml, date_from="2023.01.01", date_to="2025.12.31"
    )
    assert not present
    assert changes.total() == 0
    assert new_bytes == xml


# ---- _inspect_task_xml ----------------------------------------------------


def test_inspect_task_xml_reads_everything() -> None:
    out = _inspect_task_xml(_SAMPLE_XML)
    assert out["fitness"] == {
        "method": "ComputeFromStrategyResult",
        "ranking_type": "ReturnDDRatio",
    }
    mm = out["money_management"]
    assert mm["active_method"] == "FixedSize"
    assert mm["active_params"] == {"Size": "0.1"}
    assert mm["initial_capital"] == "10000"
    assert set(mm["available_methods"]) == {"FixedSize", "RiskFixedBalancePct"}
    assert out["data_setups"][0]["dateFrom"] == "2003.05.05"
    assert out["data_setups"][0]["chart"] == {
        "symbol": "GBPUSD",
        "timeframe": "H1",
        "spread": "3",
    }
    assert out["out_of_sample"] == {"showGraph": "false"}


def test_inspect_task_xml_returns_error_on_bad_xml() -> None:
    assert "error" in _inspect_task_xml(b"<not xml>")
