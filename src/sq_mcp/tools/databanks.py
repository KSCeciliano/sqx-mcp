"""Databank tools — list, count, load/save .sqx, export to CSV/XLSX."""

from __future__ import annotations

import difflib
import shutil
import zipfile
from pathlib import Path
from typing import Any, Literal

from lxml import etree
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_databank_name,
    validate_project_name,
    validate_strategy_list,
)
from sq_mcp._xml import safe_fromstring
from sq_mcp.engine import EngineError
from sq_mcp.parsers import parse_sqx
from sq_mcp.parsers.sqx import derive_metrics
from sq_mcp.tools._common import (
    get_engine,
    is_status_only_response,
    parse_list,
    parse_listing_response,
    parse_response,
    safe_error_payload,
)

_TEXTY_EXTS = (".xml", ".txt", ".mf", ".java", ".js", ".json", ".ini", ".properties")
_SQX_DIFF_DEFAULT_FILES = ("settings.xml", "lastSettings.xml", "strategy_Portfolio.xml", "version.txt")


def _extract_sqx_contents(
    path,
    *,
    include_xml: bool,
    include_text: bool,
    include_bin_listing: bool,
    max_text_size: int,
) -> dict[str, Any]:
    """Open a .sqx (zip) and pull text-like entries plus optional binary listing."""
    if not zipfile.is_zipfile(path):
        return {"ok": False, "error": f"{path} is not a valid .sqx (zip) file"}
    text_entries: list[dict[str, Any]] = []
    bin_entries: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                base = name.rsplit("/", 1)[-1].lower()
                is_xml = base.endswith(".xml")
                is_text = base.endswith(_TEXTY_EXTS)
                if is_text and ((is_xml and include_xml) or (not is_xml and include_text)):
                    raw = z.read(name)
                    truncated = False
                    if max_text_size and len(raw) > max_text_size:
                        raw = raw[:max_text_size]
                        truncated = True
                    content = raw.decode("utf-8", errors="replace")
                    text_entries.append(
                        {
                            "name": name,
                            "size": info.file_size,
                            "content": content,
                            "truncated": truncated,
                        }
                    )
                elif include_bin_listing:
                    bin_entries.append({"name": name, "size": info.file_size})
    except (zipfile.BadZipFile, OSError) as exc:
        return {"ok": False, "error": f"failed to read .sqx: {exc}"}
    return {
        "ok": True,
        "file_count": len(text_entries) + len(bin_entries),
        "text_files": text_entries,
        "binary_files": bin_entries,
    }


def _canonicalize_xml(raw: bytes) -> str:
    """Pretty-print XML with sorted attributes so diffs aren't noisy on reorderings."""
    try:
        root = safe_fromstring(raw)
    except etree.XMLSyntaxError:
        return raw.decode("utf-8", errors="replace")
    # Sort attributes on every element for stable comparison
    for el in root.iter():
        if el.attrib:
            sorted_items = sorted(el.attrib.items())
            el.attrib.clear()
            for k, v in sorted_items:
                el.set(k, v)
    return etree.tostring(root, pretty_print=True, encoding="unicode")


def _read_sqx_member(path: Path, member: str) -> bytes | None:
    """Read one named member from a .sqx zip. Returns None if missing."""
    try:
        with zipfile.ZipFile(path) as z:
            try:
                return z.read(member)
            except KeyError:
                return None
    except (zipfile.BadZipFile, OSError):
        return None


def _diff_sqx_files(
    a: Path, b: Path, *, members: tuple[str, ...], context_lines: int
) -> dict[str, Any]:
    """Generate a unified diff per requested .sqx member."""
    per_member: dict[str, dict[str, Any]] = {}
    total_added = total_removed = total_changed = 0
    for member in members:
        ra = _read_sqx_member(a, member)
        rb = _read_sqx_member(b, member)
        if ra is None and rb is None:
            continue
        if ra is None:
            per_member[member] = {"status": "only_in_b", "lines_b": (rb or b"").decode("utf-8", "replace").count("\n")}
            total_added += per_member[member]["lines_b"]
            continue
        if rb is None:
            per_member[member] = {"status": "only_in_a", "lines_a": ra.decode("utf-8", "replace").count("\n")}
            total_removed += per_member[member]["lines_a"]
            continue
        if member.endswith(".xml"):
            text_a = _canonicalize_xml(ra).splitlines(keepends=True)
            text_b = _canonicalize_xml(rb).splitlines(keepends=True)
        else:
            text_a = ra.decode("utf-8", "replace").splitlines(keepends=True)
            text_b = rb.decode("utf-8", "replace").splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                text_a, text_b, fromfile=f"a/{member}", tofile=f"b/{member}",
                n=context_lines,
            )
        )
        added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
        changed = min(added, removed)
        total_added += added
        total_removed += removed
        total_changed += changed
        if diff_lines:
            per_member[member] = {
                "status": "changed",
                "added": added,
                "removed": removed,
                "diff": "".join(diff_lines),
            }
        else:
            per_member[member] = {"status": "identical"}
    return {
        "summary": {
            "members_compared": len(members),
            "lines_added": total_added,
            "lines_removed": total_removed,
            "lines_changed_approx": total_changed,
        },
        "per_member": per_member,
    }


