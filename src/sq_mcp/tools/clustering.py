"""Strategy clustering and similarity analysis.

Group the strategies in a databank by their metric vectors. Two
strategies that look identical on the equity curve but differ on every
metric are *not* duplicates; two strategies whose metrics cluster
together usually share a market-regime exposure even if the rules read
differently. Use this to pick a portfolio that doesn't accidentally
concentrate on the same regime.

Tools:

- ``strategy_cluster_kmeans`` — scan a databank, z-score normalize the
  named metrics, run a small k-means (Lloyd's, pure Python) with
  configurable k and seed, and return per-strategy cluster assignments
  + per-cluster centroid means.
- ``strategy_pairwise_distance`` — pairwise Euclidean distance between
  the top-N strategies on z-scored metrics. Caps at 50 strategies to
  keep response size sane.
- ``strategy_nearest_neighbors`` — given a target strategy's index,
  list the K nearest strategies in the same databank by metric
  distance. Use to find quasi-duplicates that survived dedupe by
  trades_hash but are still highly correlated.

All math is pure Python — no numpy/scikit-learn dependency. Read-only.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_databank_name, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.portfolio import _scan_databank

# Default metrics for clustering — chosen because they're stable across runs
# and represent orthogonal facets of strategy behavior.
DEFAULT_METRICS = (
    "fitness_oos",
    "drawdown_pct",
    "oos_is_ratio",
    "trades",
    "profit_factor",
)


# ---- argument schemas ------------------------------------------------------


class ClusterKMeansArgs(BaseModel):
    project: str
    databank: str = "Results"
    k: int = Field(3, ge=2, le=12)
    metrics: list[str] = Field(default_factory=lambda: list(DEFAULT_METRICS), max_length=20)
    max_iters: int = Field(50, ge=5, le=500)
    seed: int = 42
    min_strategies: int = Field(6, ge=2, le=1000)

    @field_validator("project")
    @classmethod
    def _v_p(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_d(cls, v: str) -> str:
        return validate_databank_name(v)


class PairwiseDistanceArgs(BaseModel):
    project: str
    databank: str = "Results"
    n: int = Field(20, ge=2, le=50)
    metrics: list[str] = Field(default_factory=lambda: list(DEFAULT_METRICS), max_length=20)
    rank_by: str = "fitness_oos"

    @field_validator("project")
    @classmethod
    def _v_p(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_d(cls, v: str) -> str:
        return validate_databank_name(v)


class NearestNeighborsArgs(BaseModel):
    project: str
    databank: str = "Results"
    target_rel: str  # the .sqx relative path within the databank, used as the anchor
    k: int = Field(5, ge=1, le=50)
    metrics: list[str] = Field(default_factory=lambda: list(DEFAULT_METRICS), max_length=20)

    @field_validator("project")
    @classmethod
    def _v_p(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_d(cls, v: str) -> str:
        return validate_databank_name(v)


# ---- helpers ---------------------------------------------------------------


def _extract_vectors(
    rows: list[dict[str, Any]], metrics: list[str]
) -> tuple[list[list[float]], list[int], list[dict[str, Any]]]:
    """Pull metric vectors out of rows; drop rows with any missing/non-finite value.

    Returns ``(vectors, kept_indices, kept_rows)``. ``kept_indices`` is a list of
    indices into the *original* ``rows`` array, so callers can map back.
    """
    vectors: list[list[float]] = []
    kept_idx: list[int] = []
    kept_rows: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        vec: list[float] = []
        usable = True
        for m in metrics:
            v = r.get(m)
            if v is None:
                usable = False
                break
            try:
                f = float(v)
            except (TypeError, ValueError):
                usable = False
                break
            if f != f or math.isinf(f):
                usable = False
                break
            vec.append(f)
        if usable:
            vectors.append(vec)
            kept_idx.append(i)
            kept_rows.append(r)
    return vectors, kept_idx, kept_rows


def _zscore_columns(vectors: list[list[float]]) -> tuple[list[list[float]], list[dict[str, float]]]:
    """Z-score each column. Returns ``(normalized, per_column_stats)``.

    If a column has zero std, leave the values as 0 (no information).
    """
    if not vectors:
        return [], []
    n_cols = len(vectors[0])
    stats: list[dict[str, float]] = []
    cols: list[list[float]] = [[v[c] for v in vectors] for c in range(n_cols)]
    for c in range(n_cols):
        col = cols[c]
        mean = sum(col) / len(col)
        var = sum((x - mean) ** 2 for x in col) / len(col)
        std = math.sqrt(var)
        stats.append({"mean": mean, "std": std})
    out: list[list[float]] = []
    for v in vectors:
        norm = []
        for c, x in enumerate(v):
            s = stats[c]["std"]
            if s == 0:
                norm.append(0.0)
            else:
                norm.append((x - stats[c]["mean"]) / s)
        out.append(norm)
    return out, stats


def _euclidean(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def _kmeans(
    vectors: list[list[float]], k: int, *, max_iters: int, seed: int
) -> tuple[list[int], list[list[float]], int]:
    """Lloyd's algorithm. Returns (assignments, centroids, iters_run)."""
    if len(vectors) < k:
        # Trivial fallback: each point is its own cluster
        return list(range(len(vectors))), [list(v) for v in vectors], 0
    rng = random.Random(seed)
    # Init: pick k distinct points as initial centroids
    indices = list(range(len(vectors)))
    rng.shuffle(indices)
    centroids = [list(vectors[i]) for i in indices[:k]]
    assignments = [0] * len(vectors)
    iters_run = 0
    for _ in range(max_iters):
        iters_run += 1
        new_assignments = []
        for v in vectors:
            best_d = float("inf")
            best_c = 0
            for ci, c in enumerate(centroids):
                d = _euclidean(v, c)
                if d < best_d:
                    best_d = d
                    best_c = ci
            new_assignments.append(best_c)
        if new_assignments == assignments:
            break
        assignments = new_assignments
        # Recompute centroids
        new_cents: list[list[float]] = []
        for ci in range(k):
            members = [vectors[i] for i, a in enumerate(assignments) if a == ci]
            if not members:
                # Empty cluster — keep the old centroid (avoid degenerate state)
                new_cents.append(centroids[ci])
                continue
            n_dim = len(members[0])
            mean_v = [sum(m[d] for m in members) / len(members) for d in range(n_dim)]
            new_cents.append(mean_v)
        centroids = new_cents
    return assignments, centroids, iters_run


