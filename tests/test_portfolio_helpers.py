"""Unit tests for portfolio analysis helpers."""

from __future__ import annotations

from sq_mcp.tools.portfolio import (
    _composite_score,
    _filter_min_trades,
    _group_by,
    _pick_best_in_group,
    _rank_rows,
    _select_diverse,
    _summary_for_rows,
)

# ---- _composite_score -----------------------------------------------------


def test_composite_score_returns_none_when_no_fitness() -> None:
    assert _composite_score({}) is None
    assert _composite_score({"fitness_oos": None, "fitness_full": None, "fitness_is": None}) is None


def test_composite_score_uses_oos_first() -> None:
    # OOS=0.5, OOS/IS=0.8, DD=10% → no penalties beyond base
    score = _composite_score(
        {"fitness_oos": 0.5, "oos_is_ratio": 0.8, "drawdown_pct": 10.0, "trades": 200}
    )
    assert score is not None
    # OOS ratio 0.8 → multiplied by min(1.0, 1.6) = 1.0 (capped). No DD/trade penalty.
    assert abs(score - 0.5) < 1e-6


def test_composite_score_penalizes_overfit() -> None:
    base = _composite_score(
        {"fitness_oos": 1.0, "oos_is_ratio": 0.8, "drawdown_pct": 10.0, "trades": 200}
    )
    overfit = _composite_score(
        {"fitness_oos": 1.0, "oos_is_ratio": 0.2, "drawdown_pct": 10.0, "trades": 200}
    )
    assert base is not None and overfit is not None
    assert overfit < base


def test_composite_score_penalizes_high_drawdown() -> None:
    base = _composite_score(
        {"fitness_oos": 1.0, "oos_is_ratio": 0.8, "drawdown_pct": 10.0, "trades": 200}
    )
    deep_dd = _composite_score(
        {"fitness_oos": 1.0, "oos_is_ratio": 0.8, "drawdown_pct": 50.0, "trades": 200}
    )
    assert base is not None and deep_dd is not None
    assert deep_dd < base
    # 50% DD → factor = 25/50 = 0.5
    assert abs(deep_dd - 0.5) < 1e-6


def test_composite_score_penalizes_few_trades() -> None:
    base = _composite_score(
        {"fitness_oos": 1.0, "oos_is_ratio": 0.8, "drawdown_pct": 10.0, "trades": 200}
    )
    sparse = _composite_score(
        {"fitness_oos": 1.0, "oos_is_ratio": 0.8, "drawdown_pct": 10.0, "trades": 25}
    )
    assert base is not None and sparse is not None
    assert sparse < base
    # 25 trades → factor max(0.1, 25/100) = 0.25
    assert abs(sparse - 0.25) < 1e-6


def test_composite_score_floor_for_tiny_trades() -> None:
    score = _composite_score(
        {"fitness_oos": 1.0, "oos_is_ratio": 0.8, "drawdown_pct": 10.0, "trades": 1}
    )
    # floor 0.1 ensures we don't crash to zero
    assert score is not None
    assert score >= 0.1 - 1e-6


# ---- _pick_best_in_group --------------------------------------------------


def test_pick_best_in_group_by_oos() -> None:
    grp = [
        {"rel": "a.sqx", "fitness_oos": 0.5, "profit_to_dd_ratio": 1.0},
        {"rel": "b.sqx", "fitness_oos": 0.8, "profit_to_dd_ratio": 0.5},
        {"rel": "c.sqx", "fitness_oos": 0.3, "profit_to_dd_ratio": 2.0},
    ]
    assert _pick_best_in_group(grp, "best_oos")["rel"] == "b.sqx"


def test_pick_best_in_group_by_profit_to_dd() -> None:
    grp = [
        {"rel": "a.sqx", "fitness_oos": 0.5, "profit_to_dd_ratio": 1.0},
        {"rel": "b.sqx", "fitness_oos": 0.8, "profit_to_dd_ratio": 0.5},
        {"rel": "c.sqx", "fitness_oos": 0.3, "profit_to_dd_ratio": 2.0},
    ]
    assert _pick_best_in_group(grp, "best_profit_to_dd")["rel"] == "c.sqx"


