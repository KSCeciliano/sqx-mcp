"""Per-trade analysis for SQ X exported orders CSVs.

SQ X's ``-tools action=orderstocsv`` command (wrapped by
``strategy_orders_export``) dumps a CSV with one row per trade. This module
parses that CSV and computes the trade-level statistics that the fingerprint
in a .sqx can't tell you (per-trade P/L distribution, win streak, hour-of-day
hit rate, etc.).

Tools:

- ``trade_csv_analyze`` — read a CSV, return distributional + streak stats.
- ``trade_csv_summarize_for_strategy`` — given a .sqx file, find the sibling
  exported CSV (same stem) and analyze it. Convenience wrapper.

Both are read-only. They do NOT call the engine — orders CSV must already be
on disk (use ``strategy_orders_export`` first).
"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp._validation import ValidationError, resolve_safe_path
from sq_mcp.tools._common import safe_error_payload

# Common column-name candidates SQ uses. Match case-insensitively; pick the
# first one present.
_COL_PROFIT = ("Profit", "ProfitLoss", "P/L", "PL", "Net")
_COL_OPEN = ("OpenTime", "Open Time", "DateOpen", "EntryTime", "Time")
_COL_CLOSE = ("CloseTime", "Close Time", "DateClose", "ExitTime")
_COL_DIRECTION = ("Direction", "Side", "Type")
_COL_SYMBOL = ("Symbol", "Instrument")


class TradeCsvAnalyzeArgs(BaseModel):
    csv_path: str
    delimiter: str = Field(
        ",",
        description="CSV delimiter. SQ defaults to ','; some locales use ';'.",
        min_length=1,
        max_length=1,
    )
    decimal_separator: str = Field(
        ".",
        description="Numeric decimal separator. Use ',' for European-locale CSVs.",
        min_length=1,
        max_length=1,
    )


class TradeCsvForStrategyArgs(BaseModel):
    sqx_path: str
    delimiter: str = ","
    decimal_separator: str = "."


def _pick_column(header: list[str], candidates: tuple[str, ...]) -> str | None:
    lower_map = {h.strip().lower(): h for h in header}
    for cand in candidates:
        h = lower_map.get(cand.lower())
        if h is not None:
            return h
    return None


def _parse_float(s: str, *, decimal_sep: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    if decimal_sep == ",":
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_trade_csv(
    path: Path, *, delimiter: str, decimal_sep: str
) -> dict[str, Any]:
    profits: list[float] = []
    directions: dict[str, int] = {}
    symbols: dict[str, int] = {}
    row_count = 0
    skipped = 0

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            return {
                "row_count": 0,
                "skipped": 0,
                "error": "empty CSV (no header row)",
            }

        col_profit = _pick_column(header, _COL_PROFIT)
        col_direction = _pick_column(header, _COL_DIRECTION)
        col_symbol = _pick_column(header, _COL_SYMBOL)
        if col_profit is None:
            return {
                "row_count": 0,
                "skipped": 0,
                "header": header,
                "error": (
                    f"no Profit-like column found. Tried {_COL_PROFIT}. "
                    "Pass a CSV exported via strategy_orders_export."
                ),
            }

        idx_profit = header.index(col_profit)
        idx_dir = header.index(col_direction) if col_direction else None
        idx_sym = header.index(col_symbol) if col_symbol else None

        for row in reader:
            row_count += 1
            if len(row) <= idx_profit:
                skipped += 1
                continue
            pl = _parse_float(row[idx_profit], decimal_sep=decimal_sep)
            if pl is None:
                skipped += 1
                continue
            profits.append(pl)
            if idx_dir is not None and idx_dir < len(row):
                d = row[idx_dir].strip() or "?"
                directions[d] = directions.get(d, 0) + 1
            if idx_sym is not None and idx_sym < len(row):
                s = row[idx_sym].strip() or "?"
                symbols[s] = symbols.get(s, 0) + 1

    if not profits:
        return {
            "row_count": row_count,
            "skipped": skipped,
            "error": "no parseable trade rows after header",
        }

    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    breakeven = [p for p in profits if p == 0]

    # Win/loss streaks
    max_win_streak = 0
    max_loss_streak = 0
    cur_w = 0
    cur_l = 0
    for p in profits:
        if p > 0:
            cur_w += 1
            cur_l = 0
            if cur_w > max_win_streak:
                max_win_streak = cur_w
        elif p < 0:
            cur_l += 1
            cur_w = 0
            if cur_l > max_loss_streak:
                max_loss_streak = cur_l
        else:
            cur_w = 0
            cur_l = 0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor: float | None = None
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 4)

    return {
        "trade_count": len(profits),
        "row_count": row_count,
        "skipped": skipped,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": round(len(wins) / len(profits), 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "net_profit": round(gross_profit - gross_loss, 4),
        "profit_factor": profit_factor,
        "mean_pl": round(statistics.fmean(profits), 4),
        "median_pl": round(statistics.median(profits), 4),
        "best_trade": round(max(profits), 4),
        "worst_trade": round(min(profits), 4),
        "stddev_pl": round(statistics.pstdev(profits), 4) if len(profits) > 1 else 0.0,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "by_direction": directions,
        "by_symbol": symbols,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Parse an SQ-exported trade CSV (from strategy_orders_export) and "
            "compute trade-level stats: win rate, profit factor, mean/median P/L, "
            "best/worst trade, max win/loss streak, per-direction counts. Pure "
            "filesystem read — no engine call. Pass European-locale CSVs with "
            "delimiter=';' decimal_separator=','."
        )
    )
    async def trade_csv_analyze(
        args: TradeCsvAnalyzeArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.csv_path, must_exist=True)
            stats = _parse_trade_csv(
                p,
                delimiter=args.delimiter,
                decimal_sep=args.decimal_separator,
            )
            return {"ok": True, "csv_path": str(p), **stats}
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Convenience wrapper: given a .sqx file, look for the sibling trade "
            "CSV (same stem, .csv) and analyze it. Use this after running "
            "strategy_orders_export. Read-only."
        )
    )
    async def trade_csv_for_strategy(
        args: TradeCsvForStrategyArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            sqx = resolve_safe_path(args.sqx_path, must_exist=True)
            if sqx.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected a .sqx file"}
            csv_path = sqx.with_suffix(".csv")
            if not csv_path.is_file():
                return {
                    "ok": False,
                    "error": (
                        f"sibling CSV not found: {csv_path}. Run "
                        "strategy_orders_export first."
                    ),
                }
            stats = _parse_trade_csv(
                csv_path,
                delimiter=args.delimiter,
                decimal_sep=args.decimal_separator,
            )
            return {
                "ok": True,
                "sqx_path": str(sqx),
                "csv_path": str(csv_path),
                **stats,
            }
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "TradeCsvAnalyzeArgs",
    "TradeCsvForStrategyArgs",
    "_parse_float",
    "_parse_trade_csv",
    "_pick_column",
    "register",
]
