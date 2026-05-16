"""Historical data tools — import, update, export, list."""

from __future__ import annotations

from typing import Literal

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