def _summarize_clusters(
    rows: list[dict[str, Any]],
    assignments: list[int],
    metrics: list[str],
    k: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ci in range(k):
        members = [rows[i] for i, a in enumerate(assignments) if a == ci]
        cluster = {
            "cluster": ci,
            "size": len(members),
            "metric_means": {},
            "examples": [m.get("rel") for m in members[:5]],
        }
        for metric in metrics:
            vals = [
                float(m[metric])
                for m in members
                if m.get(metric) is not None
            ]
            cluster["metric_means"][metric] = (
                round(sum(vals) / len(vals), 4) if vals else None
            )
        out.append(cluster)
    return out


def _pairwise_matrix(
    rows: list[dict[str, Any]], metrics: list[str]
) -> dict[str, Any]:
    vectors, kept_idx, kept_rows = _extract_vectors(rows, metrics)
    if len(vectors) < 2:
        return {"ok": False, "error": "need at least 2 strategies with complete metric vectors"}
    normed, _stats = _zscore_columns(vectors)
    labels = [r.get("rel", str(i)) for i, r in enumerate(kept_rows)]
    matrix = []
    for i, vi in enumerate(normed):
        row = []
        for j, vj in enumerate(normed):
            if i == j:
                row.append(0.0)
            else:
                row.append(round(_euclidean(vi, vj), 4))
        matrix.append(row)
    # Build a summary: per-strategy nearest neighbor
    nearest = []
    for i, _ in enumerate(normed):
        nn_idx = None
        nn_d = float("inf")
        for j, _ in enumerate(normed):
            if i == j:
                continue
            d = matrix[i][j]
            if d < nn_d:
                nn_d = d
                nn_idx = j
        if nn_idx is not None:
            nearest.append({
                "rel": labels[i],
                "nearest_rel": labels[nn_idx],
                "distance": nn_d,
            })
    return {
        "ok": True,
        "labels": labels,
        "matrix": matrix,
        "nearest_neighbor": nearest,
        "metrics_used": metrics,
    }


def _nearest_neighbors(
    rows: list[dict[str, Any]], target_rel: str, metrics: list[str], k: int
) -> dict[str, Any]:
    vectors, _idx, kept_rows = _extract_vectors(rows, metrics)
    target_i = None
    for i, r in enumerate(kept_rows):
        if r.get("rel") == target_rel:
            target_i = i
            break
    if target_i is None:
        return {"ok": False, "error": f"target_rel not found in databank: {target_rel}"}
    if len(vectors) < 2:
        return {"ok": False, "error": "need at least 2 strategies for nearest-neighbor search"}
    normed, _stats = _zscore_columns(vectors)
    target_vec = normed[target_i]
    distances = []
    for i, v in enumerate(normed):
        if i == target_i:
            continue
        d = _euclidean(target_vec, v)
        distances.append({"rel": kept_rows[i]["rel"], "distance": round(d, 4)})
    distances.sort(key=lambda x: x["distance"])
    return {
        "ok": True,
        "target": target_rel,
        "neighbors": distances[:k],
        "metrics_used": metrics,
        "population": len(distances),
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Cluster a databank's strategies by their metric vectors using "
            "k-means. Z-score normalizes the named metrics, runs Lloyd's algorithm "
            "(pure Python), and returns per-strategy cluster assignments and "
            "per-cluster centroid means + size. Use to spot strategies that look "
            "similar even if they survived trade-hash dedupe. Read-only."
        )
    )
    async def strategy_cluster_kmeans(args: ClusterKMeansArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = Path(eng.config.projects_dir) / args.project / "databanks" / args.databank
            if not db_dir.is_dir():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, bad = _scan_databank(db_dir)
            if len(rows) < args.min_strategies:
                return {
                    "ok": False,
                    "error": f"only {len(rows)} strategies found, need >= {args.min_strategies}",
                }
            vectors, kept_idx, kept_rows = _extract_vectors(rows, args.metrics)
            if len(vectors) < args.k:
                return {
                    "ok": False,
                    "error": (
                        f"only {len(vectors)} strategies have complete metric vectors, "
                        f"need >= {args.k}"
                    ),
                }
            normed, col_stats = _zscore_columns(vectors)
            assignments, centroids, iters = _kmeans(
                normed, args.k, max_iters=args.max_iters, seed=args.seed
            )
            cluster_summary = _summarize_clusters(kept_rows, assignments, args.metrics, args.k)
            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "k": args.k,
                "iters_run": iters,
                "n_strategies_in_databank": len(rows),
                "n_strategies_clustered": len(vectors),
                "metrics_used": args.metrics,
                "column_stats": [
                    {"metric": m, **{kk: round(vv, 6) for kk, vv in s.items()}}
                    for m, s in zip(args.metrics, col_stats, strict=True)
                ],
                "clusters": cluster_summary,
                "assignments": [
                    {"rel": kept_rows[i]["rel"], "cluster": assignments[i]}
                    for i in range(len(kept_rows))
                ],
                "unparseable_files": len(bad),
            }
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Pairwise Euclidean distance matrix on the top-N strategies "
            "(ranked by rank_by) in a databank. Uses z-scored metrics so "
            "different magnitudes don't dominate. Returns the full matrix plus "
            "each strategy's nearest neighbor. Capped at 50 strategies. Read-only."
        )
    )
    async def strategy_pairwise_distance(args: PairwiseDistanceArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = Path(eng.config.projects_dir) / args.project / "databanks" / args.databank
            if not db_dir.is_dir():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, _bad = _scan_databank(db_dir)
            # Rank by chosen metric (descending) — drop rows with no rank value
            ranked = [r for r in rows if r.get(args.rank_by) is not None]
            ranked.sort(key=lambda r: float(r.get(args.rank_by) or 0), reverse=True)
            top = ranked[: args.n]
            result = _pairwise_matrix(top, args.metrics)
            result["project"] = args.project
            result["databank"] = args.databank
            result["rank_by"] = args.rank_by
            result["n_requested"] = args.n
            return result
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "K nearest neighbors of a target strategy within a databank by "
            "z-scored metric distance. Use to find quasi-duplicates of a "
            "specific strategy when trade-hash dedupe wasn't enough. Read-only."
        )
    )
    async def strategy_nearest_neighbors(args: NearestNeighborsArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = Path(eng.config.projects_dir) / args.project / "databanks" / args.databank
            if not db_dir.is_dir():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, _bad = _scan_databank(db_dir)
            result = _nearest_neighbors(rows, args.target_rel, args.metrics, args.k)
            result["project"] = args.project
            result["databank"] = args.databank
            return result
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)
