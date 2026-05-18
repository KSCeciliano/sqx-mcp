"""Unit tests for clustering helpers."""

from __future__ import annotations

from sq_mcp.tools.clustering import (
    _euclidean,
    _extract_vectors,
    _kmeans,
    _nearest_neighbors,
    _pairwise_matrix,
    _summarize_clusters,
    _zscore_columns,
)

# ---- _extract_vectors ------------------------------------------------------


def test_extract_vectors_drops_rows_with_none() -> None:
    rows = [
        {"rel": "a", "fitness_oos": 1.0, "drawdown_pct": 10.0},
        {"rel": "b", "fitness_oos": None, "drawdown_pct": 15.0},
        {"rel": "c", "fitness_oos": 2.0, "drawdown_pct": 20.0},
    ]
    vecs, idx, kept = _extract_vectors(rows, ["fitness_oos", "drawdown_pct"])
    assert len(vecs) == 2
    assert idx == [0, 2]
    assert kept[0]["rel"] == "a"


def test_extract_vectors_drops_nan() -> None:
    rows = [
        {"rel": "a", "x": 1.0},
        {"rel": "b", "x": float("nan")},
        {"rel": "c", "x": 3.0},
    ]
    vecs, _idx, _kept = _extract_vectors(rows, ["x"])
    assert len(vecs) == 2


def test_extract_vectors_drops_inf() -> None:
    rows = [
        {"rel": "a", "x": 1.0},
        {"rel": "b", "x": float("inf")},
        {"rel": "c", "x": 3.0},
    ]
    vecs, _idx, _kept = _extract_vectors(rows, ["x"])
    assert len(vecs) == 2


def test_extract_vectors_drops_non_numeric_strings() -> None:
    rows = [
        {"rel": "a", "x": 1.0},
        {"rel": "b", "x": "bad"},
        {"rel": "c", "x": 3.0},
    ]
    vecs, _idx, _kept = _extract_vectors(rows, ["x"])
    assert len(vecs) == 2


# ---- _zscore_columns -------------------------------------------------------


def test_zscore_zero_mean_unit_std() -> None:
    vecs = [[1.0], [2.0], [3.0], [4.0], [5.0]]
    out, stats = _zscore_columns(vecs)
    # Mean ~ 0
    col_mean = sum(v[0] for v in out) / len(out)
    assert abs(col_mean) < 1e-9
    # Std should be ~ 1 (using population std as in the helper)
    var = sum(v[0] ** 2 for v in out) / len(out)
    assert abs(var - 1.0) < 1e-6
    assert stats[0]["mean"] == 3.0


def test_zscore_zero_std_column_normalizes_to_zero() -> None:
    # All same value → std=0 → normalized = 0
    vecs = [[5.0, 1.0], [5.0, 2.0], [5.0, 3.0]]
    out, stats = _zscore_columns(vecs)
    assert stats[0]["std"] == 0
    assert all(v[0] == 0.0 for v in out)


def test_zscore_empty_input() -> None:
    out, stats = _zscore_columns([])
    assert out == []
    assert stats == []


# ---- _euclidean ------------------------------------------------------------


def test_euclidean_zero_for_same_point() -> None:
    assert _euclidean([1.0, 2.0], [1.0, 2.0]) == 0.0


def test_euclidean_pythagoras() -> None:
    # (0,0) to (3,4) = 5
    assert _euclidean([0.0, 0.0], [3.0, 4.0]) == 5.0


# ---- _kmeans ---------------------------------------------------------------


def test_kmeans_separable_clusters_recovers_clusters() -> None:
    # Two clearly-separated clusters
    vecs = [
        [0.0, 0.0], [0.1, 0.0], [0.0, 0.1], [0.1, 0.1],  # cluster A around origin
        [10.0, 10.0], [10.1, 10.0], [10.0, 10.1], [10.1, 10.1],  # cluster B
    ]
    assignments, centroids, iters = _kmeans(vecs, k=2, max_iters=50, seed=42)
    # First 4 should share an assignment, last 4 share another
    assert len(set(assignments[:4])) == 1
    assert len(set(assignments[4:])) == 1
    assert assignments[0] != assignments[4]
    # Centroids should be near the original clusters
    assert iters >= 1