def test_pick_best_in_group_first() -> None:
    grp = [
        {"rel": "c.sqx", "fitness_oos": 0.5},
        {"rel": "a.sqx", "fitness_oos": 0.8},
        {"rel": "b.sqx", "fitness_oos": 0.3},
    ]
    assert _pick_best_in_group(grp, "first")["rel"] == "a.sqx"


def test_pick_best_in_group_handles_missing_metric() -> None:
    grp = [
        {"rel": "a.sqx", "fitness_oos": None},
        {"rel": "b.sqx", "fitness_oos": 0.5},
    ]
    assert _pick_best_in_group(grp, "best_oos")["rel"] == "b.sqx"


# ---- _rank_rows -----------------------------------------------------------


def test_rank_rows_descending_by_fitness_oos() -> None:
    rows = [
        {"rel": "low.sqx", "fitness_oos": 0.2},
        {"rel": "high.sqx", "fitness_oos": 0.9},
        {"rel": "mid.sqx", "fitness_oos": 0.5},
    ]
    ranked = _rank_rows(rows, "fitness_oos")
    assert [r["rel"] for r in ranked] == ["high.sqx", "mid.sqx", "low.sqx"]


def test_rank_rows_ascending_for_lowest_drawdown() -> None:
    rows = [
        {"rel": "deep.sqx", "drawdown_pct": 40.0},
        {"rel": "small.sqx", "drawdown_pct": 5.0},
        {"rel": "mid.sqx", "drawdown_pct": 20.0},
    ]
    ranked = _rank_rows(rows, "lowest_drawdown_pct")
    assert [r["rel"] for r in ranked] == ["small.sqx", "mid.sqx", "deep.sqx"]


def test_rank_rows_sinks_missing_metrics() -> None:
    rows = [
        {"rel": "no_metric.sqx", "fitness_oos": None},
        {"rel": "has_metric.sqx", "fitness_oos": 0.5},
    ]
    ranked = _rank_rows(rows, "fitness_oos")
    assert ranked[0]["rel"] == "has_metric.sqx"
    assert ranked[1]["rel"] == "no_metric.sqx"


def test_rank_rows_defensive_attaches_composite_score() -> None:
    rows = [
        {"rel": "a.sqx", "fitness_oos": 0.5, "oos_is_ratio": 0.8, "drawdown_pct": 10.0, "trades": 200},
        {"rel": "b.sqx", "fitness_oos": 0.5, "oos_is_ratio": 0.1, "drawdown_pct": 60.0, "trades": 5},
    ]
    ranked = _rank_rows(rows, "defensive")
    # a (clean) should outrank b (very overfit + deep DD + tiny sample)
    assert ranked[0]["rel"] == "a.sqx"
    assert all("composite_score" in r for r in ranked)


# ---- _filter_min_trades ---------------------------------------------------


def test_filter_min_trades_passthrough_when_none() -> None:
    rows = [{"trades": 5}, {"trades": 100}]
    assert _filter_min_trades(rows, None) == rows


def test_filter_min_trades_drops_below_threshold() -> None:
    rows = [{"rel": "a", "trades": 5}, {"rel": "b", "trades": 50}, {"rel": "c", "trades": 100}]
    filtered = _filter_min_trades(rows, 50)
    assert [r["rel"] for r in filtered] == ["b", "c"]


def test_filter_min_trades_treats_none_trades_as_zero() -> None:
    rows = [{"rel": "a", "trades": None}, {"rel": "b", "trades": 10}]
    assert [r["rel"] for r in _filter_min_trades(rows, 5)] == ["b"]


# ---- _group_by ------------------------------------------------------------


def test_group_by_trades_hash() -> None:
    rows = [
        {"rel": "a", "trades_hash": "H1"},
        {"rel": "b", "trades_hash": "H1"},
        {"rel": "c", "trades_hash": "H2"},
        {"rel": "d", "trades_hash": None},
    ]
    groups = _group_by(rows, "trades_hash")
    assert len(groups["H1"]) == 2
    assert len(groups["H2"]) == 1
    # rows without a key bucket under '__no_key__'
    assert len(groups["__no_key__"]) == 1