def _scan_databanks_fs(project_dir) -> list[dict]:
    """Enumerate databank folders under <project>/databanks/."""
    db_root = project_dir / "databanks"
    if not db_root.exists():
        return []
    out: list[dict] = []
    for entry in sorted(db_root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        sqx = sorted(entry.rglob("*.sqx"))
        out.append(
            {
                "name": entry.name,
                "sqx_count": len(sqx),
                "path": str(entry),
            }
        )
    return out


class DatabankRefArgs(BaseModel):
    project: str = Field(..., description="Project name.")
    name: str | None = Field(None, description="Databank name (defaults to 'Results' on the SQ side if omitted).")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str | None) -> str | None:
        return validate_databank_name(v) if v else v


class DatabankCountArgs(DatabankRefArgs):
    pass


class DatabankSaveLoadArgs(DatabankRefArgs):
    folder: str = Field(..., description="Subfolder under the project's databanks/ dir.")
    strategies: list[str] | None = Field(
        None, description="Optional list of strategy names to limit the operation to."
    )

    @field_validator("strategies")
    @classmethod
    def _v_strategies(cls, v: list[str] | None) -> list[str] | None:
        return validate_strategy_list(v) if v else v


class DatabankExportArgs(DatabankRefArgs):
    file: str = Field(..., description="Output path (.csv or .xlsx).")
    view: str | None = Field(None, description="Databank view name to use.")


class SqxInspectArgs(BaseModel):
    path: str = Field(..., description="Path to a .sqx file.")


class SqxExtractArgs(BaseModel):
    path: str = Field(..., description="Path to a .sqx file.")
    include_xml: bool = Field(
        True, description="Include settings.xml / lastSettings.xml / strategy_Portfolio.xml etc."
    )
    include_text: bool = Field(
        True, description="Include non-XML text files (version.txt, MANIFEST.MF, source files)."
    )
    include_bin_listing: bool = Field(
        True,
        description="If True, also list binary files (orders.bin, dailyEquity.bin) with name + size only.",
    )
    max_text_size: int = Field(
        64_000,
        ge=0,
        le=1_000_000,
        description="Max bytes per text file to return inline (truncated above).",
    )


class SqxDiffArgs(BaseModel):
    path_a: str = Field(..., description="First .sqx file.")
    path_b: str = Field(..., description="Second .sqx file.")
    members: list[str] | None = Field(
        None,
        description=(
            "Optional list of files inside the .sqx to diff. "
            "Defaults to settings.xml, lastSettings.xml, strategy_Portfolio.xml, version.txt."
        ),
    )
    context_lines: int = Field(
        3, ge=0, le=20, description="Unified-diff context line count."
    )


class StrategyOrdersExportArgs(BaseModel):
    sqx_path: str = Field(..., description="Path to a .sqx file. Must not contain spaces (sqcli parser limitation — copy/rename first if needed).")
    output_format: Literal["csv", "xlsx"] = Field(
        "csv", description="Output format. CSV is fast and analysis-friendly."
    )
    use_comma: bool = Field(False, description="Use comma as decimal separator (regional preference). Default semicolon-delimited.")
    data_scope: Literal["main", "all"] = Field(
        "main", description="'main' = only the primary backtest, 'all' = every retest result block."
    )


