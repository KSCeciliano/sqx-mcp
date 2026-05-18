"""Unit tests for cross_instrument helpers."""

from __future__ import annotations

from sq_mcp.tools.cross_instrument import (
    _asset_class_split,
    _classify_asset,
    _correlation_groups,
    _diversification_score,
    _group_by_symbol,
    _recommend_caps,
)

# ---- _classify_asset -------------------------------------------------------


def test_classify_crypto_usdt() -> None:
    assert _classify_asset("BTCUSDT") == "crypto"


def test_classify_crypto_usd() -> None:
    assert _classify_asset("ETHUSD") == "crypto"


def test_classify_forex_eurusd() -> None:
    assert _classify_asset("EURUSD") == "forex"


def test_classify_forex_with_slash() -> None:
    assert _classify_asset("EUR/USD") == "forex"


def test_classify_futures_es() -> None:
    assert _classify_asset("ES") == "futures"


def test_classify_equities_spy() -> None:
    assert _classify_asset("SPY") == "equities"


def test_classify_unknown() -> None:
    assert _classify_asset("ZZZ") == "unknown"


# ---- _group_by_symbol ------------------------------------------------------


def _row(name: str, symbol: str, weight: float = 1.0, fitness: float | None = None) -> dict:
    return {"name": name, "symbol": symbol, "weight": weight, "fitness": fitness}


def test_group_by_symbol_basic() -> None:
    rows = [
        _row("a", "BTCUSDT"),
        _row("b", "BTCUSDT", weight=2.0),
        _row("c", "ETHUSDT", weight=1.0),
    ]
    out = _group_by_symbol(rows)
    assert out["n_unique_symbols"] == 2
    by_sym = {g["symbol"]: g for g in out["groups"]}
    assert by_sym["BTCUSDT"]["n_strategies"] == 2
    assert by_sym["BTCUSDT"]["total_weight"] == 3.0


def test_group_by_symbol_sort_by_weight() -> None:
    rows = [
        _row("a", "minor", weight=1.0),
        _row("b", "major", weight=10.0),
    ]
    out = _group_by_symbol(rows)
    assert out["groups"][0]["symbol"] == "major"


def test_group_by_symbol_mean_fitness() -> None:
    rows = [
        _row("a", "X", fitness=1.0),
        _row("b", "X", fitness=3.0),
    ]
    out = _group_by_symbol(rows)
    assert out["groups"][0]["mean_fitness"] == 2.0


# ---- _diversification_score -----------------------------------------------


def test_diversification_single_symbol_zero() -> None:
    rows = [_row("a", "BTCUSDT")]
    out = _diversification_score(rows)
    assert out["score"] == 0
    assert out["verdict"] == "single_symbol"


def test_diversification_two_equal_weight_high_score() -> None:
    rows = [_row("a", "BTC", weight=1.0), _row("b", "ETH", weight=1.0)]
    out = _diversification_score(rows)
    # 50/50 split is maximum diversification for n=2
    assert out["score"] >= 99


def test_diversification_concentrated_low_score() -> None:
    rows = [_row("a", "BTC", weight=99.0)] + [_row(f"x{i}", "ETH", weight=0.01) for i in range(5)]
    out = _diversification_score(rows)
    assert out["score"] < 40
    assert out["verdict"] == "concentrated"


# ---- _recommend_caps -------------------------------------------------------


def test_recommend_caps_fits() -> None:
    rows = [_row("a", "BTC", weight=1.0), _row("b", "ETH", weight=1.0)]
    out = _recommend_caps(rows, cap_pct=60.0)
    assert out["fits_caps"] is True
    assert out["n_violations"] == 0


def test_recommend_caps_violates() -> None:
    rows = [_row("a", "BTC", weight=9.0), _row("b", "ETH", weight=1.0)]
    # BTC is 90% > 25% cap
    out = _recommend_caps(rows, cap_pct=25.0)
    assert out["fits_caps"] is False
    assert out["n_violations"] == 1
    assert out["violators"][0]["symbol"] == "BTC"


# ---- _asset_class_split ----------------------------------------------------


def test_asset_class_split_mixed() -> None:
    rows = [
        _row("a", "BTCUSDT"),
        _row("b", "ETHUSDT"),
        _row("c", "EURUSD"),
        _row("d", "SPY"),
    ]
    out = _asset_class_split(rows)
    classes = {b["asset_class"] for b in out["buckets"]}
    assert "crypto" in classes
    assert "forex" in classes
    assert "equities" in classes


def test_asset_class_split_all_crypto() -> None:
    rows = [_row("a", "BTCUSDT"), _row("b", "ETHUSDT")]
    out = _asset_class_split(rows)
    assert out["n_asset_classes"] == 1
    assert out["buckets"][0]["asset_class"] == "crypto"


# ---- _correlation_groups ---------------------------------------------------


def test_correlation_groups_crypto_majors() -> None:
    rows = [
        _row("a", "BTCUSDT"),
        _row("b", "ETHUSDT"),
        _row("c", "SOLUSDT"),  # alt
    ]
    out = _correlation_groups(rows)
    clusters = {c["cluster"]: c for c in out["clusters"]}
    assert "crypto_majors" in clusters
    assert clusters["crypto_majors"]["n_strategies"] == 2
    assert "crypto_alts" in clusters


def test_correlation_groups_forex_split() -> None:
    rows = [
        _row("a", "EURUSD"),  # usd_quote
        _row("b", "GBPUSD"),
        _row("c", "USDJPY"),  # jpy
    ]
    out = _correlation_groups(rows)
    clusters = {c["cluster"]: c for c in out["clusters"]}
    assert "forex_usd_quote" in clusters
    assert "forex_jpy" in clusters
