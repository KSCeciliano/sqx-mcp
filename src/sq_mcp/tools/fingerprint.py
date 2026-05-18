"""Strategy fingerprinting and near-duplicate detection.

Compute a deterministic signature from a strategy's defining characteristics
(parameters, indicator set, money management, structural shape of the equity
curve). Use to:

- Detect near-duplicates in a workspace (same idea, slightly different params).
- Identify strategy lineage across builder runs.
- Compare strategies for similarity (Jaccard on feature sets, Pearson on
  curves).

Tools:

- ``fingerprint_from_metrics`` — fingerprint from a metrics dict (trades,
  net_profit, profit_factor, max_dd, oos_is_ratio, build_at, params).
- ``fingerprint_from_sqx`` — extract relevant fields from a .sqx and
  fingerprint them.
- ``fingerprint_similarity`` — pairwise similarity between two
  fingerprints (Jaccard on tag set + Pearson on equity-curve digest).
- ``fingerprint_find_duplicates`` — given a list of fingerprints, group
  near-duplicates by similarity threshold.

Pure Python. Read-only.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp._validation import ValidationError, resolve_safe_path
from sq_mcp.parsers.sqx import parse_sqx


class FingerprintFromMetricsArgs(BaseModel):
    metrics: dict[str, Any]


class FingerprintFromSqxArgs(BaseModel):
    sqx_path: str


class FingerprintSimilarityArgs(BaseModel):
    fp_a: dict[str, Any]
    fp_b: dict[str, Any]


class FingerprintFindDuplicatesArgs(BaseModel):
    fingerprints: list[dict[str, Any]] = Field(..., min_length=2, max_length=10_000)
    threshold: float = Field(0.85, ge=0.0, le=1.0)


def _round_bucket(x: float, bucket: float) -> float:
    if bucket <= 0:
        return x
    return round(round(x / bucket) * bucket, 8)


def _curve_digest(curve: list[float], samples: int = 16) -> list[float]:
    """Downsample an equity curve to a fixed-size digest for similarity tests."""
    if not curve:
        return []
    if len(curve) <= samples:
        return [round(v, 6) for v in curve]
    step = len(curve) / samples
    out: list[float] = []
    for i in range(samples):
        idx = int(i * step)
        out.append(round(curve[idx], 6))
    return out


def _fingerprint_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Compute a fingerprint from a metrics dict.

    The fingerprint has two parts:
      - tags: a set of structural traits (e.g. "trades>500", "pf>1.5", "high_dd")
      - hash: a SHA-256 over canonicalized parameters
    """
    tags: set[str] = set()
    trades = int(metrics.get("trades") or 0)
    if trades >= 1000:
        tags.add("trades>1000")
    elif trades >= 500:
        tags.add("trades>500")
    elif trades >= 100:
        tags.add("trades>100")
    else:
        tags.add("low_trades")
    pf = float(metrics.get("profit_factor") or 0.0)
    if pf >= 2.0:
        tags.add("pf>2.0")
    elif pf >= 1.5:
        tags.add("pf>1.5")
    elif pf >= 1.2:
        tags.add("pf>1.2")
    else:
        tags.add("low_pf")
    dd = float(metrics.get("drawdown_pct") or 0.0)
    if dd > 25:
        tags.add("high_dd")
    elif dd > 10:
        tags.add("mod_dd")
    else:
        tags.add("low_dd")
    oos = float(metrics.get("oos_is_ratio") or 0.0)
    if oos >= 0.5:
        tags.add("oos>0.5")
    elif oos >= 0.3:
        tags.add("oos>0.3")
    else:
        tags.add("weak_oos")
    direction = metrics.get("direction")
    if direction:
        tags.add(f"dir:{direction}")

    # Canonicalize parameters into a stable string and hash them
    params = metrics.get("params") or {}
    if isinstance(params, dict):
        canon = json.dumps(params, sort_keys=True, separators=(",", ":"))
    else:
        canon = str(params)
    param_hash = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]

    # Bucketed numeric digest — collapses tiny variations
    num_digest = []
    for k in ("net_profit", "profit_factor", "drawdown_pct", "oos_is_ratio", "fitness_oos"):
        v = metrics.get(k)
        if isinstance(v, (int, float)):
            num_digest.append((k, _round_bucket(float(v), 0.05)))
    num_digest.sort()
    num_canon = json.dumps(num_digest, separators=(",", ":"))
    num_hash = hashlib.sha256(num_canon.encode("utf-8")).hexdigest()[:16]

    curve_digest = _curve_digest(metrics.get("equity_curve") or [])
    return {
        "tags": sorted(tags),
        "param_hash": param_hash,
        "num_hash": num_hash,
        "curve_digest": curve_digest,
        "signature": f"{param_hash}.{num_hash}",
    }


def _fingerprint_from_sqx_file(path: Path) -> dict[str, Any]:
    info = parse_sqx(path)
    if not isinstance(info, dict):
        return {"ok": False, "error": "could not parse .sqx"}
    settings = info.get("settings") or {}
    metrics_dict: dict[str, Any] = {
        "trades": info.get("trades"),
        "net_profit": info.get("net_profit"),
        "profit_factor": info.get("profit_factor"),
        "drawdown_pct": info.get("drawdown_pct"),
        "oos_is_ratio": info.get("oos_is_ratio"),
        "fitness_oos": info.get("fitness_oos"),
        "direction": settings.get("direction"),
        "params": settings.get("parameters") or {},
        "equity_curve": (info.get("equity_curves") or {}).get("full") or [],
    }
    fp = _fingerprint_from_metrics(metrics_dict)
    fp["sqx_path"] = str(path)
    fp["sqx_name"] = path.name
    return fp