class DatabankTopNArgs(BaseModel):
    project: str = Field(..., description="Project name.")
    databank: str = Field(
        "Results",
        description="Databank folder name under the project (e.g. 'Results', 'Final').",
    )
    top_n: int = Field(10, ge=1, le=1000, description="Number of strategies to return.")
    sort_by: Literal["fitness_is", "fitness_oos", "fitness_full"] = Field(
        "fitness_oos",
        description=(
            "Which fitness value to rank by. fitness_oos is the most defensive choice. "
            "fitness_is is the raw build score; fitness_full is the combined IS+OOS curve."
        ),
    )
    descending: bool = Field(True, description="Sort highest-first (default) or lowest-first.")
    result_name: str | None = Field(
        None,
        description=(
            "If a .sqx has multiple Result blocks (portfolio + per-test), pick this one. "
            "Default: use the first non-portfolio result. Use 'portfolio' to rank by portfolio fitness."
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


# Metrics that databank_filter and databank_rank can be sorted on / filtered by.
_FILTERABLE_METRICS = (
    "fitness_is",
    "fitness_oos",
    "fitness_full",
    "fitness_strategy",
    "trades",
    "net_profit",
    "drawdown_abs",
    "return_pct",
    "drawdown_pct",
    "profit_to_dd_ratio",
    "avg_trade",
    "trades_per_year",
    "oos_is_ratio",
    "complexity",
    "history_years",
)


class DatabankFilterArgs(BaseModel):
    project: str = Field(..., description="Project name.")
    databank: str = Field(
        "Results",
        description="Databank folder name (under project's databanks/).",
    )
    # filter thresholds — every field is optional; omitted means no constraint
    min_trades: int | None = Field(None, ge=0)
    max_trades: int | None = Field(None, ge=0)
    min_net_profit: float | None = None
    max_drawdown_abs: float | None = Field(None, ge=0)
    max_drawdown_pct: float | None = Field(None, ge=0, le=100)
    min_profit_to_dd_ratio: float | None = Field(None, ge=0)
    min_fitness_is: float | None = None
    min_fitness_oos: float | None = None
    min_oos_is_ratio: float | None = Field(
        None,
        description=(
            "Minimum OOS/IS fitness ratio (0..1). Use ~0.7 to demand minimal IS→OOS "
            "degradation. Strategies missing OOS data are excluded when this is set."
        ),
    )
    min_avg_trade: float | None = None
    min_trades_per_year: float | None = Field(None, ge=0)
    max_complexity: int | None = Field(None, ge=1)
    exclude_ambiguous: bool = Field(
        False,
        description="If True, drop strategies whose Fingerprint reports ambiguous trades.",
    )
    sort_by: Literal[
        "fitness_is", "fitness_oos", "fitness_full", "fitness_strategy",
        "trades", "net_profit", "drawdown_abs",
        "return_pct", "drawdown_pct",
        "profit_to_dd_ratio", "avg_trade", "trades_per_year", "oos_is_ratio",
        "complexity", "history_years",
    ] = Field("profit_to_dd_ratio")
    descending: bool = Field(True)
    limit: int = Field(50, ge=1, le=5000)

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_databank(cls, v: str) -> str:
        return validate_databank_name(v)


class DatabankPromoteArgs(BaseModel):
    """Move top-N (optionally filtered) strategies from one databank to another."""
    source_project: str = Field(..., description="Source project.")
    source_databank: str = Field(
        "Results", description="Source databank folder."
    )
    dest_project: str = Field(..., description="Destination project (may equal source).")
    dest_databank: str = Field(
        ...,
        description=(
            "Destination databank folder. Use 'Strategies to retest' for a Retester "
            "input or 'Strategies to optimize' for an Optimizer input. "
            "Created if missing."
        ),
    )
    top_n: int = Field(10, ge=1, le=5000)
    sort_by: Literal[
        "fitness_is", "fitness_oos", "fitness_full", "fitness_strategy",
        "net_profit", "drawdown_abs", "profit_to_dd_ratio",
        "return_pct", "trades", "avg_trade", "oos_is_ratio",
    ] = Field("profit_to_dd_ratio")
    descending: bool = Field(True)
    # Optional filter pre-stage. Defaults are pass-through.
    min_trades: int | None = Field(None, ge=0)
    max_drawdown_pct: float | None = Field(None, ge=0, le=100)
    min_profit_to_dd_ratio: float | None = Field(None, ge=0)
    min_fitness_oos: float | None = None
    min_oos_is_ratio: float | None = Field(None, ge=0, le=2.0)
    dedupe_by_hash: bool = Field(
        True,
        description=(
            "If True, skip strategies whose Fingerprint trades_hash already exists in "
            "the destination — prevents re-promoting the exact same backtest."
        ),
    )
    overwrite: bool = Field(
        False,
        description=(
            "If True, overwrite a destination file with the same .sqx basename. "
            "If False, rename to <name>_v2.sqx, <name>_v3.sqx... on collision."
        ),
    )
    dry_run: bool = Field(
        False, description="Show what WOULD be copied without writing any files."
    )

    @field_validator("source_project", "dest_project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("source_databank", "dest_databank")
    @classmethod
    def _v_databank(cls, v: str) -> str:
        return validate_databank_name(v)


class DatabankMergeArgs(BaseModel):
    """Combine .sqx files from multiple databanks into a single destination."""
    sources: list[dict[str, str]] = Field(
        ...,
        min_length=1,
        max_length=20,
        description=(
            "List of {project, databank} pairs to merge. Order is preserved — "
            "first source wins on hash collisions when dedupe_by_hash=True."
        ),
    )
    dest_project: str = Field(..., description="Destination project.")
    dest_databank: str = Field(
        ..., description="Destination databank folder. Created if missing."
    )
    dedupe_by_hash: bool = Field(True)
    dry_run: bool = Field(False)

    @field_validator("dest_project")
    @classmethod
    def _v_dest_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("dest_databank")
    @classmethod
    def _v_dest_databank(cls, v: str) -> str:
        return validate_databank_name(v)

    @field_validator("sources")
    @classmethod
    def _v_sources(cls, v: list[dict[str, str]]) -> list[dict[str, str]]:
        clean: list[dict[str, str]] = []
        for entry in v:
            if "project" not in entry or "databank" not in entry:
                raise ValueError("each source must have 'project' and 'databank' keys")
            clean.append(
                {
                    "project": validate_project_name(entry["project"]),
                    "databank": validate_databank_name(entry["databank"]),
                }
            )
        return clean


def _existing_hashes(databank_dir: Path) -> set[str]:
    """Return the set of trades_hash values already present in a databank dir.

    Defensive: catches OS errors during enumeration so a permission glitch
    on a stray file doesn't kill the whole promote/merge call.
    """
    out: set[str] = set()
    if not databank_dir.exists():
        return out
    try:
        paths = list(databank_dir.rglob("*.sqx"))
    except OSError:
        return out
    for sqx_path in paths:
        try:
            info = parse_sqx(sqx_path)
            if info.fingerprint and info.fingerprint.trades_hash:
                out.add(info.fingerprint.trades_hash)
        except (ValueError, OSError):
            continue
    return out


# Iteration cap on the _vN suffix loop. If a databank somehow has 9999 versions
# of the same strategy something is wrong upstream — bail loudly rather than
# burning CPU forever on a broken filesystem.
_MAX_VERSION_SUFFIX = 9999


def _resolve_destination_filename(dest_dir: Path, basename: str, overwrite: bool) -> Path:
    """Pick a destination path that does not clobber an existing file.

    If `overwrite=True`, returns the basename even if a file already lives there.
    Otherwise, appends `_v2`, `_v3`, ... until a non-existent path is found, or
    raises `RuntimeError` if the search exceeds _MAX_VERSION_SUFFIX (signals
    something is broken upstream).
    """
    dest = dest_dir / basename
    if not dest.exists() or overwrite:
        return dest
    stem = dest.stem
    suffix = dest.suffix
    for n in range(2, _MAX_VERSION_SUFFIX + 1):
        candidate = dest_dir / f"{stem}_v{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(
        f"could not find a free destination for {basename!r} after "
        f"{_MAX_VERSION_SUFFIX} attempts under {dest_dir}"
    )


def _passes_filter(m: dict, args: DatabankFilterArgs) -> tuple[bool, str | None]:
    """Return (passes, fail_reason). fail_reason is None when passing."""
    def _check(condition: bool, reason: str) -> tuple[bool, str | None]:
        return (True, None) if condition else (False, reason)

    if args.min_trades is not None:
        n = m.get("trades")
        if n is None or n < args.min_trades:
            return False, f"trades<{args.min_trades}"
    if args.max_trades is not None:
        n = m.get("trades")
        if n is not None and n > args.max_trades:
            return False, f"trades>{args.max_trades}"
    if args.min_net_profit is not None:
        p = m.get("net_profit")
        if p is None or p < args.min_net_profit:
            return False, f"net_profit<{args.min_net_profit}"
    if args.max_drawdown_abs is not None:
        d = m.get("drawdown_abs")
        if d is None or d > args.max_drawdown_abs:
            return False, f"drawdown_abs>{args.max_drawdown_abs}"
    if args.max_drawdown_pct is not None:
        d = m.get("drawdown_pct")
        if d is None or d > args.max_drawdown_pct:
            return False, f"drawdown_pct>{args.max_drawdown_pct}"
    if args.min_profit_to_dd_ratio is not None:
        r = m.get("profit_to_dd_ratio")
        if r is None or r < args.min_profit_to_dd_ratio:
            return False, f"profit_to_dd<{args.min_profit_to_dd_ratio}"
    if args.min_fitness_is is not None:
        f = m.get("fitness_is")
        if f is None or f < args.min_fitness_is:
            return False, f"fitness_is<{args.min_fitness_is}"
    if args.min_fitness_oos is not None:
        f = m.get("fitness_oos")
        if f is None or f < args.min_fitness_oos:
            return False, f"fitness_oos<{args.min_fitness_oos}"
    if args.min_oos_is_ratio is not None:
        r = m.get("oos_is_ratio")
        if r is None or r < args.min_oos_is_ratio:
            return False, f"oos_is_ratio<{args.min_oos_is_ratio}"
    if args.min_avg_trade is not None:
        a = m.get("avg_trade")
        if a is None or a < args.min_avg_trade:
            return False, f"avg_trade<{args.min_avg_trade}"
    if args.min_trades_per_year is not None:
        a = m.get("trades_per_year")
        if a is None or a < args.min_trades_per_year:
            return False, f"trades_per_year<{args.min_trades_per_year}"
    if args.max_complexity is not None:
        c = m.get("complexity")
        if c is not None and c > args.max_complexity:
            return False, f"complexity>{args.max_complexity}"
    if args.exclude_ambiguous and m.get("ambiguous_trades"):
        return False, "ambiguous_trades>0"
    return True, None


def _pick_result(info, result_name: str | None):
    """Pick the SqxResult block to use for sorting."""
    if not info.results:
        return None
    if result_name:
        for r in info.results:
            if r.name == result_name:
                return r
        return None
    # Default: prefer first non-portfolio entry; fall back to first.
    for r in info.results:
        if not r.is_portfolio:
            return r
    return info.results[0]


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "List strategies inside a project's databank (default name: Results). "
            "When the engine reply is the status-only marker 'Databanks listed.' (Build 143 "
            "redirects the actual list to a file), falls back to a filesystem scan of the "
            "project's databanks/ directory."
        )
    )
    async def databank_list(args: DatabankRefArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            project_dir = eng.config.projects_dir / args.project
            cmd = f"-databank action=list project={args.project}"
            if args.name:
                cmd += f" name={args.name}"
            try:
                text = await eng.call(cmd)
            except EngineError as exc:
                fs_dbs = _scan_databanks_fs(project_dir)
                if args.name:
                    fs_dbs = [d for d in fs_dbs if d["name"] == args.name]
                return {
                    "ok": True,
                    "strategies": [],
                    "databanks_detail": fs_dbs,
                    "source": "filesystem",
                    "fallback_reason": f"engine error: {exc}",
                    "note": (
                        "Engine call failed; this lists databank folders, not strategy entries. "
                        "Use databank_top_n to read .sqx files inside a folder."
                    ),
                }
            r = parse_listing_response(text, "strategies")
            if r["ok"] and r["strategies"] and not is_status_only_response(text):
                return r | {"source": "engine"}
            fs_dbs = _scan_databanks_fs(project_dir)
            if args.name:
                fs_dbs = [d for d in fs_dbs if d["name"] == args.name]
            return {
                "ok": True,
                "strategies": [],
                "databanks_detail": fs_dbs,
                "source": "filesystem",
                "fallback_reason": (
                    "engine error" if not r["ok"]
                    else "engine output was status-only (Build 143 redirects to a file)"
                ),
                "engine_errors": r.get("errors"),
                "engine_raw": r.get("raw"),
                "note": (
                    "This lists databank folders, not strategy entries. Use databank_top_n "
                    "to read .sqx files inside a folder."
                ),
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Count strategies in a databank — useful as a cheap polling probe during long builds.")
    async def databank_count(args: DatabankCountArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cmd = f"-databank action=count project={args.project}"
            if args.name:
                cmd += f" name={args.name}"
            text = await eng.call(cmd)
            # SQ X Build 143 returns "Error: Not implemented." or
            # "Error: Missing parameter 'name'." for `count`, and also redirects
            # list output to a file. Fall back to a filesystem scan in all of
            # those cases.
            if "Error:" in text or is_status_only_response(text):
                project_dir = eng.config.projects_dir / args.project
                db_dirs = _scan_databanks_fs(project_dir)
                if args.name:
                    db_dirs = [d for d in db_dirs if d["name"] == args.name]
                count = sum(d["sqx_count"] for d in db_dirs)
                return {
                    "ok": True,
                    "count": count,
                    "source": "filesystem",
                    "databanks": db_dirs,
                    "fallback": "engine count/list unavailable — counted .sqx files on disk",
                }
            count = None
            for line in parse_list(text):
                for tok in line.split():
                    if tok.isdigit():
                        count = int(tok)
                        break
                if count is not None:
                    break
            return {"ok": True, "count": count, "raw": text.strip(), "source": "engine"}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Save databank contents to a folder under the project's databanks/.")
    async def databank_save(args: DatabankSaveLoadArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cmd = f"-databank action=save project={args.project} folder={args.folder}"
            if args.name:
                cmd += f" name={args.name}"
            if args.strategies:
                cmd += f' strategies="{",".join(args.strategies)}"'
            text = await eng.call(cmd, timeout=300.0)
            return parse_response(text) | {"folder": args.folder}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Load .sqx files from a folder into a databank.")
    async def databank_load(args: DatabankSaveLoadArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cmd = f"-databank action=load project={args.project} folder={args.folder}"
            if args.name:
                cmd += f" name={args.name}"
            if args.strategies:
                cmd += f' strategies="{",".join(args.strategies)}"'
            text = await eng.call(cmd, timeout=300.0)
            return parse_response(text) | {"folder": args.folder}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Force-sync a databank from JVM memory to disk. CRITICAL for monitoring: most "
            "SQ X project databanks default to `syncType=Auto-sync never` which means new "
            "strategies live in JVM memory and never hit disk on their own. Without this "
            "call, your file-listing / counting tools see zero strategies even while the "
            "engine has hundreds in memory. The exception is the Final databank which "
            "typically auto-syncs hourly. Use this every few minutes during a build run."
        )
    )
    async def databank_force_sync(args: DatabankRefArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cmd = f"-databank action=synctofiles project={args.project}"
            if args.name:
                cmd += f" name={args.name}"
            text = await eng.call(cmd, timeout=300.0)
            return parse_response(text) | {
                "project": args.project,
                "databank": args.name,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Clear all strategies from a databank.")
    async def databank_clear(args: DatabankRefArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cmd = f"-databank action=clear project={args.project}"
            if args.name:
                cmd += f" name={args.name}"
            text = await eng.call(cmd)
            return parse_response(text)
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Export a databank to CSV or XLSX (extension determines format).")
    async def databank_export(args: DatabankExportArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            out_path = resolve_safe_path(args.file)
            cmd = f"-databank action=export project={args.project} file={out_path}"
            if args.name:
                cmd += f" name={args.name}"
            if args.view:
                cmd += f" view={args.view}"
            text = await eng.call(cmd, timeout=300.0)
            return parse_response(text) | {"file": str(out_path)}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Parse a .sqx file directly (no engine call) — extracts version, fitness IS/OOS, presence of equity & orders blobs.")
    async def sqx_inspect(args: SqxInspectArgs, ctx: Context) -> dict:
        try:
            path = resolve_safe_path(args.path, must_exist=True)
            info = parse_sqx(path)
            return {"ok": True, **info.as_dict()}
        except (ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Extract the text-based contents (settings.xml, lastSettings.xml, strategy_Portfolio.xml, "
            "version.txt, MANIFEST.MF, etc.) from a .sqx archive without running the engine. Useful "
            "for inspecting strategy rules, indicators, MM and fitness settings. Binary blobs "
            "(orders.bin, dailyEquity.bin) are listed by name and size only."
        )
    )
    async def sqx_extract_source(args: SqxExtractArgs, ctx: Context) -> dict:
        try:
            path = resolve_safe_path(args.path, must_exist=True)
            result = _extract_sqx_contents(
                path,
                include_xml=args.include_xml,
                include_text=args.include_text,
                include_bin_listing=args.include_bin_listing,
                max_text_size=args.max_text_size,
            )
            return result | {"path": str(path)}
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Export the trade-by-trade history of a strategy (.sqx) to CSV or XLSX via the "
            "engine's `-tools action=orderstocsv|orderstoxlsx`. Produces a sibling file at "
            "<sqx_dir>/<sqx_basename>.<ext> with columns: Ticket, Symbol, Type (Buy/Sell), "
            "Open/Close time, prices, Size, P/L, Balance, Sample type (IST/IS/OOS), Close "
            "type (SL/PT/Signal), MAE, MFE, Time in trade, Comment. Use this to do offline "
            "analysis of a strategy's actual trades — distributions, drawdown timing, "
            "win/loss streaks, etc. KNOWN ISSUE: sqcli's parser barfs on spaces in the file "
            "path (returns 'Cannot invoke String.startsWith because parameter1 is null'). "
            "Caller must copy/rename the .sqx to a space-free path first."
        )
    )
    async def strategy_orders_export(args: StrategyOrdersExportArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            src = resolve_safe_path(args.sqx_path, must_exist=True)
            if " " in str(src):
                return {
                    "ok": False,
                    "error": f"sqcli parser does not accept paths with spaces: {src}",
                    "hint": "copy/rename the .sqx to an underscore-only path first",
                }
            action = "orderstocsv" if args.output_format == "csv" else "orderstoxlsx"
            cmd = f"-tools action={action} file={src} data={args.data_scope}"
            if args.use_comma:
                cmd += " usecomma=true"
            text = await eng.call(cmd, timeout=120.0)
            expected_out = src.with_suffix(f".{args.output_format}")
            return {
                "ok": expected_out.exists(),
                "sqx_path": str(src),
                "expected_output": str(expected_out),
                "exists": expected_out.exists(),
                "size_bytes": expected_out.stat().st_size if expected_out.exists() else 0,
                "raw": text.strip()[:500],
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Diff two .sqx archives. Pretty-prints XML members with sorted attributes so "
            "reordering doesn't produce noise, then emits a unified diff per member. Defaults "
            "to comparing settings.xml, lastSettings.xml, strategy_Portfolio.xml, version.txt — "
            "the files that actually describe the strategy. Use this to see what changed "
            "between two builds or iterations."
        )
    )
    async def sqx_diff(args: SqxDiffArgs, ctx: Context) -> dict:
        try:
            a = resolve_safe_path(args.path_a, must_exist=True)
            b = resolve_safe_path(args.path_b, must_exist=True)
            members = tuple(args.members) if args.members else _SQX_DIFF_DEFAULT_FILES
            result = _diff_sqx_files(a, b, members=members, context_lines=args.context_lines)
            return {
                "ok": True,
                "path_a": str(a),
                "path_b": str(b),
                "members": list(members),
                **result,
            }
        except (ValidationError, OSError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Rank strategies inside a project's databank folder by IS / OOS / full fitness. "
            "Reads .sqx files directly from disk (no engine call) and returns the top N. "
            "Useful for quickly inspecting a finished build without round-tripping through sqcli."
        )
    )
    async def databank_top_n(args: DatabankTopNArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = eng.config.projects_dir / args.project / "databanks" / args.databank
            if not db_dir.exists():
                return {
                    "ok": False,
                    "error": f"databank directory not found: {db_dir}",
                    "project": args.project,
                    "databank": args.databank,
                }
            ranked: list[dict] = []
            unparseable: list[dict] = []
            for sqx_path in sorted(db_dir.rglob("*.sqx")):
                try:
                    info = parse_sqx(sqx_path)
                    r = _pick_result(info, args.result_name)
                    if r is None:
                        unparseable.append({"file": str(sqx_path.relative_to(db_dir)), "reason": "no result block matched"})
                        continue
                    key_val = {
                        "fitness_is": r.stats.fitness_is,
                        "fitness_oos": r.stats.fitness_oos,
                        "fitness_full": r.stats.fitness_full,
                    }[args.sort_by]
                    ranked.append(
                        {
                            "file": str(sqx_path.relative_to(db_dir)),
                            "result": r.name,
                            "is_portfolio": r.is_portfolio,
                            "fitness_is": r.stats.fitness_is,
                            "fitness_oos": r.stats.fitness_oos,
                            "fitness_full": r.stats.fitness_full,
                            "sort_key": key_val,
                        }
                    )
                except (ValueError, OSError) as exc:
                    unparseable.append({"file": str(sqx_path.relative_to(db_dir)), "reason": str(exc)})

            sortable = [x for x in ranked if x["sort_key"] is not None]
            missing_key = [x for x in ranked if x["sort_key"] is None]
            sortable.sort(key=lambda x: x["sort_key"], reverse=args.descending)
            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "sort_by": args.sort_by,
                "descending": args.descending,
                "total_scanned": len(ranked) + len(unparseable),
                "ranked_count": len(sortable),
                "missing_key_count": len(missing_key),
                "unparseable_count": len(unparseable),
                "top": sortable[: args.top_n],
                "unparseable": unparseable[:10],  # cap for response size
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Filter strategies in a databank by performance/robustness criteria. "
            "Reads .sqx files directly (no engine call) and returns the subset that "
            "satisfies every supplied threshold, sorted by the chosen metric. Returns "
            "BOTH the survivors and a per-criterion rejection breakdown so callers can "
            "see which constraint is the bottleneck. Filters are the building block for "
            "promoting strategies into a Retester/Optimizer input databank. Metrics: "
            "trades, net_profit, drawdown_abs/pct, profit_to_dd_ratio, fitness_is/oos, "
            "oos_is_ratio (overfit detector), avg_trade, trades_per_year, complexity."
        )
    )
    async def databank_filter(args: DatabankFilterArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            db_dir = eng.config.projects_dir / args.project / "databanks" / args.databank
            if not db_dir.exists():
                return {
                    "ok": False,
                    "error": f"databank directory not found: {db_dir}",
                    "project": args.project,
                    "databank": args.databank,
                }
            survivors: list[dict] = []
            rejected_reasons: dict[str, int] = {}
            unparseable: list[dict] = []
            total = 0
            for sqx_path in sorted(db_dir.rglob("*.sqx")):
                total += 1
                try:
                    info = parse_sqx(sqx_path)
                    m = derive_metrics(info)
                except (ValueError, OSError) as exc:
                    unparseable.append(
                        {"file": str(sqx_path.relative_to(db_dir)), "reason": str(exc)}
                    )
                    continue
                ok, reason = _passes_filter(m, args)
                if ok:
                    survivors.append(
                        {
                            "file": str(sqx_path.relative_to(db_dir)),
                            "abs_path": str(sqx_path),
                            "metrics": m,
                        }
                    )
                else:
                    rejected_reasons[reason or "unknown"] = (
                        rejected_reasons.get(reason or "unknown", 0) + 1
                    )

            def _key(entry: dict) -> float:
                v = entry["metrics"].get(args.sort_by)
                return v if isinstance(v, (int, float)) else (
                    float("-inf") if args.descending else float("inf")
                )

            survivors.sort(key=_key, reverse=args.descending)
            return {
                "ok": True,
                "project": args.project,
                "databank": args.databank,
                "databank_dir": str(db_dir),
                "scanned": total,
                "survivors_count": len(survivors),
                "rejected_count": sum(rejected_reasons.values()),
                "rejected_by_reason": rejected_reasons,
                "unparseable_count": len(unparseable),
                "unparseable_sample": unparseable[:10],
                "sort_by": args.sort_by,
                "descending": args.descending,
                "survivors": survivors[: args.limit],
                "survivors_truncated": len(survivors) > args.limit,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Promote top-N strategies from one databank to another by copying .sqx files. "
            "Optional pre-filter on trades / drawdown_pct / profit_to_dd / fitness_oos / "
            "oos_is_ratio. Default sort is profit_to_dd_ratio descending (a robust "
            "ranking that penalizes oversized DD). Destination is created if missing. "
            "By default dedupes by Fingerprint trades_hash so re-runs don't multiply the "
            "same backtest. Set dry_run=True to preview without writing. This is the "
            "primary tool for staging Retester / Optimizer inputs from a Builder "
            "output databank."
        )
    )
    async def databank_promote(args: DatabankPromoteArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            src_dir = (
                eng.config.projects_dir / args.source_project / "databanks" / args.source_databank
            )
            dst_dir = (
                eng.config.projects_dir / args.dest_project / "databanks" / args.dest_databank
            )
            if not src_dir.exists():
                return {"ok": False, "error": f"source databank not found: {src_dir}"}
            # Materialize destination project dir if needed
            if not (eng.config.projects_dir / args.dest_project).exists():
                return {
                    "ok": False,
                    "error": (
                        f"destination project does not exist: {args.dest_project}. "
                        "Create it (e.g. via project_create_from_template) first."
                    ),
                }
            if not args.dry_run:
                dst_dir.mkdir(parents=True, exist_ok=True)

            existing_hashes = _existing_hashes(dst_dir) if args.dedupe_by_hash else set()

            # 1) Load + filter + rank source strategies
            candidates: list[dict] = []
            unparseable: list[dict] = []
            for sqx_path in sorted(src_dir.rglob("*.sqx")):
                try:
                    info = parse_sqx(sqx_path)
                    m = derive_metrics(info)
                except (ValueError, OSError) as exc:
                    unparseable.append({"file": sqx_path.name, "reason": str(exc)})
                    continue
                # apply lightweight filter inline
                if args.min_trades is not None and (m.get("trades") or 0) < args.min_trades:
                    continue
                if args.max_drawdown_pct is not None:
                    dd = m.get("drawdown_pct")
                    if dd is None or dd > args.max_drawdown_pct:
                        continue
                if args.min_profit_to_dd_ratio is not None:
                    r = m.get("profit_to_dd_ratio")
                    if r is None or r < args.min_profit_to_dd_ratio:
                        continue
                if args.min_fitness_oos is not None:
                    f = m.get("fitness_oos")
                    if f is None or f < args.min_fitness_oos:
                        continue
                if args.min_oos_is_ratio is not None:
                    r = m.get("oos_is_ratio")
                    if r is None or r < args.min_oos_is_ratio:
                        continue
                candidates.append(
                    {
                        "src_path": sqx_path,
                        "metrics": m,
                        "hash": (info.fingerprint.trades_hash if info.fingerprint else None),
                    }
                )

            def _sort_key(entry: dict) -> float:
                v = entry["metrics"].get(args.sort_by)
                return v if isinstance(v, (int, float)) else (
                    float("-inf") if args.descending else float("inf")
                )

            candidates.sort(key=_sort_key, reverse=args.descending)

            # 2) Pick top N respecting dedupe
            picks: list[dict] = []
            skipped_dupes: list[str] = []
            for entry in candidates:
                h = entry["hash"]
                if args.dedupe_by_hash and h and h in existing_hashes:
                    skipped_dupes.append(entry["src_path"].name)
                    continue
                picks.append(entry)
                if h:
                    existing_hashes.add(h)
                if len(picks) >= args.top_n:
                    break

            # 3) Copy
            copied: list[dict] = []
            for entry in picks:
                src_p: Path = entry["src_path"]
                dst_p = _resolve_destination_filename(dst_dir, src_p.name, args.overwrite)
                copied.append(
                    {
                        "src": str(src_p),
                        "dest": str(dst_p),
                        "metrics": entry["metrics"],
                        "hash": entry["hash"],
                    }
                )
                if not args.dry_run:
                    shutil.copy2(src_p, dst_p)

            return {
                "ok": True,
                "source_project": args.source_project,
                "source_databank": args.source_databank,
                "dest_project": args.dest_project,
                "dest_databank": args.dest_databank,
                "dest_dir": str(dst_dir),
                "scanned": len(candidates) + len(unparseable),
                "filtered_candidates": len(candidates),
                "promoted_count": len(copied),
                "skipped_duplicates": skipped_dupes,
                "unparseable_count": len(unparseable),
                "dry_run": args.dry_run,
                "promoted": copied,
                "hint": (
                    "After promoting, call project_load_and_start with sync_databanks=['"
                    + args.dest_databank
                    + "'] so the engine picks up the new files before kicking off the run."
                ),
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Merge .sqx files from multiple source databanks into a single destination "
            "databank. Dedupes by Fingerprint trades_hash (first source wins on conflict). "
            "Useful for combining results from parallel Builder runs or pooling survivors "
            "from several robustness tests. Destination created if missing."
        )
    )
    async def databank_merge(args: DatabankMergeArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            dst_dir = (
                eng.config.projects_dir / args.dest_project / "databanks" / args.dest_databank
            )
            if not (eng.config.projects_dir / args.dest_project).exists():
                return {
                    "ok": False,
                    "error": (
                        f"destination project does not exist: {args.dest_project}. "
                        "Create it first."
                    ),
                }
            if not args.dry_run:
                dst_dir.mkdir(parents=True, exist_ok=True)

            seen_hashes: set[str] = _existing_hashes(dst_dir) if args.dedupe_by_hash else set()
            copied: list[dict] = []
            skipped: list[dict] = []
            missing_sources: list[dict] = []
            total_scanned = 0

            for source in args.sources:
                src_dir = (
                    eng.config.projects_dir
                    / source["project"]
                    / "databanks"
                    / source["databank"]
                )
                if not src_dir.exists():
                    missing_sources.append({**source, "path": str(src_dir)})
                    continue
                for sqx_path in sorted(src_dir.rglob("*.sqx")):
                    total_scanned += 1
                    h: str | None = None
                    if args.dedupe_by_hash:
                        try:
                            info = parse_sqx(sqx_path)
                            h = info.fingerprint.trades_hash if info.fingerprint else None
                        except (ValueError, OSError):
                            h = None
                        if h and h in seen_hashes:
                            skipped.append(
                                {"src": str(sqx_path), "reason": "duplicate_hash", "hash": h}
                            )
                            continue
                    dst_path = _resolve_destination_filename(dst_dir, sqx_path.name, overwrite=False)
                    if not args.dry_run:
                        shutil.copy2(sqx_path, dst_path)
                    copied.append(
                        {
                            "src": str(sqx_path),
                            "dest": str(dst_path),
                            "hash": h,
                            "from_project": source["project"],
                            "from_databank": source["databank"],
                        }
                    )
                    if h:
                        seen_hashes.add(h)

            return {
                "ok": True,
                "dest_project": args.dest_project,
                "dest_databank": args.dest_databank,
                "dest_dir": str(dst_dir),
                "scanned": total_scanned,
                "copied_count": len(copied),
                "skipped_count": len(skipped),
                "missing_sources": missing_sources,
                "dry_run": args.dry_run,
                "copied": copied[:500],  # cap response size
                "skipped": skipped[:100],
                "copied_truncated": len(copied) > 500,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)
