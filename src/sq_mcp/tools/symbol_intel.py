"""Symbol intelligence — naming, coverage matrix, freshness reports.

Tools that summarize what historical data the workspace has, how fresh
it is, and how a symbol's name might appear in different exchanges or
in MT5 broker conventions. Pure file-system / SQLite analysis — no
engine round-trip.

Tools:

- ``symbol_normalize`` — canonical-case + common alias mapping for a
  symbol string. Handles BTCUSDT → BTC/USDT, BTC-USD, BTCUSD, etc.
- ``symbol_timeframe_matrix`` — for every symbol on disk, report which
  timeframes have .dat files and total size per row. Use to spot
  symbols with thin TF coverage before kicking off a build.
- ``symbol_freshness_report`` — sort symbols by stalest mtime; flag any
  data older than ``stale_days``.
- ``symbol_data_summary`` — single-symbol deep dive: all timeframes,
  per-file size, header strings, age.
- ``symbol_coverage_gaps`` — compare requested (symbol, timeframe) pairs
  against what's on disk. Returns the missing ones — feed to
  ``data_import`` to fill the gaps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    validate_symbol,
    validate_timeframe,
)
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload

# Canonical timeframes we expect on disk
EXPECTED_TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1")


# ---- argument schemas ------------------------------------------------------


class SymbolNormalizeArgs(BaseModel):
    symbol: str

    @field_validator("symbol")
    @classmethod
    def _v(cls, v: str) -> str:
        # Don't run validate_symbol — we want to normalize whatever the user typed
        if not v or len(v) > 64:
            raise ValueError("symbol must be 1-64 chars")
        return v.strip()


class SymbolMatrixArgs(BaseModel):
    timeframes: list[str] = Field(default_factory=lambda: list(EXPECTED_TIMEFRAMES))
    include_size: bool = True


class SymbolFreshnessArgs(BaseModel):
    stale_days: int = Field(7, ge=1, le=3650)
    max_rows: int = Field(50, ge=1, le=1000)


class SymbolDataSummaryArgs(BaseModel):
    symbol: str

    @field_validator("symbol")
    @classmethod
    def _v(cls, v: str) -> str:
        return validate_symbol(v)


class SymbolCoverageGapsArgs(BaseModel):
    requested: list[dict[str, str]] = Field(..., min_length=1, max_length=200)

    @field_validator("requested")
    @classmethod
    def _v_each(cls, items: list[dict[str, str]]) -> list[dict[str, str]]:
        for it in items:
            sym = it.get("symbol", "")
            tf = it.get("timeframe", "")
            validate_symbol(sym)
            validate_timeframe(tf)
        return items


# ---- helpers ---------------------------------------------------------------


def _aliases(symbol: str) -> dict[str, Any]:
    """Produce common alias forms for a symbol.

    Handles crypto perp/spot suffixes (USDT, USD, BUSD) and forex 6-char
    pairs.
    """
    sym = symbol.upper().replace(" ", "")
    out: dict[str, Any] = {"input": symbol, "canonical": sym, "aliases": []}

    # Strip common separators for canonical
    canonical = sym.replace("/", "").replace("-", "").replace("_", "")
    out["canonical"] = canonical

    # Crypto patterns: 3-6 char base + USDT / USD / BUSD / EUR
    crypto_quotes = ("USDT", "USDC", "BUSD", "USD", "EUR", "BTC", "ETH")
    for q in crypto_quotes:
        if canonical.endswith(q) and len(canonical) > len(q):
            base = canonical[: -len(q)]
            out["base"] = base
            out["quote"] = q
            out["aliases"] = sorted({
                canonical,
                f"{base}/{q}",
                f"{base}-{q}",
                f"{base}_{q}",
                f"{base}.{q}",
            })
            return out

    # Forex 6-char: AAAxBBB
    if len(canonical) == 6 and canonical.isalpha():
        base, quote = canonical[:3], canonical[3:]
        out["base"] = base
        out["quote"] = quote
        out["aliases"] = sorted({
            canonical,
            f"{base}/{quote}",
            f"{base}-{quote}",
        })
        return out

    # Default: just return the canonical form as the only alias
    out["aliases"] = [canonical]
    return out


def _history_dir(eng) -> Path:  # noqa: ANN001
    return Path(eng.config.data_dir) / "History"


def _symbol_subdirs(history_dir: Path) -> list[Path]:
    if not history_dir.is_dir():
        return []
    return sorted([d for d in history_dir.iterdir() if d.is_dir()])


def _dat_files(symbol_dir: Path) -> list[Path]:
    return sorted(symbol_dir.glob("*.dat"))


def _parse_tf_from_filename(filename: str) -> str | None:
    """SQ X .dat names look like ``BTCUSDT_M1.dat``. Return ``M1``."""
    if "_" not in filename:
        return None
    base = filename.rsplit(".dat", 1)[0]
    parts = base.split("_")
    if len(parts) < 2:
        return None
    return parts[-1]


def _matrix_row(symbol: str, dats: list[Path], timeframes: list[str], include_size: bool) -> dict[str, Any]:
    by_tf: dict[str, Path] = {}
    for p in dats:
        tf = _parse_tf_from_filename(p.name)
        if tf:
            by_tf[tf] = p
    row: dict[str, Any] = {"symbol": symbol, "has_count": 0, "missing": []}
    for tf in timeframes:
        if tf in by_tf:
            row["has_count"] += 1
            p = by_tf[tf]
            entry: dict[str, Any] = {"present": True}
            if include_size:
                try:
                    s = p.stat()
                    entry["size_mb"] = round(s.st_size / (1024 * 1024), 2)
                    entry["mtime"] = datetime.fromtimestamp(s.st_mtime, tz=timezone.utc).isoformat()
                except OSError:
                    pass
            row[tf] = entry
        else:
            row[tf] = {"present": False}
            row["missing"].append(tf)
    return row


def _freshness_rows(history_dir: Path, stale_days: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    cutoff_secs = stale_days * 86400
    for sym_dir in _symbol_subdirs(history_dir):
        for p in _dat_files(sym_dir):
            try:
                s = p.stat()
            except OSError:
                continue
            age_secs = now.timestamp() - s.st_mtime
            rows.append(
                {
                    "symbol": sym_dir.name,
                    "timeframe": _parse_tf_from_filename(p.name),
                    "size_mb": round(s.st_size / (1024 * 1024), 2),
                    "mtime": datetime.fromtimestamp(s.st_mtime, tz=timezone.utc).isoformat(),
                    "age_days": round(age_secs / 86400, 2),
                    "is_stale": age_secs > cutoff_secs,
                }
            )
    return rows


def _coverage_gaps(
    requested: list[dict[str, str]], history_dir: Path
) -> list[dict[str, Any]]:
    present: set[tuple[str, str]] = set()
    for sym_dir in _symbol_subdirs(history_dir):
        for p in _dat_files(sym_dir):
            tf = _parse_tf_from_filename(p.name)
            if tf:
                present.add((sym_dir.name, tf))
    gaps = []
    for r in requested:
        key = (r["symbol"], r["timeframe"])
        if key not in present:
            gaps.append({"symbol": r["symbol"], "timeframe": r["timeframe"]})
    return gaps


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Normalize a symbol string and produce common alias forms (BTCUSDT, "
            "BTC/USDT, BTC-USDT, etc.). Handles crypto quote pairs (USDT, USDC, "
            "BUSD, USD) and 6-char forex pairs. Useful before searching the "
            "broker registry or matching MT5 symbol naming. Pure string ops."
        )
    )
    async def symbol_normalize(args: SymbolNormalizeArgs) -> dict:
        return {"ok": True, **_aliases(args.symbol)}

    @mcp.tool(
        description=(
            "Coverage matrix: for every symbol with a History/ dir, report which "
            "of the requested timeframes have .dat files on disk and (optionally) "
            "their size + mtime. Returns per-symbol rows including a `missing` "
            "list of timeframes. Read-only."
        )
    )
    async def symbol_timeframe_matrix(args: SymbolMatrixArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            history = _history_dir(eng)
            if not history.is_dir():
                return {"ok": False, "error": f"History dir not found: {history}"}
            rows: list[dict[str, Any]] = []
            for sym_dir in _symbol_subdirs(history):
                dats = _dat_files(sym_dir)
                rows.append(_matrix_row(sym_dir.name, dats, args.timeframes, args.include_size))
            return {
                "ok": True,
                "history_dir": str(history),
                "symbol_count": len(rows),
                "timeframes": args.timeframes,
                "rows": rows,
            }
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Rank every .dat file in History/ by mtime. Flag files older than "
            "stale_days. Use before re-running long backtests on data that "
            "might no longer reflect recent market state. Read-only."
        )
    )
    async def symbol_freshness_report(args: SymbolFreshnessArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            history = _history_dir(eng)
            if not history.is_dir():
                return {"ok": False, "error": f"History dir not found: {history}"}
            rows = _freshness_rows(history, args.stale_days)
            rows.sort(key=lambda r: r["age_days"], reverse=True)
            stale = [r for r in rows if r["is_stale"]]
            return {
                "ok": True,
                "history_dir": str(history),
                "stale_days_threshold": args.stale_days,
                "total_files": len(rows),
                "stale_count": len(stale),
                "rows": rows[: args.max_rows],
            }
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Single-symbol deep dive: all timeframes on disk with per-file size, "
            "header strings (best-effort UTF parse), and age. Read-only."
        )
    )
    async def symbol_data_summary(args: SymbolDataSummaryArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            history = _history_dir(eng)
            sym_dir = history / args.symbol
            if not sym_dir.is_dir():
                return {"ok": False, "error": f"no History dir for symbol: {args.symbol}"}
            from sq_mcp.tools.symbols import _summarize_dat
            files = [_summarize_dat(p) for p in _dat_files(sym_dir)]
            return {
                "ok": True,
                "symbol": args.symbol,
                "symbol_dir": str(sym_dir),
                "file_count": len(files),
                "total_size_mb": round(sum(f.get("size_bytes", 0) for f in files) / (1024 * 1024), 2),
                "files": files,
            }
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Given a list of (symbol, timeframe) pairs the user wants, return the "
            "subset that is not on disk. Use to drive a fill-the-gaps data_import "
            "loop. Read-only."
        )
    )
    async def symbol_coverage_gaps(args: SymbolCoverageGapsArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            history = _history_dir(eng)
            if not history.is_dir():
                return {"ok": False, "error": f"History dir not found: {history}"}
            gaps = _coverage_gaps(args.requested, history)
            return {
                "ok": True,
                "requested_count": len(args.requested),
                "missing_count": len(gaps),
                "missing": gaps,
            }
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)