def test_group_by_symbol_tf() -> None:
    rows = [
        {"rel": "a", "symbol": "BTCUSDT", "timeframe": "M1"},
        {"rel": "b", "symbol": "BTCUSDT", "timeframe": "H1"},
        {"rel": "c", "symbol": "BTCUSDT", "timeframe": "M1"},
    ]
    groups = _group_by(rows, "symbol_tf")
    assert sorted(groups.keys()) == ["BTCUSDT_H1", "BTCUSDT_M1"]
    assert len(groups["BTCUSDT_M1"]) == 2


# ---- _select_diverse ------------------------------------------------------


def test_select_diverse_picks_from_each_bucket_first() -> None:
    rows = [
        {"rel": "h1_best", "trades_hash": "H1", "fitness_oos": 0.9},
        {"rel": "h1_worse", "trades_hash": "H1", "fitness_oos": 0.4},
        {"rel": "h2_best", "trades_hash": "H2", "fitness_oos": 0.85},
        {"rel": "h2_worse", "trades_hash": "H2", "fitness_oos": 0.3},
    ]
    picks = _select_diverse(rows, n=2, rank_mode="fitness_oos", diversify_by="trades_hash")
    # Top of each bucket, not the top two from one bucket
    rels = {p["rel"] for p in picks}
    assert rels == {"h1_best", "h2_best"}


def test_select_diverse_caps_at_available() -> None:
    rows = [
        {"rel": "only_one", "trades_hash": "H1", "fitness_oos": 0.5},
    ]
    picks = _select_diverse(rows, n=10, rank_mode="fitness_oos", diversify_by="trades_hash")
    assert len(picks) == 1


def test_select_diverse_continues_into_buckets_when_n_exceeds_groups() -> None:
    rows = [
        {"rel": "a_top", "trades_hash": "H1", "fitness_oos": 0.9},
        {"rel": "a_second", "trades_hash": "H1", "fitness_oos": 0.7},
        {"rel": "b_top", "trades_hash": "H2", "fitness_oos": 0.8},
    ]
    picks = _select_diverse(rows, n=3, rank_mode="fitness_oos", diversify_by="trades_hash")
    rels = [p["rel"] for p in picks]
    # First pass: a_top, b_top. Second pass picks a_second.
    assert rels[0] in ("a_top", "b_top")
    assert "a_second" in rels


# ---- _summary_for_rows ----------------------------------------------------


def test_summary_for_empty_rows() -> None:
    assert _summary_for_rows([]) == {"strategies": 0}


def test_summary_for_rows_counts_and_aggregates() -> None:
    rows = [
        {
            "rel": "a",
            "trades": 100,
            "net_profit": 500.0,
            "fitness_oos": 0.5,
            "drawdown_pct": 10.0,
            "profit_to_dd_ratio": 5.0,
            "oos_is_ratio": 0.8,
            "history_years": 3.0,
            "trades_hash": "H1",
            "fingerprint_exact": "FP1",
            "symbol": "BTCUSDT",
            "timeframe": "H1",
        },
        {
            "rel": "b",
            "trades": 50,
            "net_profit": -100.0,
            "fitness_oos": 0.3,
            "drawdown_pct": 30.0,
            "profit_to_dd_ratio": 0.5,
            "oos_is_ratio": 0.3,
            "history_years": 3.0,
            "trades_hash": "H1",
            "fingerprint_exact": "FP2",
            "symbol": "BTCUSDT",
            "timeframe": "H1",
        },
    ]
    s = _summary_for_rows(rows)
    assert s["strategies"] == 2
    assert s["profitable_count"] == 1
    assert s["unique_trades_hashes"] == 1  # both share H1
    assert s["unique_exact_fingerprints"] == 2
    assert s["duplicate_rate_trades_hash"] == 0.5  # 1 unique / 2 rows → 50% dup
    assert s["overfit_oos_under_0p5"] == 1  # b has oos_is_ratio=0.3
    assert s["by_symbol"] == {"BTCUSDT": 2}
    assert s["by_timeframe"] == {"H1": 2}
    assert s["trades_total"] == 150
    assert s["trades"]["count"] == 2
    assert s["trades"]["mean"] == 75
