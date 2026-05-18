"""Unit tests for exposure helpers."""

from __future__ import annotations

from sq_mcp.tools.exposure import (
    _correlation_risk,
    _exposure_hhi,
    _exposure_summary,
    _leverage_check,
    _var_decomposition,
)


def test_summary_long_only() -> None:
    positions = [
        {"symbol": "BTC", "side": "long", "notional": 10_000},
        {"symbol": "ETH", "side": "long", "notional": 5_000},
    ]
    r = _exposure_summary(positions)
    assert r["net_exposure"] == 15_000
    assert r["gross_exposure"] == 15_000
    assert r["n_symbols"] == 2
    assert r["directional_bias"] == "strongly_long"


def test_summary_mixed_long_short() -> None:
    positions = [
        {"symbol": "BTC", "side": "long", "notional": 10_000},
        {"symbol": "ETH", "side": "short", "notional": 10_000},
    ]
    r = _exposure_summary(positions)
    assert r["net_exposure"] == 0
    assert r["gross_exposure"] == 20_000
    assert r["directional_bias"] == "balanced"


def test_summary_per_symbol_aggregated() -> None:
    positions = [
        {"symbol": "BTC", "side": "long", "notional": 10_000},
        {"symbol": "BTC", "side": "long", "notional": 5_000},
    ]
    r = _exposure_summary(positions)
    assert r["by_symbol"]["BTC"]["net"] == 15_000
    assert r["by_symbol"]["BTC"]["long"] == 15_000


def test_hhi_concentrated_single_symbol() -> None:
    positions = [{"symbol": "BTC", "side": "long", "notional": 100_000}]
    r = _exposure_hhi(positions)
    assert r["normalized_hhi"] == 1.0
    assert r["verdict"] == "very_concentrated"


def test_hhi_diversified_equal_weights() -> None:
    positions = [
        {"symbol": f"S{i}", "side": "long", "notional": 1_000} for i in range(10)
    ]
    r = _exposure_hhi(positions)
    assert r["normalized_hhi"] < 0.05
    assert r["verdict"] == "diversified"


def test_leverage_check_safe() -> None:
    positions = [{"symbol": "BTC", "side": "long", "notional": 50_000}]
    r = _leverage_check(positions, equity=100_000, max_lev=2.0)
    assert r["gross_leverage"] == 0.5
    assert r["breach"] is False
    assert r["verdict"] == "safe"


def test_leverage_check_breach() -> None:
    positions = [{"symbol": "BTC", "side": "long", "notional": 300_000}]
    r = _leverage_check(positions, equity=100_000, max_lev=2.0)
    assert r["gross_leverage"] == 3.0
    assert r["breach"] is True
    assert r["verdict"] in {"breach", "critical_breach"}


def test_leverage_check_near_limit() -> None:
    positions = [{"symbol": "BTC", "side": "long", "notional": 195_000}]
    r = _leverage_check(positions, equity=100_000, max_lev=2.0)
    assert r["verdict"] == "near_limit"


def test_correlation_risk_uncorrelated_high_diversification() -> None:
    positions = [
        {"symbol": "A", "side": "long", "notional": 1_000},
        {"symbol": "B", "side": "long", "notional": 1_000},
        {"symbol": "C", "side": "long", "notional": 1_000},
    ]
    corr = {"A": {}, "B": {}, "C": {}}  # all default ρ=0
    r = _correlation_risk(positions, corr)
    # Equal weights, zero off-diagonal correlation → eff = sqrt(Σ w² · 1) = 1/sqrt(3)
    assert r["verdict"] == "strong_diversification"


def test_correlation_risk_perfectly_correlated() -> None:
    positions = [
        {"symbol": "A", "side": "long", "notional": 1_000},
        {"symbol": "B", "side": "long", "notional": 1_000},
    ]
    corr = {"A": {"B": 1.0}, "B": {"A": 1.0}}
    r = _correlation_risk(positions, corr)
    assert r["verdict"] == "highly_correlated"
    assert r["effective_exposure_fraction"] >= 0.99


def test_var_decomposition_sums_to_total() -> None:
    positions = [
        {"symbol": "A", "side": "long", "notional": 10_000},
        {"symbol": "B", "side": "long", "notional": 5_000},
    ]
    vols = {"A": 0.02, "B": 0.04}
    corr = {"A": {"B": 0.3}, "B": {"A": 0.3}}
    r = _var_decomposition(positions, vols, corr, confidence=0.95)
    assert r["portfolio_var"] > 0
    total_share = sum(c["share_pct"] for c in r["contributions"])
    assert abs(total_share - 100.0) < 1e-6
