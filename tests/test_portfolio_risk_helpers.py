"""Unit tests for portfolio_risk helpers."""

from __future__ import annotations

from sq_mcp.tools.portfolio_risk import (
    _allocation_weights,
    _bucket_counts,
    _distribution_stats,
    _hhi,
)

# ---- _distribution_stats --------------------------------------------------


def test_distribution_stats_empty() -> None:
    s = _distribution_stats([])
    assert s["count"] == 0
    assert s["mean"] is None
    assert s["min"] is None


def test_distribution_stats_single_value() -> None:
    s = _distribution_stats([10.0])
    assert s["count"] == 1
    assert s["mean"] == 10.0
    assert s["median"] == 10.0
    assert s["min"] == 10.0
    assert s["max"] == 10.0
    # percentiles collapse to the only value
    assert s["p10"] == 10.0
    assert s["p99"] == 10.0


def test_distribution_stats_skips_none_and_nan() -> None:
    s = _distribution_stats([1.0, None, 2.0, float("nan"), 3.0])
    assert s["count"] == 3
    assert s["mean"] == 2.0


def test_distribution_stats_percentiles_monotonic() -> None:
    s = _distribution_stats(list(range(101)))  # 0..100
    assert s["p10"] == 10
    assert s["p25"] == 25
    assert s["p50"] if s.get("p50") else s["median"] == 50
    assert s["p75"] == 75
    assert s["p90"] == 90
    assert s["p95"] == 95
    assert s["p99"] == 99


# ---- _hhi ------------------------------------------------------------------


def test_hhi_single_bucket_is_one() -> None:
    out = _hhi({"only": 10})
    assert out["hhi"] == 1.0
    # Normalized HHI is None when there's only 1 bucket (no diversity reference)
    assert out["hhi_normalized"] is None
    assert out["unique_buckets"] == 1
    assert out["total"] == 10


def test_hhi_even_split_normalizes_to_zero() -> None:
    out = _hhi({"a": 5, "b": 5, "c": 5, "d": 5})
    # 4 buckets equally → HHI = 4 * (0.25)^2 = 0.25, normalized → 0
    assert out["hhi"] == 0.25
    assert out["hhi_normalized"] == 0.0
    assert out["unique_buckets"] == 4


def test_hhi_concentrated_normalizes_high() -> None:
    out = _hhi({"big": 90, "small": 10})
    # HHI = 0.9^2 + 0.1^2 = 0.82
    assert abs(out["hhi"] - 0.82) < 1e-6
    # Normalized: (0.82 - 0.5) / (1 - 0.5) = 0.64
    assert abs(out["hhi_normalized"] - 0.64) < 1e-6


def test_hhi_empty_counts() -> None:
    out = _hhi({})
    assert out["hhi"] is None
    assert out["unique_buckets"] == 0
    assert out["total"] == 0


# ---- _bucket_counts -------------------------------------------------------


def test_bucket_counts_trades_hash() -> None:
    rows = [
        {"trades_hash": "H1"},
        {"trades_hash": "H1"},
        {"trades_hash": "H2"},
    ]
    out = _bucket_counts(rows, "trades_hash")
    assert out == {"H1": 2, "H2": 1}


def test_bucket_counts_symbol_tf_combo() -> None:
    rows = [
        {"symbol": "BTCUSDT", "timeframe": "M1"},
        {"symbol": "BTCUSDT", "timeframe": "M1"},
        {"symbol": "BTCUSDT", "timeframe": "H1"},
    ]
    out = _bucket_counts(rows, "symbol_tf")
    assert out["BTCUSDT_M1"] == 2
    assert out["BTCUSDT_H1"] == 1


def test_bucket_counts_handles_missing_key() -> None:
    rows = [{"trades_hash": None}, {"trades_hash": "H"}]
    out = _bucket_counts(rows, "trades_hash")
    assert "__missing__" in out
    assert out["__missing__"] == 1
    assert out["H"] == 1


# ---- _allocation_weights --------------------------------------------------


