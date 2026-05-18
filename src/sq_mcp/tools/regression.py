"""Backtest regression detection: snapshot databank metrics, compare later.

Workflow:

  1. After a builder run finishes, call `databank_snapshot_metrics` — this
     writes a JSON file with each strategy's key metrics, keyed by trades_hash
     when available (stable across re-imports) or by file path otherwise.

  2. After a future run (e.g. retest with new data, additional CrossChecks,
     or a config tweak), call `databank_regression_check` against the same
     snapshot. The tool matches strategies by hash → reports which improved,
     which regressed (and by how much), which disappeared, and which are new.

The snapshot is just a small JSON file the caller controls — there's no
secret format, and it's pure read-only on disk (no engine call).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_databank_name,
    validate_project_name,
)
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.portfolio import _scan_databank

# Snapshot file format version — bump if the JSON layout changes incompatibly.
_SNAPSHOT_VERSION = 1

# Keys we lift from the derived-metrics dict into the snapshot. Anything else
# stays out — keep the snapshot small + stable.
_TRACKED_METRICS = (
    "trades",
    "net_profit",
    "drawdown_abs",
    "drawdown_pct",
    "fitness_is",
    "fitness_oos",
    "fitness_full",
    "oos_is_ratio",
    "profit_to_dd_ratio",
    "return_pct",
    "avg_trade",
    "trades_per_year",
    "symbol",
    "timeframe",
    "history_years",
    "strategy_name",
    "trades_hash",
    "fingerprint_exact",
)

# Default thresholds for regression detection. "Worse" depends on direction:
# fitness_oos lower = worse, drawdown_pct higher = worse.
_DEFAULT_REGRESSION_THRESHOLDS = {
    "fitness_oos": 0.10,           # >10% drop = regressed
    "profit_to_dd_ratio": 0.15,    # >15% drop
    "return_pct": 0.20,            # >20% drop
    "drawdown_pct": 0.20,          # >20% rise
}


class DatabankSnapshotMetricsArgs(BaseModel):
    project: str = Field(..., description="Project name.")
    databank: str = Field("Results", description="Databank folder.")
    snapshot_path: str = Field(
        ...,
        description=(
            "Where to write the JSON snapshot file. Will overwrite if it exists. "
            "Convention: <projects_dir>/<project>/metrics_snapshots/<label>.json"
        ),
    )
    label: str | None = Field(
        None,
        max_length=128,
        description="Optional human-readable label embedded in the snapshot.",
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_databank(cls, v: str) -> str:
        return validate_databank_name(v)


class DatabankRegressionCheckArgs(BaseModel):
    project: str = Field(..., description="Project name.")
    databank: str = Field("Results", description="Databank folder.")
    snapshot_path: str = Field(..., description="Path to a previously-written snapshot JSON.")
    match_on: Literal["trades_hash", "rel_path"] = Field(
        "trades_hash",
        description=(
            "Identity for matching baseline → current. trades_hash is the most "
            "stable (survives file renames, re-imports). rel_path is the fallback "
            "for databanks where strategies don't carry a hash."
        ),
    )
    regression_threshold: float = Field(
        0.10,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction-of-baseline change that counts as a regression. 0.10 = 10%. "
            "Applied to fitness_oos / profit_to_dd / return_pct (lower=worse) and "
            "drawdown_pct (higher=worse)."
        ),
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_databank(cls, v: str) -> str:
        return validate_databank_name(v)


# ---- helpers ---------------------------------------------------------------


def _scrape_for_snapshot(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project the rich metric rows down to just the tracked keys + rel path."""
    out: list[dict[str, Any]] = []
    for r in rows:
        entry = {"rel": r.get("rel")}
        for k in _TRACKED_METRICS:
            entry[k] = r.get(k)
        out.append(entry)
    return out


def _write_snapshot_atomically(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via tmp + os.replace so a crash mid-write doesn't corrupt the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _index_snapshot(
    entries: list[dict[str, Any]], *, match_on: str
) -> dict[str, dict[str, Any]]:
    """Build {key -> entry} from a snapshot, dropping rows without a key.

    `match_on` is either 'trades_hash' or 'rel_path'; 'rel_path' resolves to
    the `rel` field in each entry.
    """
    idx: dict[str, dict[str, Any]] = {}
    for e in entries:
        k = e.get("rel") if match_on == "rel_path" else e.get(match_on)
        if not k:
            continue
        idx[str(k)] = e
    return idx


def _classify(
    baseline_val: Any,
    current_val: Any,
    *,
    threshold: float,
    direction: Literal["higher_is_better", "lower_is_better"],
) -> tuple[str, float | None]:
    """Compare two scalar values. Returns ('improved'|'regressed'|'stable'|'missing', pct_change).

    pct_change is fractional: 0.10 = +10%, -0.05 = -5% relative to baseline.
    """
    if baseline_val is None or current_val is None:
        return "missing", None
    try:
        bf = float(baseline_val)
        cf = float(current_val)
    except (TypeError, ValueError):
        return "missing", None
    if bf == 0:
        # Can't compute percent change. Tag as stable unless current changed sign.
        if cf == 0:
            return "stable", 0.0
        return "improved" if (cf > 0) == (direction == "higher_is_better") else "regressed", None
    pct = (cf - bf) / abs(bf)
    if direction == "higher_is_better":
        if pct <= -threshold:
            return "regressed", pct
        if pct >= threshold:
            return "improved", pct
        return "stable", pct
    else:  # lower_is_better
        if pct >= threshold:
            return "regressed", pct
        if pct <= -threshold:
            return "improved", pct
        return "stable", pct


