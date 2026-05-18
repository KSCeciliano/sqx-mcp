"""Human-friendly report generators — CSV exports + Markdown decks.

Tools that turn databank scans into shareable artifacts:

- ``portfolio_export_csv`` — dump every strategy in a databank to a CSV
  with all the metrics ``derive_metrics`` produces. Great for spreadsheet
  pivoting / portfolio building outside SQ X.
- ``portfolio_deck_markdown`` — emit a single Markdown document covering
  the top-N strategies: headline numbers, audit verdict, equity stats,
  per-strategy summary section. Writes to a file *and* returns the text.
- ``strategy_deck_one`` — same idea but for one .sqx (outside any project).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_databank_name,
    validate_project_name,
)
from sq_mcp.engine import EngineError
from sq_mcp.parsers.sqx import derive_metrics, parse_sqx
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.audit import _audit_strategy_metrics
from sq_mcp.tools.pipeline import _finding_to_dict, _verdict_from_findings
from sq_mcp.tools.portfolio import _filter_min_trades, _rank_rows, _scan_databank
from sq_mcp.tools.strategy_inspect import (
    _equity_drawdown_stats,
    _hit_ratio,
    _select_curve,
    _summarize_text,
)

_CSV_COLUMNS = (
    "rel",
    "strategy_name",
    "symbol",
    "timeframe",
    "trades",
    "net_profit",
    "drawdown_abs",
    "drawdown_pct",
    "return_pct",
    "profit_to_dd_ratio",
    "avg_trade",
    "trades_per_year",
    "fitness_is",
    "fitness_oos",
    "fitness_full",
    "oos_is_ratio",
    "history_years",
    "initial_capital",
    "trades_hash",
    "fingerprint_exact",
)


class PortfolioExportCsvArgs(BaseModel):
    project: str
    databank: str = "Results"
    output_path: str = Field(
        ...,
        description="Where to write the CSV (e.g. '/tmp/results.csv'). Parent created if missing.",
    )
    min_trades: int | None = None
    rank_mode: str | None = Field(
        None,
        description=(
            "Optional ranking to apply before writing. None = workspace order."
        ),
    )
    top_n: int | None = Field(
        None,
        ge=1,
        le=10000,
        description="If set, write only the top N rows after ranking.",
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class PortfolioDeckArgs(BaseModel):
    project: str
    databank: str = "Results"
    n: int = Field(10, ge=1, le=50)
    rank_mode: str = Field("defensive")
    min_trades: int | None = 30
    output_path: str | None = Field(
        None,
        description="Where to write the markdown. None = return text only.",
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class StrategyDeckOneArgs(BaseModel):
    sqx_path: str


class PortfolioReviewBundleArgs(BaseModel):
    project: str
    databank: str = "Results"
    n: int = Field(10, ge=1, le=50)
    rank_mode: str = "defensive"
    min_trades: int | None = 30
    output_dir: str = Field(
        ...,
        description="Directory to write the tarball + manifest into.",
    )
    label: str | None = Field(None, max_length=64)

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class WorkspaceSummaryMarkdownArgs(BaseModel):
    output_path: str | None = Field(
        None,
        description="Where to write the markdown. None = return text only.",
    )


def _row_for_csv(r: dict[str, Any]) -> dict[str, Any]:
    return {c: r.get(c) for c in _CSV_COLUMNS}


def _markdown_for_one_strategy(
    *, info: Any, metrics: dict[str, Any], findings: list[Any]
) -> str:
    verdict = _verdict_from_findings(findings, block_on=["critical", "high"])
    curve = _select_curve(info, "full")
    stats = _equity_drawdown_stats(curve)
    stats["hit_ratio"] = _hit_ratio(curve)
    lines = _summarize_text(metrics, verdict)

    out = io.StringIO()
    out.write(f"### {metrics.get('strategy_name') or '(unnamed)'}\n\n")
    for line in lines:
        out.write(f"- {line}\n")
    out.write("\n**Equity:**\n\n")
    for k in (
        "max_drawdown_pct_of_peak",
        "longest_underwater",
        "recovery_factor",
        "ends_at_new_high",
        "hit_ratio",
    ):
        out.write(f"- {k}: `{stats.get(k)}`\n")
    if verdict["traffic_light"] != "green":
        out.write("\n**Findings:**\n\n")
        for f in findings:
            d = _finding_to_dict(f)
            out.write(f"- *{d.get('severity')}* `{d.get('code')}`: {d.get('message')}\n")
    out.write("\n")
    return out.getvalue()


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Write a CSV of every strategy in a databank with all the metrics "
            "derive_metrics produces. Optional rank_mode + top_n filter rows "
            "before writing. Returns the file path and row count."
        )
    )
    async def portfolio_export_csv(
        args: PortfolioExportCsvArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / args.project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}

            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            if args.rank_mode:
                rows = _rank_rows(rows, args.rank_mode)
            if args.top_n is not None:
                rows = rows[: args.top_n]

            out_path = resolve_safe_path(args.output_path, must_exist=False)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
                writer.writeheader()
                for r in rows:
                    writer.writerow(_row_for_csv(r))
            return {
                "ok": True,
                "csv_path": str(out_path),
                "row_count": len(rows),
                "unparseable_count": len(bad),
                "columns": list(_CSV_COLUMNS),
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Generate a Markdown deck for the top-N strategies of a databank: "
            "headline numbers, traffic-light verdict, equity geometry, findings. "
            "Optional output_path writes to disk; otherwise the markdown is "
            "returned in the response."
        )
    )
    async def portfolio_deck_markdown(
        args: PortfolioDeckArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / args.project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}

            rows, bad = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            ranked = _rank_rows(rows, args.rank_mode)[: args.n]

            out = io.StringIO()
            out.write(f"# Portfolio deck — {args.project} / {args.databank}\n\n")
            out.write(
                f"Generated {datetime.now(tz=timezone.utc).isoformat()} — "
                f"top {len(ranked)} of {len(rows)} (rank_mode=`{args.rank_mode}`)\n\n"
            )
            for r in ranked:
                info = parse_sqx(Path(r["file"]))
                m = derive_metrics(info)
                findings = _audit_strategy_metrics(m)
                out.write(_markdown_for_one_strategy(
                    info=info, metrics=m, findings=findings
                ))

            text = out.getvalue()
            wrote_to: str | None = None
            if args.output_path:
                p = resolve_safe_path(args.output_path, must_exist=False)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
                wrote_to = str(p)

            return {
                "ok": True,
                "project": args.project,
                "databank": databank,
                "wrote_to": wrote_to,
                "picked_count": len(ranked),
                "unparseable_count": len(bad),
                "markdown": text,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Generate a Markdown deck for a single .sqx file (outside any project). "
            "Returns markdown text only — caller picks where to write it."
        )
    )
    async def strategy_deck_one(
        args: StrategyDeckOneArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.sqx_path, must_exist=True)
            if p.suffix.lower() != ".sqx":
                return {"ok": False, "error": "expected .sqx"}
            info = parse_sqx(p)
            m = derive_metrics(info)
            findings = _audit_strategy_metrics(m)
            md = _markdown_for_one_strategy(info=info, metrics=m, findings=findings)
            return {"ok": True, "sqx_path": str(p), "markdown": md}
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Generate a Markdown summary of the entire workspace: every project, "
            "its task XML count, databanks, total .sqx files. Use to publish or "
            "share the current workspace state. Optional output_path writes to disk."
        )
    )
    async def workspace_summary_markdown(
        args: WorkspaceSummaryMarkdownArgs, ctx: Context
    ) -> dict:
        try:
            from datetime import datetime as _datetime
            from datetime import timezone as _tz

            from sq_mcp.tools.batch import _count_databanks_for_project
            from sq_mcp.tools.projects import _scan_projects_fs
            eng = get_engine(ctx)
            scanned = _scan_projects_fs(eng.config.projects_dir)
            out = io.StringIO()
            out.write(f"# Workspace summary — {eng.config.sqx_home}\n\n")
            out.write(
                f"Generated {_datetime.now(tz=_tz.utc).isoformat()} — "
                f"{len(scanned)} projects on disk\n\n"
            )
            total_sqx = 0
            total_databanks = 0
            for p in scanned:
                pd = eng.config.projects_dir / p["name"]
                inv = _count_databanks_for_project(pd)
                total_sqx += inv["sqx_files_total"]
                total_databanks += inv["databanks"]
                out.write(f"## {p['name']}\n\n")
                out.write(f"- cfx_size: `{p['cfx_size']}` bytes\n")
                out.write(f"- cfx_mtime: `{p['cfx_mtime']}`\n")
                out.write(f"- databanks: `{inv['databanks']}`\n")
                out.write(f"- total .sqx: `{inv['sqx_files_total']}`\n")
                if inv["by_databank"]:
                    out.write("\n  Per-databank:\n")
                    for db in inv["by_databank"]:
                        out.write(f"  - `{db['name']}`: {db['sqx_count']} .sqx\n")
                out.write("\n")
            out.write(f"\n**Totals:** {total_databanks} databanks, {total_sqx} .sqx files.\n")
            text = out.getvalue()
            wrote_to: str | None = None
            if args.output_path:
                p_out = resolve_safe_path(args.output_path, must_exist=False)
                p_out.parent.mkdir(parents=True, exist_ok=True)
                p_out.write_text(text, encoding="utf-8")
                wrote_to = str(p_out)
            return {
                "ok": True,
                "wrote_to": wrote_to,
                "project_count": len(scanned),
                "total_databanks": total_databanks,
                "total_sqx": total_sqx,
                "markdown": text,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Bundle the top-N strategies of a databank into a single tar.gz for "
            "offline review or sharing. Contains: each .sqx file, a Markdown "
            "deck per strategy, a portfolio CSV, and a manifest.json. Use to "
            "send to a teammate or archive for later review."
        )
    )
    async def portfolio_review_bundle(
        args: PortfolioReviewBundleArgs, ctx: Context
    ) -> dict:
        try:
            import csv as _csv
            import io as _io
            import json as _json
            import tarfile as _tarfile
            from datetime import datetime as _datetime
            from datetime import timezone as _tz

            eng = get_engine(ctx)
            databank = validate_databank_name(args.databank)
            db_dir = eng.config.projects_dir / args.project / "databanks" / databank
            if not db_dir.exists():
                return {"ok": False, "error": f"databank not found: {db_dir}"}
            rows, _ = _scan_databank(db_dir)
            rows = _filter_min_trades(rows, args.min_trades)
            ranked = _rank_rows(rows, args.rank_mode)[: args.n]

            out_dir = resolve_safe_path(args.output_dir, must_exist=False)
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = _datetime.now(tz=_tz.utc).strftime("%Y%m%dT%H%M%SZ")
            suffix = f"-{args.label}" if args.label else ""
            archive = out_dir / f"review-{args.project}-{databank}-{ts}{suffix}.tar.gz"

            # Build CSV in memory
            csv_buf = _io.StringIO()
            writer = _csv.DictWriter(csv_buf, fieldnames=_CSV_COLUMNS)
            writer.writeheader()
            for r in ranked:
                writer.writerow(_row_for_csv(r))
            csv_bytes = csv_buf.getvalue().encode("utf-8")

            # Deck in memory
            deck_buf = _io.StringIO()
            deck_buf.write(f"# {args.project} / {databank} — top {len(ranked)}\n\n")
            deck_buf.write(f"Generated {_datetime.now(tz=_tz.utc).isoformat()}\n\n")
            manifest_entries: list[dict[str, Any]] = []
            with _tarfile.open(archive, "w:gz") as tar:
                for r in ranked:
                    src = Path(r["file"])
                    if not src.is_file():
                        continue
                    arcname = f"strategies/{src.name}"
                    tar.add(src, arcname=arcname)
                    info = parse_sqx(src)
                    m = derive_metrics(info)
                    findings = _audit_strategy_metrics(m)
                    md = _markdown_for_one_strategy(info=info, metrics=m, findings=findings)
                    deck_buf.write(md)
                    manifest_entries.append(
                        {
                            "rel": r["rel"],
                            "sqx_filename": src.name,
                            "strategy_name": m.get("strategy_name"),
                            "trades": m.get("trades"),
                            "fitness_oos": m.get("fitness_oos"),
                            "drawdown_pct": m.get("drawdown_pct"),
                            "verdict_findings": [f.code for f in findings],
                        }
                    )

                # Add deck and CSV
                deck_bytes = deck_buf.getvalue().encode("utf-8")
                for name, payload in (
                    ("deck.md", deck_bytes),
                    ("portfolio.csv", csv_bytes),
                ):
                    ti = _tarfile.TarInfo(name=name)
                    ti.size = len(payload)
                    ti.mtime = int(_datetime.now(tz=_tz.utc).timestamp())
                    tar.addfile(ti, _io.BytesIO(payload))

                # Manifest
                manifest = {
                    "project": args.project,
                    "databank": databank,
                    "created_at": _datetime.now(tz=_tz.utc).isoformat(),
                    "rank_mode": args.rank_mode,
                    "min_trades": args.min_trades,
                    "label": args.label,
                    "entries": manifest_entries,
                }
                mp = _json.dumps(manifest, indent=2).encode("utf-8")
                ti = _tarfile.TarInfo(name="manifest.json")
                ti.size = len(mp)
                ti.mtime = int(_datetime.now(tz=_tz.utc).timestamp())
                tar.addfile(ti, _io.BytesIO(mp))

            return {
                "ok": True,
                "archive_path": str(archive),
                "archive_size_bytes": archive.stat().st_size,
                "strategies_bundled": len(manifest_entries),
                "manifest_entries": manifest_entries,
            }
        except (EngineError, ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "PortfolioDeckArgs",
    "PortfolioExportCsvArgs",
    "PortfolioReviewBundleArgs",
    "StrategyDeckOneArgs",
    "WorkspaceSummaryMarkdownArgs",
    "_CSV_COLUMNS",
    "_markdown_for_one_strategy",
    "_row_for_csv",
    "register",
]
