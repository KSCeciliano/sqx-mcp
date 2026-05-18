"""Data-quality tools — bar-density estimates, staleness checks, .dat diff.

These complement ``data.py`` (which imports / updates / exports raw history)
and ``integrity.py`` (which cross-checks registry against disk). The focus
here is *quality* of the data already on disk:

- ``data_workspace_quality_report`` — fan-out audit across every symbol in
  the registry: stale .dat files, registry rows pointing to missing files,
  bar-density anomalies. One call, full picture.
- ``data_bar_density`` — estimate actual vs expected bar count for a single
  symbol/TF/date range. Catches partial data fetches that didn't fail loudly.
- ``data_dat_header_diff`` — compare two .dat files' headers + size + bar
  estimates. Useful for "did my pull actually add bars?" checks.
- ``data_age_check`` — flag .dat files whose mtime is older than N days.

All read-only; no engine calls (operates on data.db + filesystem).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_date,
    validate_symbol,
    validate_timeframe,
)
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.integrity import _index_disk_files, _index_registry_rows
from sq_mcp.tools.symbols import (
    _DAT_BYTES_PER_BAR,
    _enrich_data_row,
    _open_data_registry,
    _parse_dat_header,
)

# ---- timeframe → minutes per bar ------------------------------------------

_TF_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H2": 120, "H4": 240, "H6": 360, "H8": 480, "H12": 720,
    "D1": 60 * 24,
    "W1": 60 * 24 * 7,
    "MN1": 60 * 24 * 30,  # approximation
}


# ---- argument schemas ------------------------------------------------------


class DataBarDensityArgs(BaseModel):
    symbol: str
    timeframe: str
    date_from: str | None = Field(
        None,
        description="yyyy.mm.dd. Defaults to registry date_from when missing.",
    )
    date_to: str | None = Field(
        None,
        description="yyyy.mm.dd. Defaults to registry date_to (or now).",
    )

    @field_validator("symbol")
    @classmethod
    def _v_symbol(cls, v: str) -> str:
        return validate_symbol(v)

    @field_validator("timeframe")
    @classmethod
    def _v_tf(cls, v: str) -> str:
        return validate_timeframe(v)

    @field_validator("date_from", "date_to")
    @classmethod
    def _v_date(cls, v: str | None) -> str | None:
        return validate_date(v) if v else v


class DataDatHeaderDiffArgs(BaseModel):
    path_a: str
    path_b: str


class DataAgeCheckArgs(BaseModel):
    max_age_days: int = Field(7, ge=1, le=3650)


# ---- helpers ---------------------------------------------------------------


def _expected_bars_for_window(
    *, timeframe: str, ms_from: int, ms_to: int, market_hours_per_week: int = 168
) -> int | None:
    """Expected bar count for a [ms_from, ms_to] window on a TF.

    `market_hours_per_week` defaults to 168 (24h × 7) which is right for crypto
    and a generous upper bound for forex/futures. For forex you'd use 120
    (Mon 00:00 → Fri 24:00 UTC) but we leave that as an explicit caller knob.
    """
    minutes = _TF_MINUTES.get(timeframe)
    if not minutes:
        return None
    if ms_to <= ms_from:
        return 0
    duration_ms = ms_to - ms_from
    total_minutes = duration_ms / 1000.0 / 60.0
    # Scale by the fraction of hours the market is open
    open_fraction = market_hours_per_week / 168.0
    return int(total_minutes * open_fraction / minutes)


def _estimate_actual_bars(dat_path: Path) -> int:
    """Best-effort actual-bar count by file size minus header bytes."""
    try:
        stat = dat_path.stat()
    except OSError:
        return 0
    header = _parse_dat_header(dat_path)
    header_bytes = header.get("header_bytes", 0) or 0
    body = max(0, stat.st_size - header_bytes)
    return body // _DAT_BYTES_PER_BAR


def _ms_for_yyyymmdd(s: str) -> int:
    y, m, d = s.split(".")
    return int(datetime(int(y), int(m), int(d), tzinfo=timezone.utc).timestamp() * 1000)


def _bar_density_report(
    *,
    dat_paths: list[Path],
    timeframe: str,
    ms_from: int,
    ms_to: int,
    market_hours_per_week: int,
) -> dict[str, Any]:
    expected = _expected_bars_for_window(
        timeframe=timeframe,
        ms_from=ms_from,
        ms_to=ms_to,
        market_hours_per_week=market_hours_per_week,
    )
    per_file = []
    actual_total = 0
    for p in dat_paths:
        actual = _estimate_actual_bars(p)
        actual_total += actual
        per_file.append(
            {
                "name": p.name,
                "estimated_bars": actual,
                "size_bytes": p.stat().st_size if p.exists() else None,
            }
        )
    ratio: float | None = None
    if expected and expected > 0:
        ratio = round(actual_total / expected, 4)
    return {
        "expected_bars": expected,
        "actual_bars_estimated": actual_total,
        "coverage_ratio": ratio,
        "files": per_file,
    }


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Estimate bar density for a (symbol, timeframe) pair: expected bars "
            "for the registry date window vs the actual bar count derived from "
            ".dat file size. Coverage ratios well under 1.0 signal a partial "
            "fetch; ratios over ~1.05 signal an over-sized file (possibly "
            "header miscount or duplicate bars). Read-only."
        )
    )
    async def data_bar_density(args: DataBarDensityArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            con = _open_data_registry(eng.config.data_dir)
            if con is None:
                return {"ok": False, "error": "data.db registry not readable"}
            try:
                cur = con.execute(
                    "SELECT * FROM DATA WHERE TIMEFRAME = ? AND "
                    "(SYMBOL = ? OR USYMBOL = ? OR INSTRUMENT = ?) LIMIT 1",
                    (args.timeframe, args.symbol, args.symbol, args.symbol),
                )
                row = cur.fetchone()
            finally:
                con.close()
            if row is None:
                return {
                    "ok": False,
                    "error": (
                        f"no DATA row for {args.symbol}/{args.timeframe} — "
                        "check broker_data_integrity or data_coverage_check first"
                    ),
                }
            row_d = _enrich_data_row(dict(row))
            ms_from = (
                _ms_for_yyyymmdd(args.date_from)
                if args.date_from
                else int(row_d.get("DATEFROM") or 0)
            )
            ms_to = (
                _ms_for_yyyymmdd(args.date_to)
                if args.date_to
                else int(row_d.get("DATETO") or datetime.now(tz=timezone.utc).timestamp() * 1000)
            )

            disk_idx = _index_disk_files(eng.config.history_dir)
            dat_paths = disk_idx.get((args.symbol, args.timeframe), [])
            report = _bar_density_report(
                dat_paths=dat_paths,
                timeframe=args.timeframe,
                ms_from=ms_from,
                ms_to=ms_to,
                market_hours_per_week=168,
            )
            return {
                "ok": True,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "registry_date_from": row_d.get("date_from_iso"),
                "registry_date_to": row_d.get("date_to_iso"),
                "window_ms_from": ms_from,
                "window_ms_to": ms_to,
                **report,
            }
        except (EngineError, ValidationError, OSError, ValueError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "One-call workspace data quality audit: iterates every (symbol, TF) "
            "in the data.db registry, computes bar-density coverage, flags "
            "stale (>=30 days) and orphaned files. Aggregates issues by code. "
            "Slow on huge workspaces — runs O(symbols × TFs). Read-only."
        )
    )
    async def data_workspace_quality_report(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            con = _open_data_registry(eng.config.data_dir)
            registry_rows: list[dict[str, Any]] = []
            if con is not None:
                try:
                    cur = con.execute("SELECT * FROM DATA")
                    registry_rows = [_enrich_data_row(dict(r)) for r in cur.fetchall()]
                finally:
                    con.close()

            disk_idx = _index_disk_files(eng.config.history_dir)
            reg_idx = _index_registry_rows(registry_rows)

            issues_by_code: dict[str, int] = {}
            entries: list[dict[str, Any]] = []
            now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

            keys = sorted(set(disk_idx) | set(reg_idx))
            for sym, tf in keys:
                disk_files = disk_idx.get((sym, tf), [])
                reg_rows = reg_idx.get((sym, tf), [])
                entry: dict[str, Any] = {
                    "symbol": sym,
                    "timeframe": tf,
                    "disk_files": len(disk_files),
                    "registry_rows": len(reg_rows),
                    "issues": [],
                }
                if not disk_files and reg_rows:
                    entry["issues"].append("REGISTRY_HAS_NO_FILE")
                    issues_by_code["REGISTRY_HAS_NO_FILE"] = (
                        issues_by_code.get("REGISTRY_HAS_NO_FILE", 0) + 1
                    )
                if disk_files and not reg_rows:
                    entry["issues"].append("FILE_NOT_IN_REGISTRY")
                    issues_by_code["FILE_NOT_IN_REGISTRY"] = (
                        issues_by_code.get("FILE_NOT_IN_REGISTRY", 0) + 1
                    )
                if reg_rows:
                    row = reg_rows[0]
                    ms_from = int(row.get("DATEFROM") or 0)
                    ms_to = int(row.get("DATETO") or 0)
                    if ms_to and (now_ms - ms_to) > 30 * 86400 * 1000:
                        entry["issues"].append("REGISTRY_DATA_STALE_30D")
                        issues_by_code["REGISTRY_DATA_STALE_30D"] = (
                            issues_by_code.get("REGISTRY_DATA_STALE_30D", 0) + 1
                        )
                    if disk_files:
                        density = _bar_density_report(
                            dat_paths=disk_files,
                            timeframe=tf,
                            ms_from=ms_from,
                            ms_to=ms_to,
                            market_hours_per_week=168,
                        )
                        ratio = density["coverage_ratio"]
                        entry["coverage_ratio"] = ratio
                        if ratio is not None and ratio < 0.85:
                            entry["issues"].append("LOW_COVERAGE_RATIO")
                            issues_by_code["LOW_COVERAGE_RATIO"] = (
                                issues_by_code.get("LOW_COVERAGE_RATIO", 0) + 1
                            )
                entries.append(entry)

            return {
                "ok": True,
                "history_dir": str(eng.config.history_dir),
                "data_db": str(eng.config.data_dir / "data.db"),
                "pairs_total": len(entries),
                "pairs_with_issues": sum(1 for e in entries if e["issues"]),
                "issues_by_code": issues_by_code,
                "entries": entries,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Compare two .dat history files: header strings, file size, header "
            "byte offset, and estimated bar count. Useful for verifying that "
            "an incremental data update actually appended bars. Read-only."
        )
    )
    async def data_dat_header_diff(
        args: DataDatHeaderDiffArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            pa = resolve_safe_path(args.path_a, must_exist=True)
            pb = resolve_safe_path(args.path_b, must_exist=True)
            if pa.suffix.lower() != ".dat" or pb.suffix.lower() != ".dat":
                return {"ok": False, "error": "both paths must point to .dat files"}
            ha = _parse_dat_header(pa)
            hb = _parse_dat_header(pb)
            sa = pa.stat().st_size
            sb = pb.stat().st_size
            ba = _estimate_actual_bars(pa)
            bb = _estimate_actual_bars(pb)
            return {
                "ok": True,
                "a": {
                    "path": str(pa),
                    "size_bytes": sa,
                    "header_strings": ha.get("strings"),
                    "header_bytes": ha.get("header_bytes"),
                    "estimated_bars": ba,
                },
                "b": {
                    "path": str(pb),
                    "size_bytes": sb,
                    "header_strings": hb.get("strings"),
                    "header_bytes": hb.get("header_bytes"),
                    "estimated_bars": bb,
                },
                "diff": {
                    "size_delta_bytes": sb - sa,
                    "bar_delta_estimated": bb - ba,
                    "headers_match": ha.get("strings") == hb.get("strings"),
                },
            }
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Flag .dat history files whose mtime is older than max_age_days. "
            "Stale files mean the data hasn't been refreshed (and a recent "
            "backtest may be testing against year-old data). Read-only."
        )
    )
    async def data_age_check(args: DataAgeCheckArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cutoff_s = datetime.now(tz=timezone.utc).timestamp() - args.max_age_days * 86400
            stale: list[dict[str, Any]] = []
            fresh: list[dict[str, Any]] = []
            for p in eng.config.history_dir.rglob("*.dat"):
                try:
                    st = p.stat()
                except OSError:
                    continue
                age_days = round((datetime.now(tz=timezone.utc).timestamp() - st.st_mtime) / 86400, 2)
                row = {
                    "path": str(p),
                    "size_mb": round(st.st_size / (1024 * 1024), 2),
                    "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                    "age_days": age_days,
                }
                if st.st_mtime < cutoff_s:
                    stale.append(row)
                else:
                    fresh.append(row)
            return {
                "ok": True,
                "history_dir": str(eng.config.history_dir),
                "max_age_days": args.max_age_days,
                "stale_count": len(stale),
                "fresh_count": len(fresh),
                "stale": sorted(stale, key=lambda r: r["mtime"]),
                "fresh_count_by_dir": {
                    Path(r["path"]).parent.name: 1 + (
                        {} if not fresh else {}
                    ).get(Path(r["path"]).parent.name, 0)
                    for r in fresh[:200]
                },
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "DataAgeCheckArgs",
    "DataBarDensityArgs",
    "DataDatHeaderDiffArgs",
    "_bar_density_report",
    "_estimate_actual_bars",
    "_expected_bars_for_window",
    "_ms_for_yyyymmdd",
    "register",
]