def _compare_entries(
    baseline: dict[str, Any], current: dict[str, Any], *, threshold: float
) -> dict[str, Any]:
    """Compare one matched pair. Returns per-metric classifications + worst flag."""
    metric_results: dict[str, dict[str, Any]] = {}
    for metric in _DEFAULT_REGRESSION_THRESHOLDS:
        direction = "lower_is_better" if metric == "drawdown_pct" else "higher_is_better"
        klass, pct = _classify(
            baseline.get(metric),
            current.get(metric),
            threshold=threshold,
            direction=direction,
        )
        metric_results[metric] = {
            "baseline": baseline.get(metric),
            "current": current.get(metric),
            "pct_change": round(pct, 4) if pct is not None else None,
            "classification": klass,
        }
    overall = "stable"
    if any(m["classification"] == "regressed" for m in metric_results.values()):
        overall = "regressed"
    elif any(m["classification"] == "improved" for m in metric_results.values()) and not any(
        m["classification"] == "regressed" for m in metric_results.values()
    ):
        overall = "improved"
    return {"overall": overall, "metrics": metric_results}


# ---- public tools ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Snapshot a databank's per-strategy metrics to a JSON file. Captures "
            "trades, fitness IS/OOS, drawdown, profit/DD ratio, OOS-IS ratio, and "
            "the trades_hash + fingerprint identity. Use this AFTER a builder run "
            "finishes — it's the baseline for databank_regression_check on the "
            "next iteration. Atomic write (tmp + replace)."
        )
    )
    async def databank_snapshot_metrics(
        args: DatabankSnapshotMetricsArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = eng.config.projects_dir / args.project / "databanks" / args.databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank directory not found: {db_dir}"}

            snap_path = resolve_safe_path(args.snapshot_path, must_exist=False)
            rows, bad = _scan_databank(db_dir)
            entries = _scrape_for_snapshot(rows)
            payload: dict[str, Any] = {
                "snapshot_version": _SNAPSHOT_VERSION,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "project": args.project,
                "databank": args.databank,
                "databank_dir": str(db_dir),
                "label": args.label,
                "strategy_count": len(entries),
                "unparseable_count": len(bad),
                "entries": entries,
                "unparseable": bad[:10],
            }
            _write_snapshot_atomically(snap_path, payload)
            return {
                "ok": True,
                "snapshot_path": str(snap_path),
                "project": args.project,
                "databank": args.databank,
                "captured_strategies": len(entries),
                "unparseable_count": len(bad),
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compare a databank's current state against a previous snapshot to detect "
            "regression. Matches strategies by trades_hash (preferred — stable across "
            "re-imports) or by relative file path. Reports per-strategy classification "
            "(improved / regressed / stable) and per-metric percent change. Tracks "
            "fitness_oos, profit_to_dd_ratio, return_pct (higher-is-better) and "
            "drawdown_pct (lower-is-better). Default 10% threshold; tunable."
        )
    )
    async def databank_regression_check(
        args: DatabankRegressionCheckArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = eng.config.projects_dir / args.project / "databanks" / args.databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank directory not found: {db_dir}"}

            snap_path = resolve_safe_path(args.snapshot_path, must_exist=True)
            try:
                snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return {"ok": False, "error": f"snapshot read/parse failed: {exc}"}

            ver = snapshot.get("snapshot_version")
            if ver != _SNAPSHOT_VERSION:
                return {
                    "ok": False,
                    "error": (
                        f"snapshot version mismatch: file={ver}, expected={_SNAPSHOT_VERSION}"
                    ),
                }

            rows, bad = _scan_databank(db_dir)
            current_entries = _scrape_for_snapshot(rows)

            baseline_entries: list[dict[str, Any]] = snapshot.get("entries", [])
            baseline_idx = _index_snapshot(baseline_entries, match_on=args.match_on)
            current_idx = _index_snapshot(current_entries, match_on=args.match_on)

            shared_keys = set(baseline_idx) & set(current_idx)
            only_baseline = set(baseline_idx) - shared_keys
            only_current = set(current_idx) - shared_keys

            comparisons: list[dict[str, Any]] = []
            counts = {"improved": 0, "regressed": 0, "stable": 0}
            for key in sorted(shared_keys):
                cmp_result = _compare_entries(
                    baseline_idx[key],
                    current_idx[key],
                    threshold=args.regression_threshold,
                )
                comparisons.append(
                    {
                        "key": key,
                        "baseline_rel": baseline_idx[key].get("rel"),
                        "current_rel": current_idx[key].get("rel"),
                        **cmp_result,
                    }
                )
                counts[cmp_result["overall"]] += 1

            regressions = [c for c in comparisons if c["overall"] == "regressed"]
            improvements = [c for c in comparisons if c["overall"] == "improved"]

            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "snapshot_path": str(snap_path),
                "snapshot_label": snapshot.get("label"),
                "snapshot_created_utc": snapshot.get("created_utc"),
                "match_on": args.match_on,
                "regression_threshold": args.regression_threshold,
                "baseline_count": len(baseline_idx),
                "current_count": len(current_idx),
                "matched_count": len(shared_keys),
                "only_in_baseline_count": len(only_baseline),
                "only_in_current_count": len(only_current),
                "counts": counts,
                "regressions": regressions,
                "improvements": improvements,
                "only_in_baseline": sorted(only_baseline)[:20],
                "only_in_current": sorted(only_current)[:20],
                "unparseable_count": len(bad),
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "register",
    "_scrape_for_snapshot",
    "_classify",
    "_compare_entries",
    "_index_snapshot",
    "_write_snapshot_atomically",
    "_TRACKED_METRICS",
    "_DEFAULT_REGRESSION_THRESHOLDS",
    "_SNAPSHOT_VERSION",
]
