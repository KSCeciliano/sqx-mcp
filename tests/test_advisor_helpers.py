"""Unit tests for advisor heuristics."""

from __future__ import annotations

from sq_mcp.tools.advisor import _infer_asset_class, _recommend

# ---- _infer_asset_class --------------------------------------------------


def test_infer_btcusdt_is_crypto() -> None:
    assert _infer_asset_class("BTCUSDT") == "crypto"
    assert _infer_asset_class("ETHUSDT") == "crypto"
    assert _infer_asset_class("solusdc") == "crypto"


def test_infer_eurusd_is_forex() -> None:
    assert _infer_asset_class("EURUSD") == "forex"
    assert _infer_asset_class("GBPJPY") == "forex"


def test_infer_futures() -> None:
    assert _infer_asset_class("ES") == "futures"
    assert _infer_asset_class("GC") == "futures"


def test_infer_unknown() -> None:
    assert _infer_asset_class("FOOBAR") == "unknown"
    # Not crypto (no crypto suffix, no crypto base) and not 6 letters forex
    assert _infer_asset_class("FOOBARBAZ") == "unknown"


# ---- _recommend ----------------------------------------------------------


def test_recommend_yield_crypto_is_aggressive() -> None:
    out = _recommend("yield", "crypto")
    s = out["settings"]
    assert s["fitness_criterion"] == "NetProfit"
    assert s["population_size"] == 200
    assert s["mm_params"]["RiskedMoney"] == "2.0"
    # Crypto-specific overrides
    assert s["exit_on_friday"] is False
    assert s["slpt_value_type"] == "percent"


def test_recommend_risk_min_balanced_safety() -> None:
    out = _recommend("risk_min", "forex")
    s = out["settings"]
    assert s["fitness_criterion"] == "ReturnDDRatio"
    assert s["mm_params"]["RiskedMoney"] == "0.5"
    assert s["min_trades"] == 100  # tighter sample requirement


def test_recommend_smoke_minimal() -> None:
    out = _recommend("smoke", "unknown")
    s = out["settings"]
    assert s["population_size"] == 20
    assert s["max_generations"] == 10
    assert s["max_strategies"] == 20


def test_recommend_balanced_default() -> None:
    out = _recommend("balanced", "unknown")
    s = out["settings"]
    assert s["population_size"] == 100
    assert s["fitness_criterion"] == "ReturnDDRatio"
    assert s["mm_params"]["RiskedMoney"] == "1.0"


def test_recommend_crypto_adds_24_7_notes() -> None:
    out = _recommend("balanced", "crypto")
    assert any("24/7" in n for n in out["notes"])
    assert out["settings"]["slpt_value_type"] == "percent"


def test_recommend_forex_keeps_friday_exit() -> None:
    out = _recommend("yield", "forex")
    s = out["settings"]
    assert s["exit_on_friday"] is True
    assert s["slpt_value_type"] == "pips"