def _jaccard(a: list[str], b: list[str]) -> float:
    set_a = set(a)
    set_b = set(b)
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _pearson_lists(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a_, b_ = a[:n], b[:n]
    ma = sum(a_) / n
    mb = sum(b_) / n
    da = [x - ma for x in a_]
    db = [x - mb for x in b_]
    var_a = sum(x * x for x in da)
    var_b = sum(x * x for x in db)
    if var_a == 0 or var_b == 0:
        return 0.0
    cov = sum(da[i] * db[i] for i in range(n))
    return cov / math.sqrt(var_a * var_b)


def _similarity(fp_a: dict[str, Any], fp_b: dict[str, Any]) -> dict[str, Any]:
    if fp_a.get("signature") and fp_a.get("signature") == fp_b.get("signature"):
        return {"similarity": 1.0, "verdict": "identical", "exact_match": True}
    tag_jaccard = _jaccard(fp_a.get("tags") or [], fp_b.get("tags") or [])
    curve_corr = _pearson_lists(
        fp_a.get("curve_digest") or [],
        fp_b.get("curve_digest") or [],
    )
    # Weighted combination: 60% tag jaccard, 40% curve correlation
    sim = 0.6 * tag_jaccard + 0.4 * max(0.0, curve_corr)
    return {
        "similarity": round(sim, 4),
        "tag_jaccard": round(tag_jaccard, 4),
        "curve_correlation": round(curve_corr, 4),
        "verdict": (
            "near_identical" if sim >= 0.95
            else "very_similar" if sim >= 0.85
            else "similar" if sim >= 0.70
            else "distinct"
        ),
        "exact_match": False,
    }


def _find_duplicates(
    fps: list[dict[str, Any]], threshold: float
) -> dict[str, Any]:
    n = len(fps)
    pairs: list[dict[str, Any]] = []
    groups: list[list[int]] = []
    visited: set[int] = set()
    for i in range(n):
        if i in visited:
            continue
        group = [i]
        visited.add(i)
        for j in range(i + 1, n):
            if j in visited:
                continue
            s = _similarity(fps[i], fps[j])
            if s["similarity"] >= threshold:
                pairs.append({"i": i, "j": j, **s})
                group.append(j)
                visited.add(j)
        if len(group) > 1:
            groups.append(group)
    return {
        "n_fingerprints": n,
        "threshold": threshold,
        "n_pairs_above_threshold": len(pairs),
        "n_groups": len(groups),
        "pairs": pairs,
        "groups": groups,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Compute a fingerprint from a strategy metrics dict. Returns tags "
            "(coarse structural traits), param_hash (SHA-256 of canonicalized "
            "params), num_hash (over bucketed numeric metrics), and "
            "curve_digest (16-sample equity-curve summary). Use to compare or "
            "deduplicate strategies. Read-only."
        )
    )
    async def fingerprint_from_metrics(args: FingerprintFromMetricsArgs) -> dict:
        return {"ok": True, **_fingerprint_from_metrics(args.metrics)}

    @mcp.tool(
        description=(
            "Compute a fingerprint directly from a .sqx file. Parses settings + "
            "metrics + embedded equity curve and produces the same fingerprint "
            "shape as fingerprint_from_metrics."
        )
    )
    async def fingerprint_from_sqx(args: FingerprintFromSqxArgs) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
        except ValidationError as e:
            return {"ok": False, "error": str(e)}
        result = _fingerprint_from_sqx_file(p)
        if "ok" in result and result["ok"] is False:
            return result
        return {"ok": True, **result}

    @mcp.tool(
        description=(
            "Pairwise similarity between two fingerprints. Returns a similarity "
            "score in [0,1], its components (tag Jaccard + curve correlation), "
            "and a verdict (identical / near_identical / very_similar / similar "
            "/ distinct)."
        )
    )
    async def fingerprint_similarity(args: FingerprintSimilarityArgs) -> dict:
        return {"ok": True, **_similarity(args.fp_a, args.fp_b)}

    @mcp.tool(
        description=(
            "Find near-duplicates in a list of fingerprints by pairwise "
            "similarity ≥ threshold (default 0.85). Returns the duplicate "
            "groups + each pair's score. Use to clean up redundant strategies."
        )
    )
    async def fingerprint_find_duplicates(
        args: FingerprintFindDuplicatesArgs,
    ) -> dict:
        return {
            "ok": True,
            **_find_duplicates(args.fingerprints, args.threshold),
        }


__all__ = [
    "FingerprintFindDuplicatesArgs",
    "FingerprintFromMetricsArgs",
    "FingerprintFromSqxArgs",
    "FingerprintSimilarityArgs",
    "_curve_digest",
    "_find_duplicates",
    "_fingerprint_from_metrics",
    "_fingerprint_from_sqx_file",
    "_jaccard",
    "_pearson_lists",
    "_similarity",
    "register",
]
