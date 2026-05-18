"""Cross-project search + symbol-centric views.

Answers questions like "where are all my BTCUSDT strategies?" or "what do I
have on disk for ETHUSDT?" by scanning every project's databanks at once.

Tools:

- ``workspace_find_strategies`` — fan out across every project's databanks,
  filter by symbol / timeframe / min_trades, return one row per matching
  strategy with the project + databank it lives in.
- ``symbol_overview`` — gather everything about one symbol: on-disk .dat
  inventory, registry rows, which projects reference it, strategies that
  trade on it across the workspace.
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
from sq_mcp.tools.integrity import _index_disk_files
from sq_mcp.tools.portfolio import _scan_databank
from sq_mcp.tools.projects import _scan_projects_fs
from sq_mcp.tools.symbols import (
    _open_data_registry,
    _query_data_registry,
)


class WorkspaceFindStrategiesArgs(BaseModel):
    symbol: str | None = Field(None, description="Filter to this symbol (None = any).")
    timeframe: str | None = Field(None, description="Filter to this timeframe (None = any).")
    min_trades: int | None = Field(None, description="Drop strategies with fewer trades.")
    only_databank: str | None = Field(
        None,
        description="Restrict scan to a specific databank name across every project.",
    )
    max_results: int = Field(500, ge=1, le=5000)

    @field_validator("symbol")
    @classmethod
    def _v_symbol(cls, v: str | None) -> str | None:
        return validate_symbol(v) if v else v

    @field_validator("timeframe")
    @classmethod
    def _v_tf(cls, v: str | None) -> str | None:
        return validate_timeframe(v) if v else v


class SymbolOverviewArgs(BaseModel):
    symbol: str

    @field_validator("symbol")
    @classmethod
    def _v_symbol(cls, v: str) -> str:
        return validate_symbol(v)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Scan every project's databanks and return strategies matching the "
            "given filters (symbol/timeframe/min_trades). Use this when looking "
            "for 'all my BTCUSDT survivors' or 'every strategy with >100 trades' "
            "across the workspace. Read-only."
        )
    )
    async def workspace_find_strategies(
        args: WorkspaceFindStrategiesArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            projects = _scan_projects_fs(eng.config.projects_dir)
            results: list[dict[str, Any]] = []
            unparseable_total = 0
            for p in projects:
                project_dir = eng.config.projects_dir / p["name"]
                db_root = project_dir / "databanks"
                if not db_root.is_dir():
                    continue
                for db_dir in sorted(db_root.iterdir()):
                    if not db_dir.is_dir():
                        continue
                    if args.only_databank and db_dir.name != args.only_databank:
                        continue
                    rows, bad = _scan_databank(db_dir)
                    unparseable_total += len(bad)
                    for r in rows:
                        if args.symbol and r.get("symbol") != args.symbol:
                            continue
                        if args.timeframe and r.get("timeframe") != args.timeframe:
                            continue
                        if (
                            args.min_trades is not None
                            and (r.get("trades") or 0) < args.min_trades
                        ):
                            continue
                        results.append(
                            {
                                "project": p["name"],
                                "databank": db_dir.name,
                                "rel": r.get("rel"),
                                "file": r.get("file"),
                                "strategy_name": r.get("strategy_name"),
                                "symbol": r.get("symbol"),
                                "timeframe": r.get("timeframe"),
                                "trades": r.get("trades"),
                                "fitness_oos": r.get("fitness_oos"),
                                "drawdown_pct": r.get("drawdown_pct"),
                                "trades_hash": r.get("trades_hash"),
                            }
                        )
                        if len(results) >= args.max_results:
                            break
                    if len(results) >= args.max_results:
                        break
                if len(results) >= args.max_results:
                    break
            return {
                "ok": True,
                "filters": {
                    "symbol": args.symbol,
                    "timeframe": args.timeframe,
                    "min_trades": args.min_trades,
                    "only_databank": args.only_databank,
                },
                "projects_scanned": len(projects),
                "match_count": len(results),
                "unparseable_total": unparseable_total,
                "matches": results,
                "truncated": len(results) >= args.max_results,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "One-call comprehensive view of a single symbol: on-disk .dat files "
            "(any timeframe), data.db registry rows, which projects reference it, "
            "and how many strategies in the workspace trade it. Read-only."
        )
    )
    async def symbol_overview(args: SymbolOverviewArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            # Disk
            disk_idx = _index_disk_files(eng.config.history_dir)
            disk_entries: list[dict[str, Any]] = []
            for (sym, tf), paths in disk_idx.items():
                if sym == args.symbol:
                    for p in paths:
                        try:
                            st = p.stat()
                            disk_entries.append(
                                {
                                    "path": str(p),
                                    "timeframe": tf,
                                    "size_bytes": st.st_size,
                                    "mtime": datetime.fromtimestamp(
                                        st.st_mtime, tz=timezone.utc
                                    ).isoformat(),
                                }
                            )
                        except OSError:
                            continue
            # Registry
            con = _open_data_registry(eng.config.data_dir)
            registry_rows: list[dict[str, Any]] = []
            if con is not None:
                try:
                    registry_rows = _query_data_registry(con, symbol=args.symbol)
                finally:
                    con.close()
            # Projects referencing it (via cfx_inspect logic — read each cfx)
            from sq_mcp.tools.projects import _referenced_symbols_from_cfx
            referencing_projects: list[str] = []
            for p in _scan_projects_fs(eng.config.projects_dir):
                project_dir = eng.config.projects_dir / p["name"]
                cfx = project_dir / "project.cfx"
                if not cfx.is_file():
                    continue
                try:
                    refs = _referenced_symbols_from_cfx(cfx)
                except OSError:
                    refs = set()
                # Match either exact symbol or base symbol (e.g. BTCUSDT_M1_dukas → BTCUSDT)
                for sym in refs:
                    base = sym.split("_")[0]
                    if sym == args.symbol or base == args.symbol:
                        referencing_projects.append(p["name"])
                        break
            # Count strategies trading on this symbol (regardless of project)
            strategy_count = 0
            for p in _scan_projects_fs(eng.config.projects_dir):
                project_dir = eng.config.projects_dir / p["name"]
                db_root = project_dir / "databanks"
                if not db_root.is_dir():
                    continue
                for db_dir in db_root.iterdir():
                    if not db_dir.is_dir():
                        continue
                    rows, _ = _scan_databank(db_dir)
                    strategy_count += sum(
                        1 for r in rows if r.get("symbol") == args.symbol
                    )
            return {
                "ok": True,
                "symbol": args.symbol,
                "disk_dat_count": len(disk_entries),
                "disk_dat": disk_entries,
                "registry_row_count": len(registry_rows),
                "registry_rows": registry_rows,
                "referencing_project_count": len(referencing_projects),
                "referencing_projects": sorted(set(referencing_projects)),
                "strategies_in_workspace_using_symbol": strategy_count,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "SymbolOverviewArgs",
    "WorkspaceFindStrategiesArgs",
    "register",
]

# Suppress unused warning for Path
_ = Path
