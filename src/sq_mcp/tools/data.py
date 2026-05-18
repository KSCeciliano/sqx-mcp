"""Historical data tools — import, update, export, list, coverage check."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_date,
    validate_project_name,
    validate_symbol,
    validate_timeframe,
)
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import (
    get_engine,
    parse_list,
    parse_response,
    safe_error_payload,
)


class DataImportArgs(BaseModel):
    symbol: str = Field(..., description="Symbol to import, e.g. 'BTCUSDT'.")
    filepath: str = Field(..., description="Path to source data file (CSV / MT4 bar / etc.).")
    instrument: str | None = Field(None, description="Instrument template name in SQ.")
    timeframe: str = Field("auto", description="auto / TICK / M1 / M5 / M15 / M30 / H1 / H4 / D1.")
    timezone: str | None = Field(None, description="Source timezone (e.g. 'Etc/UTC', 'America/New_York').")
    bar_type: Literal["startofbar", "endofbar"] = Field("endofbar")
    error_handling: Literal["stop", "ignore"] = Field("stop")
    date_from: str | None = Field(None, description="yyyy.MM.dd")
    date_to: str | None = Field(None, description="yyyy.MM.dd")

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


class DataUpdateArgs(BaseModel):
    symbols: list[str] | None = Field(
        None, description="Specific symbols to update (e.g. ['BTCUSDT_M1', 'ETHUSDT_M1']). Omit to update all."
    )

    @field_validator("symbols")
    @classmethod
    def _v_symbols(cls, v: list[str] | None) -> list[str] | None:
        return [validate_symbol(s) for s in v] if v else v


class DataExportArgs(BaseModel):
    symbols: list[str] = Field(..., min_length=1, description="Symbols to export.")
    timeframe: str = Field("M1")
    output_dir: str = Field(..., description="Target directory.")
    format: str = Field("MetaTrader4 bar format", description="See sqcli -data help for full list.")
    date_from: str | None = Field(None)
    date_to: str | None = Field(None)

    @field_validator("symbols")
    @classmethod
    def _v_symbols(cls, v: list[str]) -> list[str]:
        return [validate_symbol(s) for s in v]

    @field_validator("timeframe")
    @classmethod
    def _v_tf(cls, v: str) -> str:
        return validate_timeframe(v)

    @field_validator("date_from", "date_to")
    @classmethod
    def _v_date(cls, v: str | None) -> str | None:
        return validate_date(v) if v else v


class DataCoverageCheckArgs(BaseModel):
    """Verify local data covers a requested test window.

    Exactly one of (project) or (symbol + timeframe + date_from + date_to) must be set.
    """
    project: str | None = Field(
        None,
        description=(
            "Project name. If set, the tool mines referenced symbols and date "
            "ranges from the project's .cfx and checks each."
        ),
    )
    symbol: str | None = Field(None, description="Explicit symbol to check.")
    timeframe: str | None = Field("M1", description="Explicit timeframe.")
    date_from: str | None = Field(None, description="Requested start (yyyy.MM.dd).")
    date_to: str | None = Field(None, description="Requested end (yyyy.MM.dd).")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str | None) -> str | None:
        return validate_project_name(v) if v else v

    @field_validator("symbol")
    @classmethod
    def _v_symbol(cls, v: str | None) -> str | None:
        return validate_symbol(v) if v else v

    @field_validator("timeframe")
    @classmethod
    def _v_tf(cls, v: str | None) -> str | None:
        return validate_timeframe(v) if v else v

    @field_validator("date_from", "date_to")
    @classmethod
    def _v_date(cls, v: str | None) -> str | None:
        return validate_date(v) if v else v


def _check_symbol_coverage(
    *, eng, symbol: str, timeframe: str | None, date_from: str | None, date_to: str | None
) -> dict:
    """Return a coverage report for one (symbol, tf, date_window) tuple."""
    from sq_mcp.tools.symbols import _open_data_registry, _query_data_registry

    history_root = eng.config.history_dir
    base = symbol.split("_")[0]
    history_dir = None
    for cand in (history_root / symbol, history_root / base):
        if cand.is_dir():
            history_dir = cand
            break

    out: dict = {
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_date_from": date_from,
        "requested_date_to": date_to,
        "history_dir_present": history_dir is not None,
        "history_dir": str(history_dir) if history_dir else None,
        "issues": [],
    }

    # .dat presence
    dat_count = 0
    dat_size_total = 0
    if history_dir is not None:
        try:
            for f in history_dir.glob("*.dat"):
                dat_count += 1
                try:
                    dat_size_total += f.stat().st_size
                except OSError:
                    pass
        except OSError:
            pass
    out["dat_count"] = dat_count
    out["dat_size_bytes"] = dat_size_total
    if dat_count == 0:
        out["issues"].append(
            {"severity": "high", "code": "NO_LOCAL_DAT",
             "message": f"no .dat history under {history_root}/{symbol} (or /{base})"}
        )

    # registry
    con = _open_data_registry(eng.config.data_dir)
    registry_rows = []
    if con is not None:
        try:
            registry_rows = _query_data_registry(con, symbol=symbol, timeframe=timeframe)
        finally:
            con.close()
    out["registry_rows"] = registry_rows
    if not registry_rows:
        out["issues"].append(
            {"severity": "high", "code": "NOT_IN_REGISTRY",
             "message": f"symbol {symbol!r} TF {timeframe!r} not in data.db DATA table"}
        )
    else:
        # Window check against the first registry row (most relevant TF match)
        row = registry_rows[0]
        reg_from_ms = row.get("DATEFROM")
        reg_to_ms = row.get("DATETO")
        if date_from and reg_from_ms:
            try:
                y, m, d = date_from.split(".")
                req_ms = int(datetime(int(y), int(m), int(d), tzinfo=timezone.utc).timestamp() * 1000)
                if req_ms < int(reg_from_ms):
                    out["issues"].append(
                        {"severity": "high", "code": "REQUESTED_BEFORE_AVAILABLE",
                         "message": (
                            f"requested date_from {date_from} predates available data "
                            f"start ({row.get('date_from_iso') or reg_from_ms})"
                         )}
                    )
            except (ValueError, KeyError):
                pass
        if date_to and reg_to_ms:
            try:
                y, m, d = date_to.split(".")
                req_ms = int(datetime(int(y), int(m), int(d), tzinfo=timezone.utc).timestamp() * 1000)
                if req_ms > int(reg_to_ms):
                    out["issues"].append(
                        {"severity": "medium", "code": "REQUESTED_AFTER_AVAILABLE",
                         "message": (
                            f"requested date_to {date_to} extends past available data "
                            f"end ({row.get('date_to_iso') or reg_to_ms}) — re-import "
                            "or data_update first"
                         )}
                    )
            except (ValueError, KeyError):
                pass
    out["ok"] = not out["issues"]
    return out


def register(mcp: FastMCP) -> None:
    @mcp.tool(description="Import historical data into SQ X from a file.")
    async def data_import(args: DataImportArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            src = resolve_safe_path(args.filepath, must_exist=True)
            parts = [
                "-data action=import",
                f"symbol={args.symbol}",
                f"filepath={src}",
                f"timeframe={args.timeframe}",
                f"bartype={args.bar_type}",
                f"errorhandling={args.error_handling}",
            ]
            if args.instrument:
                parts.append(f"instrument={args.instrument}")
            if args.timezone:
                parts.append(f"timezone={args.timezone}")
            if args.date_from:
                parts.append(f"datefrom={args.date_from}")
            if args.date_to:
                parts.append(f"dateto={args.date_to}")
            text = await eng.call(" ".join(parts), timeout=900.0)
            return parse_response(text) | {"symbol": args.symbol, "filepath": str(src)}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Update existing data symbols (refresh from broker / data source).")
    async def data_update(args: DataUpdateArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cmd = "-data action=update"
            if args.symbols:
                cmd += f" symbols={','.join(args.symbols)}"
            text = await eng.call(cmd, timeout=900.0)
            return parse_response(text)
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Export data to CSV / MT format / etc.")
    async def data_export(args: DataExportArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            out = resolve_safe_path(args.output_dir)
            parts = [
                "-data action=export",
                f"symbols={','.join(args.symbols)}",
                f"timeframe={args.timeframe}",
                f"outputdir={out}",
                f'format="{args.format}"',
            ]
            if args.date_from:
                parts.append(f"datefrom={args.date_from}")
            if args.date_to:
                parts.append(f"dateto={args.date_to}")
            text = await eng.call(" ".join(parts), timeout=600.0)
            return parse_response(text)
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="List symbols + timeframes already present in the local SQ X data folder.")
    async def data_list_local(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            history = eng.config.history_dir
            if not history.exists():
                return {"ok": False, "error": f"history dir missing: {history}"}
            out = []
            for sym_dir in sorted(history.iterdir()):
                if not sym_dir.is_dir():
                    continue
                tfs = sorted(
                    f.stem.replace(f"{sym_dir.name}_", "")
                    for f in sym_dir.glob("*.dat")
                )
                out.append({"symbol": sym_dir.name, "timeframes": tfs})
            return {"ok": True, "history_dir": str(history), "symbols": out}
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="List available timezones for data import.")
    async def data_timezones(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            text = await eng.call("-data action=timezones")
            return {"ok": True, "timezones": parse_list(text), "raw": text.strip()}
        except EngineError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Verify that local data actually covers a requested backtest window. "
            "Three-way cross-check: .dat file presence under history/, data.db DATA "
            "table date range, and the requested window. Two modes: pass `project=` "
            "to mine referenced symbols/dates from the project's .cfx, or pass "
            "`symbol`/`timeframe`/`date_from`/`date_to` for an explicit one-off "
            "check. Returns coverage findings per symbol with severity-tagged issues "
            "(NO_LOCAL_DAT, NOT_IN_REGISTRY, REQUESTED_BEFORE_AVAILABLE, "
            "REQUESTED_AFTER_AVAILABLE). Call BEFORE kicking off a Builder/Retester."
        )
    )
    async def data_coverage_check(args: DataCoverageCheckArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            if not args.project and not args.symbol:
                return {
                    "ok": False,
                    "error": "must supply either project or symbol",
                }
            reports: list[dict] = []
            if args.project:
                from sq_mcp.tools.projects import (
                    _referenced_dates_from_cfx,
                    _referenced_symbols_from_cfx,
                )
                cfx_path = eng.config.projects_dir / args.project / "project.cfx"
                if not cfx_path.is_file():
                    return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
                referenced = sorted(_referenced_symbols_from_cfx(cfx_path))
                dates = _referenced_dates_from_cfx(cfx_path)
                df_list = dates.get("date_from") or []
                dt_list = dates.get("date_to") or []
                # Pick the strictest window from the task XMLs (use min date_from,
                # max date_to) for coverage checks.
                df = next((d for d in sorted(df_list) if "." in d), None)
                dt = next(
                    (d for d in sorted(dt_list, reverse=True) if "." in d), None
                )
                for sym in referenced:
                    reports.append(
                        _check_symbol_coverage(
                            eng=eng,
                            symbol=sym,
                            timeframe=args.timeframe,
                            date_from=df,
                            date_to=dt,
                        )
                    )
            else:
                reports.append(
                    _check_symbol_coverage(
                        eng=eng,
                        symbol=args.symbol,
                        timeframe=args.timeframe,
                        date_from=args.date_from,
                        date_to=args.date_to,
                    )
                )
            blocker_count = sum(
                1 for r in reports for i in r["issues"] if i["severity"] == "high"
            )
            return {
                "ok": blocker_count == 0,
                "project": args.project,
                "report_count": len(reports),
                "blocker_count": blocker_count,
                "warning_count": sum(
                    1 for r in reports for i in r["issues"] if i["severity"] == "medium"
                ),
                "reports": reports,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)