def test_allocation_weights_sum_to_one() -> None:
    rows = [
        {"rel": "a", "drawdown_pct": 10.0, "fitness_oos": 0.5},
        {"rel": "b", "drawdown_pct": 20.0, "fitness_oos": 0.3},
        {"rel": "c", "drawdown_pct": 5.0, "fitness_oos": 0.4},
    ]
    out = _allocation_weights(rows, fitness_bias=0.5)
    assert len(out) == 3
    total = sum(o["weight_blended"] for o in out)
    assert abs(total - 1.0) < 1e-3


def test_allocation_weights_lower_dd_gets_more_with_zero_fitness_bias() -> None:
    rows = [
        {"rel": "low_dd", "drawdown_pct": 5.0, "fitness_oos": 0.1},
        {"rel": "high_dd", "drawdown_pct": 50.0, "fitness_oos": 0.9},
    ]
    # Pure inverse-DD: low_dd should win even though its fitness is worse
    out = _allocation_weights(rows, fitness_bias=0.0)
    low = next(o for o in out if o["rel"] == "low_dd")
    high = next(o for o in out if o["rel"] == "high_dd")
    assert low["weight_blended"] > high["weight_blended"]


def test_allocation_weights_higher_fitness_wins_when_bias_is_one() -> None:
    rows = [
        {"rel": "low_dd", "drawdown_pct": 5.0, "fitness_oos": 0.1},
        {"rel": "high_fit", "drawdown_pct": 50.0, "fitness_oos": 0.9},
    ]
    out = _allocation_weights(rows, fitness_bias=1.0)
    high = next(o for o in out if o["rel"] == "high_fit")
    low = next(o for o in out if o["rel"] == "low_dd")
    assert high["weight_blended"] > low["weight_blended"]


def test_allocation_weights_sorted_by_blended() -> None:
    rows = [
        {"rel": "z", "drawdown_pct": 1.0, "fitness_oos": 1.0},
        {"rel": "a", "drawdown_pct": 100.0, "fitness_oos": 0.0},
    ]
    out = _allocation_weights(rows, fitness_bias=0.5)
    assert out[0]["weight_blended"] >= out[1]["weight_blended"]


# ---- _diversity_score -----------------------------------------------------

from sq_mcp.tools.portfolio_risk import _diversity_score  # noqa: E402


def test_diversity_score_empty_rows() -> None:
    out = _diversity_score([])
    assert out["score"] == 0
    assert out["tier"] == "empty"


def test_diversity_score_perfect_diversity() -> None:
    # 20 strategies, all different hashes, symbols, fingerprints → diversity ~100
    rows = [
        {
            "trades_hash": f"H{i}",
            "fingerprint_exact": f"FP{i}",
            "symbol": f"SYM{i}",
            "timeframe": f"TF{i % 2}",
        }
        for i in range(20)
    ]
    out = _diversity_score(rows)
    # 20 distinct hashes/FPs across 20 rows → normalized HHI = 0 → sub-score = 100
    assert out["score"] >= 95
    assert out["tier"] == "excellent"


def test_diversity_score_zero_diversity_one_bucket() -> None:
    # 20 strategies, all sharing every bucket → diversity ~0
    rows = [
        {
            "trades_hash": "SAME",
            "fingerprint_exact": "FP",
            "symbol": "BTCUSDT",
            "timeframe": "H1",
        }
        for _ in range(20)
    ]
    out = _diversity_score(rows)
    # All in same bucket → no diversity to measure (normalized HHI is None for n=1)
    # Falls back to neutral 50
    assert 40 <= out["score"] <= 60


def test_diversity_score_mid_range() -> None:
    # Skewed distribution: H1 dominates → diversity is below perfect
    rows = []
    # 10× H1, 5× H2, 3× H3, 2× H4
    for _ in range(10):
        rows.append({"trades_hash": "H1", "fingerprint_exact": "FP1", "symbol": "S1", "timeframe": "H1"})
    for _ in range(5):
        rows.append({"trades_hash": "H2", "fingerprint_exact": "FP2", "symbol": "S2", "timeframe": "H1"})
    for _ in range(3):
        rows.append({"trades_hash": "H3", "fingerprint_exact": "FP3", "symbol": "S3", "timeframe": "H1"})
    for _ in range(2):
        rows.append({"trades_hash": "H4", "fingerprint_exact": "FP4", "symbol": "S4", "timeframe": "H1"})
    out = _diversity_score(rows)
    # Should NOT be perfect (skewed distribution)
    assert out["score"] < 100
    # But also should not be terrible (4 distinct buckets exist)
    assert out["score"] > 0


