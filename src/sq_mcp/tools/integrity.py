"""Cross-checks between SQ X's internal state and the on-disk filesystem.

These tools verify that the picture of "what symbols/data are configured" you
get from the SQLite registry (``user/data/data.db``) matches the actual
``.dat`` files in ``user/data/History/``. The two can drift when:

- A symbol was added in the GUI but the data fetch failed silently.
- Files were ``rm -rf``'d from disk without going through SQ X.
- A Binance native pull crashed partway through, leaving a partial file.

Tools:

- ``broker_data_integrity`` — full cross-check, with orphans called out
  on both sides + per-row size/mtime detail.
- ``broker_data_integrity_quick`` — same idea but returns only the counts
  and the orphan lists (no per-file detail), so the agent can decide if
  a deeper look is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_symbol, validate_timeframe
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.symbols import (
    _enrich_data_row,
    _open_data_registry,
    _scan_symbols_fs,
    _summarize_dat,
)

# ---- argument schemas ------------------------------------------------------


class BrokerDataIntegrityArgs(BaseModel):
    symbol: str | None = Field(
        None,
        description="Restrict the check to this symbol only (e.g. 'BTCUSDT').",
    )
    timeframe: str | None = Field(
        None,
        description="Restrict the check to this timeframe (e.g. 'M1', 'H1').",
    )
    include_details: bool = Field(
        True,
        description="When False, only counts + orphan lists (saves response size).",
    )

    @field_validator("symbol")
    @classmethod
    def _v_symbol(cls, v: str | None) -> str | None:
        return validate_symbol(v) if v else v

    @field_validator("timeframe")
    @classmethod
    def _v_tf(cls, v: str | None) -> str | None:
        return validate_timeframe(v) if v else v


# ---- helpers ---------------------------------------------------------------


def _index_disk_files(history_dir: Path) -> dict[tuple[str, str], list[Path]]:
    """Map (symbol, timeframe) → list of .dat paths under ``history_dir``.

    The filename layout SQ X uses is ``<symbol>_<TF>_<source>.dat`` (or older
    builds: ``<symbol>_<TF>.dat`` with no source suffix). We accept both.
    """
    out: dict[tuple[str, str], list[Path]] = {}
    if not history_dir.exists():
        return out
    for symbol_dir in history_dir.iterdir():
        if not symbol_dir.is_dir():
            continue
        sym = symbol_dir.name
        for dat in symbol_dir.glob("*.dat"):
            stem = dat.stem
            parts = stem.split("_")
            if len(parts) < 2:
                continue
            # source suffix is at the tail when present
            if len(parts) >= 3 and parts[-1] in (
                "dukas", "binance", "yahoo", "csv", "metatrader", "ducas"
            ):
                tf = parts[-2]
            else:
                tf = parts[-1]
            out.setdefault((sym, tf), []).append(dat)
    return out


def _index_registry_rows(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        sym = r.get("SYMBOL") or r.get("USYMBOL") or r.get("INSTRUMENT")
        tf = r.get("TIMEFRAME")
        if not sym or not tf:
            continue
        out.setdefault((str(sym), str(tf)), []).append(r)
    return out


def _compare_indexes(
    on_disk: dict[tuple[str, str], list[Path]],
    in_registry: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Compute per-pair status: matched / disk_only / registry_only."""
    disk_keys = set(on_disk.keys())
    reg_keys = set(in_registry.keys())
    matched = sorted(disk_keys & reg_keys)
    disk_only = sorted(disk_keys - reg_keys)
    registry_only = sorted(reg_keys - disk_keys)
    return {
        "matched_pairs": matched,
        "disk_only_pairs": disk_only,
        "registry_only_pairs": registry_only,
    }


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Cross-check the SQ X SQLite data registry (data.db DATA table) against "
            "the on-disk History/<symbol>/*.dat files. Reports: pairs (symbol/TF) "
            "present in both, on disk only (registry forgot to record it), and "
            "in registry only (SQ thinks data exists but the .dat is missing). "
            "Optionally narrow by symbol and/or timeframe. Read-only."
        )
    )
    async def broker_data_integrity(
        args: BrokerDataIntegrityArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            data_dir = eng.config.data_dir
            history_dir = eng.config.history_dir
            con = _open_data_registry(data_dir)
            registry_rows: list[dict[str, Any]] = []
            if con is None:
                registry_status = "registry-missing-or-unreadable"
            else:
                try:
                    cur = con.execute("SELECT * FROM DATA")
                    registry_rows = [_enrich_data_row(dict(r)) for r in cur.fetchall()]
                    registry_status = "ok"
                finally:
                    con.close()

            if args.symbol or args.timeframe:
                def _matches(row: dict[str, Any]) -> bool:
                    sym = row.get("SYMBOL") or row.get("USYMBOL") or row.get("INSTRUMENT")
                    tf = row.get("TIMEFRAME")
                    if args.symbol and sym != args.symbol:
                        return False
                    if args.timeframe and tf != args.timeframe:
                        return False
                    return True
                registry_rows = [r for r in registry_rows if _matches(r)]

            disk_index = _index_disk_files(history_dir)
            if args.symbol or args.timeframe:
                disk_index = {
                    k: v
                    for k, v in disk_index.items()
                    if (not args.symbol or k[0] == args.symbol)
                    and (not args.timeframe or k[1] == args.timeframe)
                }

            registry_index = _index_registry_rows(registry_rows)
            cmp = _compare_indexes(disk_index, registry_index)

            details: dict[str, Any] = {}
            if args.include_details:
                disk_only_details = [
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "files": [_summarize_dat(p) for p in disk_index[(sym, tf)]],
                    }
                    for sym, tf in cmp["disk_only_pairs"]
                ]
                registry_only_details = [
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "rows": registry_index[(sym, tf)],
                    }
                    for sym, tf in cmp["registry_only_pairs"]
                ]
                details = {
                    "disk_only_details": disk_only_details,
                    "registry_only_details": registry_only_details,
                }

            return {
                "ok": True,
                "history_dir": str(history_dir),
                "data_db": str(data_dir / "data.db"),
                "registry_status": registry_status,
                "registry_row_count": len(registry_rows),
                "disk_pair_count": len(disk_index),
                "matched_count": len(cmp["matched_pairs"]),
                "disk_only_count": len(cmp["disk_only_pairs"]),
                "registry_only_count": len(cmp["registry_only_pairs"]),
                "matched_pairs": [f"{s}_{tf}" for s, tf in cmp["matched_pairs"]],
                "disk_only_pairs": [f"{s}_{tf}" for s, tf in cmp["disk_only_pairs"]],
                "registry_only_pairs": [
                    f"{s}_{tf}" for s, tf in cmp["registry_only_pairs"]
                ],
                **details,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Filesystem inventory of every History/ symbol directory: counts, total "
            "bytes, timeframes present. Read-only. Useful right before disk-cleanup "
            "or before a Binance native pull, so the agent knows what's already on disk."
        )
    )
    async def history_disk_inventory(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            history_dir = eng.config.history_dir
            inv = _scan_symbols_fs(history_dir)
            return {
                "ok": True,
                "history_dir": str(history_dir),
                "symbol_count": len(inv),
                "total_bytes": sum(s["total_bytes"] for s in inv),
                "total_mb": round(sum(s["total_bytes"] for s in inv) / (1024 * 1024), 2),
                "symbols": inv,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "BrokerDataIntegrityArgs",
    "_compare_indexes",
    "_index_disk_files",
    "_index_registry_rows",
    "register",
]
