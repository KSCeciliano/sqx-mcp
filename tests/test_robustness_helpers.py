"""Unit tests for Walk-Forward & Robustness XML patcher helpers."""

from __future__ import annotations

from lxml import etree

from sq_mcp.tools.robustness import (
    _patch_robustness_in_xml,
    _patch_walkforward_in_xml,
)

_RETEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Settings>
  <CrossChecks>
    <RetestWithHigherPrecision use="false"><Settings/></RetestWithHigherPrecision>
    <MonteCarloRetest use="false">
      <Settings><Methods></Methods></Settings>
    </MonteCarloRetest>
    <MonteCarloManipulation use="false">
      <Settings><Methods></Methods></Settings>
    </MonteCarloManipulation>
    <WalkForwardOptimization use="false">
      <Settings>
        <WalkForward type="1" period="10" optimization="15">
          <Param1 value="20"/>
          <Param2 value="10"/>
        </WalkForward>
      </Settings>
    </WalkForwardOptimization>
    <WalkForwardMatrix use="false">
      <Settings/>
    </WalkForwardMatrix>
    <WhatIf use="false"/>
  </CrossChecks>
</Settings>
"""

_NO_WF_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Settings>
  <CrossChecks>
    <MonteCarloRetest use="false"><Settings/></MonteCarloRetest>
  </CrossChecks>
</Settings>
"""


def test_patch_walkforward_enables_and_sets_type_rolling() -> None:
    new_bytes, changes, present = _patch_walkforward_in_xml(
        _RETEST_XML, enable=True, wf_type="rolling", period_oos=20, period_is=40
    )
    assert present is True
    assert changes["use"] == 1
    assert changes["wf_type"] == 1
    assert changes["wf_period_oos"] == 1
    assert changes["wf_period_is"] == 1
    root = etree.fromstring(new_bytes)
    wf_outer = root.find(".//WalkForwardOptimization")
    assert wf_outer.get("use") == "true"
    inner = wf_outer.find("Settings/WalkForward")
    assert inner.get("type") == "2"  # rolling
    assert inner.get("period") == "20"
    assert inner.get("optimization") == "40"


def test_patch_walkforward_creates_inner_when_missing() -> None:
    """When the project has the outer block but no <Settings><WalkForward/>,
    the patcher creates it so the engine sees parameters when use=true."""
    outer_only = b"""<?xml version="1.0"?>
    <Settings><CrossChecks>
      <WalkForwardOptimization use="false"/>
    </CrossChecks></Settings>"""
    new_bytes, changes, present = _patch_walkforward_in_xml(
        outer_only, enable=True, wf_type="anchored", period_oos=10, period_is=20
    )
    assert present is True
    assert changes["wf_block_created"] == 1
    root = etree.fromstring(new_bytes)
    inner = root.find(".//WalkForwardOptimization/Settings/WalkForward")
    assert inner is not None
    assert inner.get("type") == "1"  # anchored
    assert inner.get("period") == "10"
    assert inner.get("optimization") == "20"


def test_patch_walkforward_returns_present_false_on_non_retest_xml() -> None:
    new_bytes, changes, present = _patch_walkforward_in_xml(
        _NO_WF_XML, enable=True, wf_type="rolling", period_oos=10, period_is=20
    )
    assert present is False
    assert changes == {}  # no changes
    assert new_bytes == _NO_WF_XML  # unchanged


def test_patch_walkforward_disable_only_changes_use() -> None:
    enabled_xml = _RETEST_XML.replace(
        b'<WalkForwardOptimization use="false">',
        b'<WalkForwardOptimization use="true">',
    )
    new_bytes, changes, _ = _patch_walkforward_in_xml(
        enabled_xml, enable=False, wf_type=None, period_oos=None, period_is=None
    )
    root = etree.fromstring(new_bytes)
    assert root.find(".//WalkForwardOptimization").get("use") == "false"
    assert changes["use"] == 1
    assert "wf_type" not in changes


def test_patch_walkforward_idempotent_when_already_correct() -> None:
    """Re-applying the same patch should report zero changes."""
    new_bytes, _, _ = _patch_walkforward_in_xml(
        _RETEST_XML, enable=True, wf_type="anchored", period_oos=10, period_is=15
    )
    new_bytes2, changes2, _ = _patch_walkforward_in_xml(
        new_bytes, enable=True, wf_type="anchored", period_oos=10, period_is=15
    )
    assert changes2 == {}  # idempotent


def test_patch_robustness_toggles_known_blocks() -> None:
    new_bytes, counter, touched, not_found = _patch_robustness_in_xml(
        _RETEST_XML, enable_map={"MonteCarloRetest": True, "MonteCarloManipulation": True, "WhatIf": True}
    )
    root = etree.fromstring(new_bytes)
    assert root.find(".//MonteCarloRetest").get("use") == "true"
    assert root.find(".//MonteCarloManipulation").get("use") == "true"
    assert root.find(".//WhatIf").get("use") == "true"
    assert counter["MonteCarloRetest"] == 1
    assert counter["MonteCarloManipulation"] == 1
    assert counter["WhatIf"] == 1
    assert "MonteCarloRetest=on" in touched
    assert not_found == []


def test_patch_robustness_reports_missing_blocks() -> None:
    _, counter, _, not_found = _patch_robustness_in_xml(
        _NO_WF_XML, enable_map={"WalkForwardOptimization": True, "WhatIf": True, "MonteCarloRetest": True}
    )
    # MonteCarloRetest exists in _NO_WF_XML, the other two don't.
    assert "WalkForwardOptimization" in not_found
    assert "WhatIf" in not_found
    assert "MonteCarloRetest" not in not_found
    assert counter["MonteCarloRetest"] == 1


def test_patch_robustness_skip_when_already_matching() -> None:
    new_bytes, _, _, _ = _patch_robustness_in_xml(
        _RETEST_XML, enable_map={"MonteCarloRetest": True}
    )
    _, counter2, touched2, _ = _patch_robustness_in_xml(
        new_bytes, enable_map={"MonteCarloRetest": True}
    )
    assert counter2 == {}
    assert touched2 == []
