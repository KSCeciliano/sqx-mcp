"""Unit tests for fingerprint helpers."""

from __future__ import annotations

from sq_mcp.tools.fingerprint import (
    _curve_digest,
    _find_duplicates,
    _fingerprint_from_metrics,
    _jaccard,
    _pearson_lists,
    _similarity,
)


def test_curve_digest_downsamples() -> None:
    curve = list(range(100))
    digest = _curve_digest(curve, samples=16)
    assert len(digest) == 16
    assert digest[0] == 0
    assert digest[-1] >= 90


def test_curve_digest_short_curve_returned_as_is() -> None:
    curve = [1.0, 2.0, 3.0]
    digest = _curve_digest(curve, samples=16)
    assert digest == [1.0, 2.0, 3.0]


def test_curve_digest_empty() -> None:
    assert _curve_digest([], samples=16) == []


def test_fingerprint_from_metrics_stable() -> None:
    metrics = {
        "trades": 500,
        "net_profit": 1234.5,
        "profit_factor": 1.7,
        "drawdown_pct": 8.0,
        "oos_is_ratio": 0.5,
        "params": {"a": 1, "b": 2},
    }
    fp1 = _fingerprint_from_metrics(metrics)
    fp2 = _fingerprint_from_metrics(metrics)
    assert fp1["signature"] == fp2["signature"]
    assert "trades>500" in fp1["tags"]
    assert "pf>1.5" in fp1["tags"]


def test_fingerprint_different_params_different_hash() -> None:
    base = {"trades": 500, "profit_factor": 1.5, "params": {"a": 1}}
    other = {"trades": 500, "profit_factor": 1.5, "params": {"a": 2}}
    fp_a = _fingerprint_from_metrics(base)
    fp_b = _fingerprint_from_metrics(other)
    assert fp_a["param_hash"] != fp_b["param_hash"]


def test_jaccard_overlap() -> None:
    assert _jaccard(["a", "b", "c"], ["b", "c", "d"]) == 2 / 4


def test_jaccard_identical() -> None:
    assert _jaccard(["a", "b"], ["a", "b"]) == 1.0


def test_jaccard_disjoint() -> None:
    assert _jaccard(["a"], ["b"]) == 0.0


def test_pearson_perfect_correlation() -> None:
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [2.0, 4.0, 6.0, 8.0, 10.0]
    rho = _pearson_lists(a, b)
    assert abs(rho - 1.0) < 1e-6


def test_pearson_perfect_anticorrelation() -> None:
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [5.0, 4.0, 3.0, 2.0, 1.0]
    rho = _pearson_lists(a, b)
    assert abs(rho - (-1.0)) < 1e-6


def test_similarity_identical_signature() -> None:
    metrics = {"trades": 500, "profit_factor": 1.5, "params": {"a": 1}}
    fp = _fingerprint_from_metrics(metrics)
    result = _similarity(fp, fp)
    assert result["similarity"] == 1.0
    assert result["exact_match"] is True


def test_similarity_distinct() -> None:
    a = _fingerprint_from_metrics({"trades": 50, "profit_factor": 0.8, "params": {"x": 1}})
    b = _fingerprint_from_metrics({"trades": 5000, "profit_factor": 2.5, "params": {"y": 99}})
    result = _similarity(a, b)
    assert result["similarity"] < 0.8


def test_find_duplicates_groups_near_dupes() -> None:
    base = {"trades": 500, "profit_factor": 1.5, "params": {"a": 1}}
    fps = [
        _fingerprint_from_metrics(base),
        _fingerprint_from_metrics(base),  # exact duplicate
        _fingerprint_from_metrics({"trades": 5000, "profit_factor": 2.5, "params": {"x": 9}}),
    ]
    result = _find_duplicates(fps, threshold=0.85)
    assert result["n_groups"] == 1
    assert any(0 in g and 1 in g for g in result["groups"])