# ---- _metric_pair_pearson -----------------------------------------------

from sq_mcp.tools.portfolio_risk import _metric_pair_pearson  # noqa: E402


def test_metric_pair_pearson_perfect_positive() -> None:
    rows = [
        {"fitness": 1.0, "drawdown": 1.0},
        {"fitness": 2.0, "drawdown": 2.0},
        {"fitness": 3.0, "drawdown": 3.0},
        {"fitness": 4.0, "drawdown": 4.0},
    ]
    out = _metric_pair_pearson(rows, metric_a="fitness", metric_b="drawdown")
    assert out["pairs_used"] == 4
    assert out["correlation"] == 1.0


def test_metric_pair_pearson_perfect_negative() -> None:
    rows = [
        {"fitness": 1.0, "drawdown": 4.0},
        {"fitness": 2.0, "drawdown": 3.0},
        {"fitness": 3.0, "drawdown": 2.0},
        {"fitness": 4.0, "drawdown": 1.0},
    ]
    out = _metric_pair_pearson(rows, metric_a="fitness", metric_b="drawdown")
    assert out["correlation"] == -1.0


def test_metric_pair_pearson_drops_nulls() -> None:
    rows = [
        {"fitness": 1.0, "drawdown": 1.0},
        {"fitness": None, "drawdown": 2.0},
        {"fitness": 2.0, "drawdown": None},
        {"fitness": 3.0, "drawdown": 3.0},
        {"fitness": 4.0, "drawdown": 4.0},
    ]
    out = _metric_pair_pearson(rows, metric_a="fitness", metric_b="drawdown")
    assert out["pairs_used"] == 3
    assert out["correlation"] == 1.0


def test_metric_pair_pearson_too_few_pairs() -> None:
    rows = [
        {"fitness": 1.0, "drawdown": 1.0},
        {"fitness": None, "drawdown": 2.0},
    ]
    out = _metric_pair_pearson(rows, metric_a="fitness", metric_b="drawdown")
    assert out["pairs_used"] == 1
    assert out["correlation"] is None
    assert "note" in out


def test_metric_pair_pearson_drops_nans() -> None:
    rows = [
        {"fitness": 1.0, "drawdown": 1.0},
        {"fitness": float("nan"), "drawdown": 2.0},
        {"fitness": 2.0, "drawdown": 2.0},
        {"fitness": 3.0, "drawdown": 3.0},
    ]
    out = _metric_pair_pearson(rows, metric_a="fitness", metric_b="drawdown")
    assert out["pairs_used"] == 3
    assert out["correlation"] == 1.0


def test_metric_pair_pearson_handles_non_numeric() -> None:
    rows = [
        {"fitness": 1.0, "drawdown": 1.0},
        {"fitness": "bad", "drawdown": 2.0},
        {"fitness": 2.0, "drawdown": 2.0},
        {"fitness": 3.0, "drawdown": 3.0},
    ]
    out = _metric_pair_pearson(rows, metric_a="fitness", metric_b="drawdown")
    assert out["pairs_used"] == 3
    assert out["correlation"] == 1.0


def test_metric_pair_pearson_independent_variables() -> None:
    # Two unrelated variables → near-zero correlation
    rows = [
        {"a": 1.0, "b": 5.0},
        {"a": 2.0, "b": 3.0},
        {"a": 3.0, "b": 7.0},
        {"a": 4.0, "b": 4.0},
        {"a": 5.0, "b": 6.0},
    ]
    out = _metric_pair_pearson(rows, metric_a="a", metric_b="b")
    assert out["pairs_used"] == 5
    assert out["correlation"] is not None
    assert abs(out["correlation"]) < 0.95  # not perfectly correlated
