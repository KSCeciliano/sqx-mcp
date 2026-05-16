"""Symbol & instrument management tools."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_symbol, validate_timeframe
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import (
    get_engine,
    is_status_only_response,
    parse_listing_response,
    parse_response,
    safe_error_payload,
)
from sq_mcp.tools.projects import _extract_broker_entries

# Approx bytes per bar — used to estimate bar counts for known timeframes.
# SQ X .dat files store ohlcv + timestamp per bar in a custom layout; this
# heuristic was calibrated against BTCUSDT_M1 (~119 MB ≈ 4M bars from 2017-09).
_DAT_BYTES_PER_BAR = 28


def _parse_dat_header(path: Path, *, peek_bytes: int = 1024) -> dict[str, Any]:
    """Best-effort parse of the leading writeUTF-style strings in a SQ X .dat header.

    SQ X uses Java's DataOutputStream convention for the metadata block: each
    field is a 2-byte big-endian length followed by UTF-8 bytes. We walk those
    until a length looks implausible, then stop.
    """
    info: dict[str, Any] = {"strings": [], "header_bytes": 0}
    try:
        with path.open("rb") as f:
            raw = f.read(peek_bytes)
    except OSError as exc:
        return {"error": str(exc)}
    info["leading_hex"] = raw[:64].hex()
    offset = 0
    while offset + 2 <= len(raw):
        length = int.from_bytes(raw[offset : offset + 2], "big")
        if length == 0 or length > 64:
            break
        end = offset + 2 + length
        if end > len(raw):
            break
        try:
            s = raw[offset + 2 : end].decode("ascii")
        except UnicodeDecodeError:
            break
        if not s.isprintable():
            break
        info["strings"].append(s)
        offset = end
        info["header_bytes"] = offset
        if len(info["strings"]) >= 8:
            break
    return info


def _summarize_dat(p: Path) -> dict[str, Any]:
    try:
        stat = p.stat()
    except OSError as exc:
        return {"name": p.name, "error": str(exc)}
    header = _parse_dat_header(p)
    # Strip header bytes from estimate; remainder / bytes-per-bar = approximate bar count.
    data_bytes = max(0, stat.st_size - header.get("header_bytes", 0))
    estimated_bars = data_bytes // _DAT_BYTES_PER_BAR if data_bytes else 0
    return {
        "name": p.name,
        "size_bytes": stat.st_size,
        "size_mb": round(stat.st_size / (1024 * 1024), 2),
        "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "header_strings": header.get("strings"),
        "header_leading_hex": header.get("leading_hex"),
        "header_bytes": header.get("header_bytes"),
        "estimated_bar_count": estimated_bars,
    }


def _summarize_symbol_dir(sym_dir: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    total = 0
    for dat in sorted(sym_dir.glob("*.dat")):
        info = _summarize_dat(dat)
        files.append(info)
        total += info.get("size_bytes", 0) or 0
    return {
        "symbol": sym_dir.name,
        "dat_count": len(files),
        "total_bytes": total,
        "total_mb": round(total / (1024 * 1024), 2),
        "files": files,
    }


def _scan_instruments_fs(projects_dir) -> list[dict]:
    """Build the instrument inventory from every .cfx file under the projects dir.

    Used as a fallback for `instrument_list` because the engine command
    `-instrument action=list type=*` is broken in Build 143 (parser rejects
    every value for `type`).
    """
    if not projects_dir.exists():
        return []
    by_key: dict[str, dict] = {}
    for proj_dir in sorted(projects_dir.iterdir(), key=lambda p: p.name.lower()):
        cfx = proj_dir / "project.cfx"
        if not cfx.is_file():
            continue
        for row in _extract_broker_entries(cfx, proj_dir.name):
            if row["kind"] != "instrument":
                continue
            existing = by_key.get(row["key"])
            if existing is None:
                by_key[row["key"]] = {
                    "instrument": row["key"],
                    "broker": row["broker"],
                    "dataType": row["dataType"],
                    "exchange": row.get("exchange"),
                    "sector": row.get("sector"),
                    "country": row.get("country"),
                    "seen_in": [row["project"]],
                }
            elif row["project"] not in existing["seen_in"]:
                existing["seen_in"].append(row["project"])
    return list(by_key.values())


def _scan_symbols_fs(history_dir) -> list[dict]:
    """Enumerate data symbols from the local History/ directory."""
    if not history_dir.exists():
        return []
    out: list[dict] = []
    for entry in sorted(history_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        dat_files = sorted(entry.glob("*.dat"))
        if not dat_files:
            # Some dirs contain subfolders (e.g. sq_equity/A/...). Skip if no .dat.
            continue
        # Group dat files by what timeframes are present
        timeframes: set[str] = set()
        total_size = 0
        for f in dat_files:
            stem = f.stem  # e.g. GBPJPY_M1_dukas
            parts = stem.split("_")
            if len(parts) >= 2 and parts[-2] != entry.name and parts[-1] in ("dukas", "binance", "yahoo", "csv"):
                # symbol + tf + source
                timeframes.add(parts[-2])
            elif len(parts) >= 2:
                timeframes.add(parts[-1])
            try:
                total_size += f.stat().st_size
            except OSError:
                pass
        out.append(
            {
                "symbol": entry.name,
                "timeframes": sorted(timeframes),
                "dat_files": len(dat_files),
                "total_bytes": total_size,
            }
        )
    return out


class InstrumentAddArgs(BaseModel):
    instrument: str = Field(..., description="Instrument symbol (e.g. 'BTCUSDT').")
    description: str | None = None
    point_value: float | None = Field(None, description="Point value (e.g. 100000 for forex).", gt=0)
    tick_size: float | None = Field(None, description="Pip/Tick size (e.g. 0.0001).", gt=0)
    tick_step: float | None = Field(None, description="Pip/Tick step.", gt=0)
    default_spread: float | None = Field(None, ge=0)
    data_type: Literal["stock", "futures", "forex", "cfds", "etf", "index", "crypto"] = "forex"
    commissions: float | None = Field(None, ge=0)
    min_distance: float | None = Field(None, ge=0)
    order_size_multiplier: float | None = Field(None, gt=0)
    order_size_step: float | None = Field(None, gt=0)
    swap: float | None = None
    broker: str | None = Field(None, description="Broker name (default 'SQ Default').")

    @field_validator("instrument")
    @classmethod
    def _v_instrument(cls, v: str) -> str:
        return validate_symbol(v)


class InstrumentNameArgs(BaseModel):
    instrument: str = Field(..., description="Instrument symbol.")

    @field_validator("instrument")
    @classmethod
    def _v_instrument(cls, v: str) -> str:
        return validate_symbol(v)


class HistoryDataSummaryArgs(BaseModel):
    symbol: str | None = Field(
        None,
        description=(
            "Limit to a specific symbol directory under <data_dir>/History/. "
            "If omitted, summarize every symbol on disk."
        ),
    )

    @field_validator("symbol")
    @classmethod
    def _v_symbol(cls, v: str | None) -> str | None:
        return validate_symbol(v) if v else v


# ----- data registry (SQLite data.db) ---------------------------------------

_REGISTRY_TABLES = ("BROKER", "BROKER_STOCK", "DATA", "ELEMENTS", "INSTRUMENTS",
                    "SESSIONS", "STOCK", "STOCK_GROUP")


def _data_db_path(data_dir: Path) -> Path:
    return data_dir / "data.db"


def _epoch_ms_to_iso(ms: int | None) -> str | None:
    if ms is None or ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _enrich_data_row(row: dict[str, Any]) -> dict[str, Any]:
    """Add ISO-format dates to a DATA row."""
    out = dict(row)
    if "DATEFROM" in out:
        out["date_from_iso"] = _epoch_ms_to_iso(out["DATEFROM"])
    if "DATETO" in out:
        out["date_to_iso"] = _epoch_ms_to_iso(out["DATETO"])
    return out


def _open_data_registry(data_dir: Path) -> sqlite3.Connection | None:
    """Open data.db read-only. Returns None if missing/unreadable."""
    db = _data_db_path(data_dir)
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _query_data_registry(
    con: sqlite3.Connection, *, symbol: str | None = None, timeframe: str | None = None
) -> list[dict[str, Any]]:
    """Lookup rows in the DATA table by symbol and/or timeframe."""
    clauses: list[str] = []
    params: list[Any] = []
    if symbol:
        clauses.append("(SYMBOL = ? OR USYMBOL = ? OR INSTRUMENT = ?)")
        params.extend([symbol, symbol, symbol])
    if timeframe:
        clauses.append("TIMEFRAME = ?")
        params.append(timeframe)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cur = con.execute(f"SELECT * FROM DATA{where} ORDER BY SYMBOL, TIMEFRAME", params)
    return [_enrich_data_row(dict(r)) for r in cur.fetchall()]


class DataRegistryLookupArgs(BaseModel):
    symbol: str | None = Field(None, description="Exact symbol/usymbol/instrument match (e.g. 'BTCUSDT').")
    timeframe: str | None = Field(None, description="Timeframe filter (e.g. 'M1', 'H1').")

    @field_validator("symbol")
    @classmethod
    def _v_symbol(cls, v: str | None) -> str | None:
        return validate_symbol(v) if v else v

    @field_validator("timeframe")
    @classmethod
    def _v_tf(cls, v: str | None) -> str | None:
        return validate_timeframe(v) if v else v


class BrokerRegistryQueryArgs(BaseModel):
    table: Literal["BROKER", "BROKER_STOCK", "DATA", "ELEMENTS", "INSTRUMENTS",
                   "SESSIONS", "STOCK", "STOCK_GROUP"] = Field(
        "DATA", description="Which registry table to query."
    )
    where_column: str | None = Field(None, description="Optional WHERE column name.")
    where_value: str | None = Field(None, description="Optional WHERE value (string match, case-insensitive contains).")
    limit: int = Field(50, ge=1, le=1000, description="Max rows to return.")

    @field_validator("where_column")
    @classmethod
    def _v_col(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.replace("_", "").isalnum() or len(v) > 64:
            raise ValueError("where_column must be alphanumeric/underscore, <=64 chars")
        return v.upper()


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "List all configured instruments in SQ X. Build 143's `-instrument action=list` "
            "is broken (parser demands a value for `type` but rejects every value), so this "
            "falls back to mining `<InstrumentInfo>` blocks from every project's .cfx file."
        )
    )
    async def instrument_list(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            fs_instruments = _scan_instruments_fs(eng.config.projects_dir)
            try:
                text = await eng.call("-instrument action=list")
            except EngineError as exc:
                return {
                    "ok": True,
                    "instruments": [i["instrument"] for i in fs_instruments],
                    "instruments_detail": fs_instruments,
                    "source": "filesystem",
                    "fallback_reason": f"engine error: {exc}",
                }
            r = parse_listing_response(text, "instruments")
            if r["ok"] and r["instruments"] and not is_status_only_response(text):
                return r | {"source": "engine"}
            return {
                "ok": True,
                "instruments": [i["instrument"] for i in fs_instruments],
                "instruments_detail": fs_instruments,
                "source": "filesystem",
                "fallback_reason": (
                    "engine error" if not r["ok"]
                    else "engine output was status-only (redirected to file)"
                ),
                "engine_errors": r.get("errors"),
                "engine_raw": r.get("raw"),
            }
        except EngineError as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Add a new instrument (e.g. for a crypto pair not in SQ defaults).")
    async def instrument_add(args: InstrumentAddArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            parts = [f"-instrument action=add instrument={args.instrument}"]
            opt_map = {
                "description": args.description,
                "pointvalue": args.point_value,
                "ticksize": args.tick_size,
                "tickstep": args.tick_step,
                "defaultspread": args.default_spread,
                "datatype": args.data_type,
                "commissions": args.commissions,
                "minDistance": args.min_distance,
                "orderSizeMultiplier": args.order_size_multiplier,
                "orderSizeStep": args.order_size_step,
                "swap": args.swap,
                "broker": args.broker,
            }
            for k, v in opt_map.items():
                if v is not None:
                    parts.append(f"{k}={v}")
            text = await eng.call(" ".join(parts))
            return parse_response(text) | {"instrument": args.instrument}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Delete an instrument from SQ X.")
    async def instrument_delete(args: InstrumentNameArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            await ctx.warning(f"deleting instrument {args.instrument}")
            text = await eng.call(f"-instrument action=delete instruments={args.instrument}")
            return parse_response(text) | {"instrument": args.instrument}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "List all data symbols (e.g. EURUSD_M1_dukas). Falls back to a filesystem "
            "scan of <data_dir>/History when sqcli redirects its output to a file "
            "and only the status line ('Data listed.') is visible over HTTP."
        )
    )
    async def symbol_list(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            fs_symbols = _scan_symbols_fs(eng.config.history_dir)
            try:
                text = await eng.call("-symbol action=list")
            except EngineError as exc:
                return {
                    "ok": True,
                    "symbols": [s["symbol"] for s in fs_symbols],
                    "symbols_detail": fs_symbols,
                    "source": "filesystem",
                    "fallback_reason": f"engine error: {exc}",
                }
            r = parse_listing_response(text, "symbols")
            if r["ok"] and r["symbols"] and not is_status_only_response(text):
                return r | {"source": "engine"}
            return {
                "ok": True,
                "symbols": [s["symbol"] for s in fs_symbols],
                "symbols_detail": fs_symbols,
                "source": "filesystem",
                "fallback_reason": (
                    "engine error" if not r["ok"]
                    else "engine output was status-only (redirected to file)"
                ),
                "engine_errors": r.get("errors"),
                "engine_raw": r.get("raw"),
            }
        except EngineError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Summarize the local .dat history files for one or every symbol under "
            "<data_dir>/History/. Reports file size, mtime, header magic strings "
            "(Java DataOutputStream-style writeUTF prefix), and an estimated bar "
            "count derived from file size. Pure filesystem read — no engine call. "
            "Note: .dat first/last bar timestamps are not extractable without the "
            "engine; use mtime + size for freshness."
        )
    )
    async def history_data_summary(
        args: HistoryDataSummaryArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            root = eng.config.history_dir
            if not root.exists():
                return {"ok": False, "error": f"history dir not found: {root}"}
            con = _open_data_registry(eng.config.data_dir)
            try:
                if args.symbol:
                    d = root / args.symbol
                    if not d.is_dir():
                        return {"ok": False, "error": f"symbol dir not found: {d}"}
                    summary = _summarize_symbol_dir(d)
                    if con is not None:
                        summary["registry"] = _query_data_registry(con, symbol=args.symbol)
                    return {
                        "ok": True,
                        "history_dir": str(root),
                        "registry_db": str(_data_db_path(eng.config.data_dir)) if con else None,
                        "symbol_count": 1,
                        "symbols": [summary],
                    }
                summaries: list[dict] = []
                for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                    if not entry.is_dir():
                        continue
                    if not any(entry.glob("*.dat")):
                        continue
                    summary = _summarize_symbol_dir(entry)
                    if con is not None:
                        summary["registry"] = _query_data_registry(con, symbol=entry.name)
                    summaries.append(summary)
                return {
                    "ok": True,
                    "history_dir": str(root),
                    "registry_db": str(_data_db_path(eng.config.data_dir)) if con else None,
                    "symbol_count": len(summaries),
                    "symbols": summaries,
                }
            finally:
                if con is not None:
                    con.close()
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Look up authoritative metadata for a symbol/timeframe combo from SQ X's "
            "SQLite registry (`data.db`). Returns DATEFROM/DATETO (as both epoch ms and "
            "ISO), ROW count, BROKER_ID, SOURCE, USYMBOL, etc. — the ground-truth metadata "
            "for what data is available. Works while the engine is running."
        )
    )
    async def data_registry_lookup(args: DataRegistryLookupArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            con = _open_data_registry(eng.config.data_dir)
            if con is None:
                return {
                    "ok": False,
                    "error": f"data.db not readable at {_data_db_path(eng.config.data_dir)}",
                }
            try:
                rows = _query_data_registry(con, symbol=args.symbol, timeframe=args.timeframe)
                return {
                    "ok": True,
                    "registry_db": str(_data_db_path(eng.config.data_dir)),
                    "filter": {"symbol": args.symbol, "timeframe": args.timeframe},
                    "row_count": len(rows),
                    "rows": rows,
                }
            finally:
                con.close()
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Read a registry table from SQ X's SQLite `data.db`. Tables: BROKER (broker IDs "
            "and names), INSTRUMENTS (instrument metadata: point value, tick size, data type), "
            "DATA (symbol/timeframe rows with date ranges), STOCK / STOCK_GROUP / SESSIONS / "
            "ELEMENTS. Optionally filter by a single column matching a value (case-insensitive "
            "contains). Read-only — no writes."
        )
    )
    async def broker_registry_query(args: BrokerRegistryQueryArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            con = _open_data_registry(eng.config.data_dir)
            if con is None:
                return {
                    "ok": False,
                    "error": f"data.db not readable at {_data_db_path(eng.config.data_dir)}",
                }
            try:
                params: list[Any] = []
                where = ""
                if args.where_column and args.where_value is not None:
                    cur = con.execute(f"PRAGMA table_info({args.table})")
                    valid_cols = {row["name"].upper() for row in cur.fetchall()}
                    if args.where_column not in valid_cols:
                        return {
                            "ok": False,
                            "error": f"column {args.where_column!r} not in table {args.table}",
                            "available_columns": sorted(valid_cols),
                        }
                    where = f" WHERE UPPER(CAST({args.where_column} AS TEXT)) LIKE ?"
                    params.append(f"%{args.where_value.upper()}%")
                cur = con.execute(
                    f"SELECT * FROM {args.table}{where} LIMIT ?", params + [args.limit]
                )
                rows = [dict(r) for r in cur.fetchall()]
                if args.table == "DATA":
                    rows = [_enrich_data_row(r) for r in rows]
                return {
                    "ok": True,
                    "registry_db": str(_data_db_path(eng.config.data_dir)),
                    "table": args.table,
                    "row_count": len(rows),
                    "rows": rows,
                }
            finally:
                con.close()
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)