def test_kmeans_k_equal_n_each_point_its_own_cluster() -> None:
    vecs = [[1.0], [2.0], [3.0]]
    assignments, centroids, _ = _kmeans(vecs, k=3, max_iters=50, seed=7)
    # Each point should be in its own cluster
    assert sorted(assignments) == [0, 1, 2]


def test_kmeans_n_less_than_k_returns_trivial() -> None:
    # k > n → fallback: each point its own cluster
    vecs = [[1.0], [2.0]]
    assignments, centroids, iters = _kmeans(vecs, k=5, max_iters=50, seed=7)
    assert assignments == [0, 1]
    assert iters == 0


def test_kmeans_deterministic_with_seed() -> None:
    vecs = [[float(i), float(i * 2)] for i in range(20)]
    a1, _, _ = _kmeans(vecs, k=3, max_iters=50, seed=42)
    a2, _, _ = _kmeans(vecs, k=3, max_iters=50, seed=42)
    assert a1 == a2


# ---- _summarize_clusters ---------------------------------------------------


def test_summarize_clusters_counts_members_correctly() -> None:
    rows = [
        {"rel": "a", "fitness_oos": 1.0},
        {"rel": "b", "fitness_oos": 2.0},
        {"rel": "c", "fitness_oos": 3.0},
    ]
    assignments = [0, 0, 1]
    out = _summarize_clusters(rows, assignments, ["fitness_oos"], k=2)
    assert out[0]["size"] == 2
    assert out[1]["size"] == 1
    assert out[0]["metric_means"]["fitness_oos"] == 1.5
    assert out[1]["metric_means"]["fitness_oos"] == 3.0


def test_summarize_clusters_examples_list_max_5() -> None:
    rows = [{"rel": f"s{i}", "x": float(i)} for i in range(10)]
    assignments = [0] * 10
    out = _summarize_clusters(rows, assignments, ["x"], k=1)
    assert len(out[0]["examples"]) == 5


# ---- _pairwise_matrix ------------------------------------------------------


def test_pairwise_matrix_smoke() -> None:
    rows = [
        {"rel": "a", "x": 1.0, "y": 1.0},
        {"rel": "b", "x": 2.0, "y": 2.0},
        {"rel": "c", "x": 3.0, "y": 3.0},
    ]
    out = _pairwise_matrix(rows, ["x", "y"])
    assert out["ok"] is True
    assert len(out["labels"]) == 3
    assert len(out["matrix"]) == 3
    # Diagonal is zero
    for i in range(3):
        assert out["matrix"][i][i] == 0.0


def test_pairwise_matrix_too_few_strategies() -> None:
    rows = [{"rel": "a", "x": 1.0}]
    out = _pairwise_matrix(rows, ["x"])
    assert out["ok"] is False


def test_pairwise_matrix_nearest_neighbor_present() -> None:
    rows = [
        {"rel": "a", "x": 1.0},
        {"rel": "b", "x": 1.1},
        {"rel": "c", "x": 10.0},
    ]
    out = _pairwise_matrix(rows, ["x"])
    nn = {x["rel"]: x["nearest_rel"] for x in out["nearest_neighbor"]}
    # a and b are closest to each other
    assert nn["a"] == "b"
    assert nn["b"] == "a"


# ---- _nearest_neighbors ----------------------------------------------------


def test_nearest_neighbors_finds_closest() -> None:
    rows = [
        {"rel": "a", "x": 1.0},
        {"rel": "b", "x": 1.5},
        {"rel": "c", "x": 5.0},
        {"rel": "d", "x": 10.0},
    ]
    out = _nearest_neighbors(rows, target_rel="a", metrics=["x"], k=2)
    assert out["ok"] is True
    assert out["neighbors"][0]["rel"] == "b"


def test_nearest_neighbors_target_not_found() -> None:
    rows = [{"rel": "a", "x": 1.0}, {"rel": "b", "x": 2.0}]
    out = _nearest_neighbors(rows, target_rel="missing", metrics=["x"], k=1)
    assert out["ok"] is False


def test_nearest_neighbors_too_few_strategies() -> None:
    rows = [{"rel": "a", "x": 1.0}]
    out = _nearest_neighbors(rows, target_rel="a", metrics=["x"], k=1)
    assert out["ok"] is False
