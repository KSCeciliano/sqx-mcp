"""Project lifecycle tools — list, start, stop, status, configure, clone."""

from __future__ import annotations

import asyncio
import io
import os
import re
import shutil
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from lxml import etree
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
from sq_mcp._xml import safe_fromstring
from sq_mcp.engine import EngineClient, EngineError
from sq_mcp.parsers import parse_cfx
from sq_mcp.tools._common import (
    get_engine,
    parse_list,
    parse_listing_response,
    parse_response,
    safe_error_payload,
)

# ---- filesystem helpers (used by fallbacks and clone/registry) -------------


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write data to path atomically: tmp file + os.replace.

    Guarantees the destination is never left in a half-written state if the
    process is killed mid-write. Critical for CFX archives that SQ X reads
    on the next project load.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _scan_projects_fs(projects_dir: Path) -> list[dict[str, Any]]:
    """Enumerate project directories on disk that contain a project.cfx."""
    if not projects_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(projects_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        cfx = entry / "project.cfx"
        if not cfx.is_file():
            continue
        try:
            stat = cfx.stat()
            mtime = stat.st_mtime
            size = stat.st_size
        except OSError:
            mtime = 0.0
            size = 0
        out.append(
            {
                "name": entry.name,
                "cfx_size": size,
                "cfx_mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                if mtime
                else None,
            }
        )
    return out


def _project_status_fs(eng: EngineClient, name: str) -> dict[str, Any]:
    """Best-effort status report when sqcli's `-project action=status` is broken."""
    pdir = eng.config.projects_dir / name
    cfx = pdir / "project.cfx"
    if not pdir.exists():
        return {"ok": False, "project": name, "exists": False, "source": "filesystem"}
    log_mentions: list[str] = []
    try:
        tail = eng.recent_log
    except Exception:  # noqa: BLE001
        tail = []
    needle = name.lower()
    for line in tail[-200:]:
        if needle in line.lower():
            log_mentions.append(line)
    return {
        "ok": True,
        "project": name,
        "exists": True,
        "cfx_present": cfx.is_file(),
        "databank_dir_present": (pdir / "databanks").is_dir(),
        "recent_log_mentions": log_mentions[-20:],
        "source": "filesystem",
        "note": (
            "Engine-side status is unavailable in this SQ build; this report is derived from "
            "filesystem inspection and the recent sqcli log tail."
        ),
    }


# ---- clone helpers ---------------------------------------------------------


_TASK_XML_PREFIXES = ("Build-", "Retest-", "Optimize-", "WalkForward-", "MonteCarlo-")
_VALID_TIMEFRAME_VALUES = (
    "TICK",
    *(f"M{n}" for n in (1, 2, 3, 5, 10, 15, 20, 30, 60, 90, 120, 180, 240, 360, 480, 720)),
    *(f"H{n}" for n in (1, 2, 3, 4, 6, 8, 12)),
    *(f"D{n}" for n in (1, 2, 3, 5)),
    *(f"W{n}" for n in (1, 2)),
    "MN",
    "MN1",
    "Intraday",
)


def _date_to_epoch_ms(date_str: str) -> int:
    y, m, d = date_str.split(".")
    dt = datetime(int(y), int(m), int(d), tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _date_parse_any(s: str) -> int | None:
    """Parse either yyyy.MM.dd or epoch milliseconds → epoch ms. Returns None on failure."""
    if not s:
        return None
    if "." in s:
        try:
            return _date_to_epoch_ms(s)
        except (ValueError, AttributeError):
            return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _quote_if_space(value: str) -> str:
    """Wrap a value in double quotes if it contains whitespace, for sqcli `key=value` args.

    sqcli's HTTP parser DOES survive double-quoted values in key=value form for some
    actions (e.g. databank `name="Strategies to optimize"`), even though it doesn't
    survive quotes in `name=` for the `-project` action. Use only for databank names.
    """
    if value and any(c.isspace() for c in value):
        return f'"{value}"'
    return value


def _patch_config_xml(xml_bytes: bytes, new_name: str) -> bytes:
    root = safe_fromstring(xml_bytes)
    root.set("name", new_name)
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def _looks_like_tf(value: str) -> bool:
    return value in _VALID_TIMEFRAME_VALUES


def _patch_task_xml(
    xml_bytes: bytes,
    *,
    symbol: str | None,
    timeframe: str | None,
    date_from: str | None,
    date_to: str | None,
) -> tuple[bytes, Counter]:
    """Rewrite symbol / timeframe / dateFrom / dateTo attributes throughout a task XML."""
    root = safe_fromstring(xml_bytes)
    changes: Counter = Counter()

    df_epoch = _date_to_epoch_ms(date_from) if date_from else None
    dt_epoch = _date_to_epoch_ms(date_to) if date_to else None

    for el in root.iter():
        if symbol is not None and el.get("symbol") is not None:
            el.set("symbol", symbol)
            changes["symbol"] += 1
        if timeframe is not None:
            tf_val = el.get("timeframe")
            if tf_val is not None and _looks_like_tf(tf_val):
                el.set("timeframe", timeframe)
                changes["timeframe"] += 1
        if date_from is not None:
            df = el.get("dateFrom")
            if df is not None and df != "0":
                el.set("dateFrom", date_from if "." in df else str(df_epoch))
                changes["dateFrom"] += 1
        if date_to is not None:
            dtv = el.get("dateTo")
            if dtv is not None and dtv != "0":
                el.set("dateTo", date_to if "." in dtv else str(dt_epoch))
                changes["dateTo"] += 1

    return (
        etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True),
        changes,
    )


def _is_task_xml(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return any(base.startswith(p) for p in _TASK_XML_PREFIXES) and base.endswith(".xml")


def _clone_cfx_zip(
    src_cfx: Path,
    dst_cfx: Path,
    *,
    new_name: str,
    symbol: str | None,
    timeframe: str | None,
    date_from: str | None,
    date_to: str | None,
) -> dict[str, Any]:
    total_changes: Counter = Counter()
    per_file: dict[str, dict[str, int]] = {}
    buf = io.BytesIO()
    with zipfile.ZipFile(src_cfx, "r") as src, zipfile.ZipFile(
        buf, "w", compression=zipfile.ZIP_DEFLATED
    ) as dst:
        for item in src.infolist():
            raw = src.read(item.filename)
            if item.filename == "config.xml":
                raw = _patch_config_xml(raw, new_name)
            elif _is_task_xml(item.filename) and (
                symbol or timeframe or date_from or date_to
            ):
                raw, c = _patch_task_xml(
                    raw,
                    symbol=symbol,
                    timeframe=timeframe,
                    date_from=date_from,
                    date_to=date_to,
                )
                if c:
                    per_file[item.filename] = dict(c)
                    total_changes.update(c)
            dst.writestr(item, raw)
    _atomic_write_bytes(dst_cfx, buf.getvalue())
    return {"total_changes": dict(total_changes), "per_file": per_file}


# ---- broker registry extraction --------------------------------------------


def _extract_broker_entries(cfx_path: Path, project_name: str) -> list[dict[str, Any]]:
    """Mine broker/source/dataType/barType combos from a project's task XMLs.

    SQ X stores this metadata across two element types:
      * <Symbol name="GBPJPY_M1_dukas" source="2" barType="1" precision="M1" ...>
        — the data-side row (source = data provider code, barType = M1/Tick/etc).
      * <InstrumentInfo instrument="GBPJPY_dukascopy" broker="3" dataType="3" ...>
        — the broker-side row (broker = broker registry code, dataType = asset class).

    We emit one entry per matching element so the user can see the codes attached
    to each symbol/instrument when crafting a new task XML.
    """
    rows: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(cfx_path) as z:
            for name in z.namelist():
                if not _is_task_xml(name):
                    continue
                try:
                    root = safe_fromstring(z.read(name))
                except etree.XMLSyntaxError:
                    continue
                for el in root.iter():
                    tag = etree.QName(el.tag).localname
                    if tag == "Symbol":
                        key = el.get("name")
                        if not key:
                            continue
                        rows.append(
                            {
                                "kind": "data_symbol",
                                "key": key,
                                "broker": el.get("broker"),
                                "source": el.get("source"),
                                "dataType": el.get("dataType"),
                                "barType": el.get("barType"),
                                "precision": el.get("precision"),
                                "timezone": el.get("timezone"),
                                "uSymbol": el.get("uSymbol"),
                                "project": project_name,
                                "task_xml": name,
                            }
                        )
                    elif tag == "InstrumentInfo":
                        key = el.get("instrument")
                        if not key:
                            continue
                        rows.append(
                            {
                                "kind": "instrument",
                                "key": key,
                                "broker": el.get("broker"),
                                "source": el.get("source"),
                                "dataType": el.get("dataType"),
                                "barType": el.get("barType"),
                                "exchange": el.get("exchange"),
                                "country": el.get("country"),
                                "sector": el.get("sector"),
                                "project": project_name,
                                "task_xml": name,
                            }
                        )
    except (zipfile.BadZipFile, OSError):
        return []
    return rows


# ---- snapshot / validate / template helpers --------------------------------


def _validate_cfx_structure(cfx_path: Path) -> dict[str, Any]:
    """Walk a .cfx and report structural issues without trusting it.

    Checks: zip integrity, config.xml presence and well-formedness, every
    referenced taskXMLFile exists, no orphan task XMLs in the archive, every
    task XML parses.
    """
    issues: list[str] = []
    if not cfx_path.is_file():
        return {"ok": False, "error": f"file not found: {cfx_path}"}
    try:
        z = zipfile.ZipFile(cfx_path)
    except zipfile.BadZipFile as exc:
        return {"ok": False, "error": f"not a valid zip: {exc}"}
    except OSError as exc:
        return {"ok": False, "error": f"cannot read file: {exc}"}
    with z:
        names = set(z.namelist())
        if "config.xml" not in names:
            return {
                "ok": False,
                "error": "missing config.xml",
                "files_in_archive": sorted(names),
            }
        try:
            cfg = safe_fromstring(z.read("config.xml"))
        except etree.XMLSyntaxError as exc:
            return {"ok": False, "error": f"config.xml is not valid XML: {exc}"}
        project_name = cfg.get("name")
        version = cfg.get("version")
        tasks: list[dict[str, Any]] = []
        missing: list[str] = []
        for task_el in cfg.iterfind(".//Task"):
            xml_file = task_el.get("taskXMLFile") or ""
            entry = {
                "task_name": task_el.get("name") or "",
                "task_type": task_el.get("type") or "",
                "active": (task_el.get("active") or "true").lower() == "true",
                "xml_file": xml_file,
                "exists": xml_file in names,
            }
            tasks.append(entry)
            if xml_file and xml_file not in names:
                missing.append(xml_file)
                issues.append(
                    f"task {entry['task_name']!r} references missing file {xml_file!r}"
                )
        referenced_set = {t["xml_file"] for t in tasks if t["xml_file"]}
        orphan_xmls = sorted(
            n for n in names if _is_task_xml(n) and n not in referenced_set
        )
        for x in orphan_xmls:
            issues.append(f"orphan task XML in archive: {x!r}")
        xml_parse_errors: list[dict[str, str]] = []
        for entry in tasks:
            if not entry["exists"]:
                continue
            try:
                safe_fromstring(z.read(entry["xml_file"]))
            except etree.XMLSyntaxError as exc:
                err = {"file": entry["xml_file"], "error": str(exc)}
                xml_parse_errors.append(err)
                issues.append(
                    f"task XML {entry['xml_file']!r} is malformed: {exc}"
                )
    return {
        "ok": not issues,
        "project_name": project_name,
        "version": version,
        "tasks": tasks,
        "missing_files": missing,
        "orphan_task_xmls": orphan_xmls,
        "task_xml_parse_errors": xml_parse_errors,
        "files_in_archive": len(names),
        "issues": issues,
    }


def _make_snapshot(cfx_path: Path, *, label: str | None = None) -> Path:
    """Copy a .cfx to a sibling backup with UTC timestamp and optional label."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f".bak.{ts}"
    if label:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", label)[:32]
        if safe:
            suffix += f".{safe}"
    dst = cfx_path.with_name(cfx_path.name + suffix)
    shutil.copy2(cfx_path, dst)
    return dst


def _find_template_source(
    projects_dir: Path,
    *,
    instrument: str | None,
    data_symbol: str | None,
    prefer_source: str | None,
) -> tuple[Path | None, list[dict[str, Any]]]:
    """Find the best source project to clone from for a given instrument/data symbol."""
    candidates: list[dict[str, Any]] = []
    if not projects_dir.exists():
        return None, []
    for proj_dir in sorted(projects_dir.iterdir(), key=lambda p: p.name.lower()):
        cfx = proj_dir / "project.cfx"
        if not cfx.is_file():
            continue
        rows = _extract_broker_entries(cfx, proj_dir.name)
        matches: list[dict[str, Any]] = []
        for r in rows:
            if (
                r["kind"] == "instrument"
                and instrument is not None
                and (r["key"] or "").lower() == instrument.lower()
            ):
                matches.append(r)
            elif (
                r["kind"] == "data_symbol"
                and data_symbol is not None
                and (r["key"] or "").lower() == data_symbol.lower()
            ):
                matches.append(r)
        if matches:
            try:
                mtime = cfx.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append(
                {
                    "project": proj_dir.name,
                    "cfx": cfx,
                    "matches": matches,
                    "mtime": mtime,
                }
            )
    if not candidates:
        return None, []
    if prefer_source:
        for c in candidates:
            if c["project"] == prefer_source:
                return c["cfx"], candidates
    best = max(candidates, key=lambda c: c["mtime"])
    return best["cfx"], candidates


# ---- engine log progress parsing -------------------------------------------


# (regex, kind, group-names) — applied against each log line to extract markers.
_PROGRESS_PATTERNS: tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...] = (
    (re.compile(r"Strategies?\s+built[:\s]+(\d+)", re.IGNORECASE), "built", ("count",)),
    (re.compile(r"Built\s+(\d+)\s+strateg", re.IGNORECASE), "built", ("count",)),
    (re.compile(r"Tested\s+(\d+)\s+strateg", re.IGNORECASE), "tested", ("count",)),
    (re.compile(r"Speed[:\s]+([\d.]+)\s*strateg", re.IGNORECASE), "speed", ("rate",)),
    (
        re.compile(r"Best\s+fitness[:\s]+([\-+]?\d*\.?\d+)", re.IGNORECASE),
        "best_fitness",
        ("value",),
    ),
    (
        re.compile(r"Generation\s+(\d+)(?:\s*/\s*(\d+))?", re.IGNORECASE),
        "generation",
        ("current", "total"),
    ),
    (
        re.compile(r"Backtest\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE),
        "backtest",
        ("current", "total"),
    ),
    (re.compile(r"Progress[:\s]+([\d.]+)\s*%", re.IGNORECASE), "progress_pct", ("pct",)),
    (
        re.compile(r"Task\s+(\S+)\s+(started|finished|stopped|paused|resumed)", re.IGNORECASE),
        "task_event",
        ("task", "event"),
    ),
    (
        re.compile(r"Project\s+(\S+)\s+(started|finished|stopped)", re.IGNORECASE),
        "project_event",
        ("project", "event"),
    ),
    (re.compile(r"All tasks completed", re.IGNORECASE), "tasks_completed", ()),
)


def _resolve_instrument_metadata(
    data_dir: Path, *, symbol: str, timeframe: str
) -> dict[str, str] | None:
    """Look up authoritative metadata for a symbol/timeframe from data.db SQLite.

    Returns a dict with the merged DATA + INSTRUMENTS rows, ready to be projected
    onto <Symbol> and <InstrumentInfo> XML elements. Returns None if not found.
    """
    import sqlite3

    db = data_dir / "data.db"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT * FROM DATA WHERE (SYMBOL = ? OR USYMBOL = ?) AND TIMEFRAME = ?",
            (symbol, symbol, timeframe),
        )
        data_row = cur.fetchone()
        if data_row is None:
            # Fall back: any timeframe for the symbol
            cur = con.execute(
                "SELECT * FROM DATA WHERE SYMBOL = ? OR USYMBOL = ? ORDER BY TIMEFRAME LIMIT 1",
                (symbol, symbol),
            )
            data_row = cur.fetchone()
            if data_row is None:
                return None
        data_dict = {k: data_row[k] for k in data_row.keys()}
        # Pull instrument record
        instr_name = data_dict.get("INSTRUMENT") or ""
        cur = con.execute(
            "SELECT * FROM INSTRUMENTS WHERE INSTRUMENT = ?", (instr_name,)
        )
        instr_row = cur.fetchone()
        instr_dict = {k: instr_row[k] for k in instr_row.keys()} if instr_row else {}
        return {"data": data_dict, "instrument": instr_dict}
    finally:
        con.close()


def _project_metadata_onto_symbol(el: etree._Element, meta: dict[str, Any], *, dat_symbol: str, timeframe: str) -> None:
    """Rewrite a <Symbol> element's attributes from registry metadata."""
    data = meta.get("data", {})
    instr = meta.get("instrument", {})

    def _set(attr: str, value: Any) -> None:
        if value is None:
            return
        el.set(attr, str(value))

    _set("name", dat_symbol)
    _set("source", data.get("SOURCE"))
    _set("barType", "1")
    _set("precision", timeframe)
    _set("timezone", data.get("TIMEZONE"))
    if data.get("DATEFROM") is not None:
        _set("dateFrom", data["DATEFROM"])
    if data.get("DATETO") is not None:
        _set("dateTo", data["DATETO"])
    _set("uSymbol", data.get("USYMBOL"))
    _set("uSymbolName", data.get("USYMBOLNAME"))
    rw = data.get("REMOVE_WEEKENDS")
    _set("removeWeekends", "true" if rw else "false")
    broker_id = instr.get("BROKER_ID")
    if broker_id is not None:
        _set("broker", broker_id)


def _project_metadata_onto_instrumentinfo(el: etree._Element, meta: dict[str, Any]) -> None:
    """Rewrite an <InstrumentInfo> element from registry metadata."""
    data = meta.get("data", {})
    instr = meta.get("instrument", {})
    if not instr:
        return

    def _set(attr: str, value: Any) -> None:
        if value is None:
            return
        el.set(attr, str(value))

    _set("instrument", instr.get("INSTRUMENT"))
    _set("description", instr.get("DESCRIPTION") or "")
    _set("tickSize", instr.get("TICKSIZE"))
    _set("tickStep", instr.get("TICKSTEP"))
    _set("minDistance", instr.get("MIN_DISTANCE"))
    _set("tickValueInMoney", "0.0")
    _set("dateFrom", "0")
    _set("dateTo", "0")
    _set("rows", "0")
    _set("totalDays", "0")
    _set("defaultSpread", instr.get("DEFAULTSPREAD"))
    _set("defaultSlippage", instr.get("DEFAULTSLIPPAGE"))
    _set("decimals", data.get("DECIMALS") or 5)
    if instr.get("COMMISSIONS"):
        _set("commissions", instr["COMMISSIONS"])
    _set("pointValue", instr.get("POINTVALUE"))
    _set("dataType", instr.get("DATATYPE"))
    _set("recognizedFromOrders", "false")
    _set("exchange", instr.get("EXCHANGE") or "")
    _set("country", instr.get("COUNTRY") or "")
    _set("sector", instr.get("SECTOR") or "")
    if instr.get("SWAP"):
        _set("swap", instr["SWAP"])
    _set("orderSizeMultiplier", instr.get("ORDERSIZEMULTIPLIER"))
    _set("orderSizeStep", instr.get("ORDERSIZESTEP"))
    _set("broker", instr.get("BROKER_ID"))


def _referenced_symbols_from_cfx(cfx_path: Path) -> set[str]:
    """Mine all Symbol/InstrumentInfo references from a .cfx's task XMLs."""
    symbols: set[str] = set()
    try:
        with zipfile.ZipFile(cfx_path) as z:
            for name in z.namelist():
                if not _is_task_xml(name):
                    continue
                try:
                    root = safe_fromstring(z.read(name))
                except etree.XMLSyntaxError:
                    continue
                for el in root.iter():
                    tag = etree.QName(el.tag).localname
                    if tag == "Symbol" and el.get("name"):
                        symbols.add(el.get("name"))
                    elif tag == "InstrumentInfo" and el.get("instrument"):
                        symbols.add(el.get("instrument"))
                    if el.get("symbol"):
                        symbols.add(el.get("symbol"))
    except (zipfile.BadZipFile, OSError):
        pass
    return symbols


def _referenced_dates_from_cfx(cfx_path: Path) -> dict[str, list[str]]:
    """Mine date_from / date_to references from task XMLs."""
    out: dict[str, list[str]] = {"date_from": [], "date_to": []}
    try:
        with zipfile.ZipFile(cfx_path) as z:
            for name in z.namelist():
                if not _is_task_xml(name):
                    continue
                try:
                    root = safe_fromstring(z.read(name))
                except etree.XMLSyntaxError:
                    continue
                for el in root.iter():
                    df = el.get("dateFrom")
                    dt = el.get("dateTo")
                    if df and df != "0" and df not in out["date_from"]:
                        out["date_from"].append(df)
                    if dt and dt != "0" and dt not in out["date_to"]:
                        out["date_to"].append(dt)
    except (zipfile.BadZipFile, OSError):
        pass
    return out


_LICENSE_RE = re.compile(
    r"(?P<product>StrategyQuant\s+X[^()]*?Build\s+\d+)\s*"
    r"\((?P<license_type>[^)]+?)\)\s*-\s*"
    r"(?:valid until|valid till)\s+(?P<expires>\d{1,2}\.\d{2}\.\d{4})"
    r"(?:.*?license\s+(?P<code>[0-9A-Fa-f]+))?",
    re.IGNORECASE,
)


def _parse_license_info(raw: str) -> dict[str, Any]:
    """Parse sqcli's `-license action=info` response into a structured dict."""
    if not raw:
        return {"parsed": False}
    text = raw.strip().splitlines()[0] if raw.strip() else ""
    m = _LICENSE_RE.search(text)
    if not m:
        return {"parsed": False, "raw_first_line": text}
    expires_dotted = m.group("expires")
    expires_iso = None
    try:
        d, mo, y = expires_dotted.split(".")
        expires_iso = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    except (ValueError, AttributeError):
        pass
    return {
        "parsed": True,
        "product": (m.group("product") or "").strip(),
        "license_type": (m.group("license_type") or "").strip(),
        "expires": expires_dotted,
        "expires_iso": expires_iso,
        "code": m.group("code"),
    }


def _parse_progress_from_log(
    lines: list[str], *, project: str | None = None
) -> dict[str, Any]:
    """Walk a log tail and extract progress markers, optionally project-filtered."""
    events: list[dict[str, Any]] = []
    latest: dict[str, dict[str, Any]] = {}
    needle = project.lower() if project else None
    for raw_line in lines:
        line = raw_line
        line_lower = line.lower()
        ts_match = re.match(r"^(\d{2}:\d{2}:\d{2})\s+(.*)$", line)
        ts = ts_match.group(1) if ts_match else None
        body = ts_match.group(2) if ts_match else line
        for pat, kind, names in _PROGRESS_PATTERNS:
            m = pat.search(body)
            if not m:
                continue
            if needle:
                if kind == "project_event":
                    # project_event captures the project name in group 1; match exactly.
                    if (m.group(1) or "").lower() != needle:
                        continue
                elif needle not in line_lower:
                    continue
            groups = m.groups()
            data: dict[str, Any] = dict(zip(names, groups, strict=False)) if names else {}
            event = {
                "kind": kind,
                "line": line,
                "timestamp": ts,
                "data": data,
            }
            events.append(event)
            latest[kind] = event
    return {
        "events": events[-50:],
        "latest": latest,
        "event_count": len(events),
    }


class ProjectStartArgs(BaseModel):
    name: str = Field(..., description="Project name as shown in SQ workspace (e.g. 'Builder', 'Retester').")
    only_task: int | None = Field(
        None, description="If set, run only task N (1-indexed) instead of the whole project.", ge=1, le=999
    )
    from_task: int | None = Field(
        None, description="If set, run from task N onwards.", ge=1, le=999
    )
    wait: bool = Field(
        False, description="Block until the project finishes. For long projects leave False and use monitor_status instead."
    )

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return validate_project_name(v)


class ProjectNameArgs(BaseModel):
    name: str = Field(..., description="Project name.")

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return validate_project_name(v)


class ProjectConfigArgs(BaseModel):
    name: str = Field(..., description="Project name.")
    file: str = Field(..., description="Path to .cfx file (load) or destination path (save).")
    action: Literal["load", "save"] = Field(..., description="load: replace project config from file. save: dump current to file.")

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return validate_project_name(v)


class ProjectCloneArgs(BaseModel):
    source: str = Field(..., description="Source project name (must exist).")
    dest: str = Field(..., description="Destination project name (must NOT exist).")
    symbol: str | None = Field(
        None,
        description="If set, rewrite every `symbol=` attribute in the task XMLs to this value.",
    )
    timeframe: str | None = Field(
        None,
        description=(
            "If set, rewrite every recognised `timeframe=` attribute (M1, H1, D1, ...). "
            "Boolean-style timeframe attributes are left alone."
        ),
    )
    date_from: str | None = Field(
        None,
        description=(
            "If set, rewrite every `dateFrom=` attribute (yyyy.MM.dd). "
            "Both string and epoch-ms forms are patched."
        ),
    )
    date_to: str | None = Field(
        None,
        description="If set, rewrite every `dateTo=` attribute (yyyy.MM.dd). Both forms patched.",
    )

    @field_validator("source", "dest")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return validate_project_name(v)

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


class CfxValidateArgs(BaseModel):
    path: str | None = Field(
        None, description="Absolute path to a .cfx file. If omitted, `project` is used."
    )
    project: str | None = Field(
        None, description="Project name (uses <projects_dir>/<name>/project.cfx)."
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str | None) -> str | None:
        return validate_project_name(v) if v else v


class ProjectSnapshotArgs(BaseModel):
    project: str = Field(..., description="Project name to snapshot.")
    label: str | None = Field(
        None, description="Optional label appended to the backup filename."
    )
    list_existing: bool = Field(
        False,
        description="If True, list existing snapshots without creating a new one.",
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class ProjectCreateFromTemplateArgs(BaseModel):
    dest: str = Field(..., description="New project name (must not exist).")
    instrument: str | None = Field(
        None,
        description="Instrument key to match against <InstrumentInfo instrument=> in template .cfx files.",
    )
    data_symbol: str | None = Field(
        None,
        description="Data-side symbol to match against <Symbol name=> in template .cfx files.",
    )
    symbol: str | None = Field(
        None,
        description="Override the value rewritten into Chart.symbol. Defaults to data_symbol.",
    )
    timeframe: str | None = Field(None, description="Timeframe for the new project.")
    date_from: str | None = Field(None, description="dateFrom (yyyy.MM.dd).")
    date_to: str | None = Field(None, description="dateTo (yyyy.MM.dd).")
    prefer_source: str | None = Field(
        None,
        description="If multiple template projects match, prefer this source project name.",
    )

    @field_validator("dest", "prefer_source")
    @classmethod
    def _v_name(cls, v: str | None) -> str | None:
        return validate_project_name(v) if v else v

    @field_validator("symbol", "data_symbol", "instrument")
    @classmethod
    def _v_sym(cls, v: str | None) -> str | None:
        return validate_symbol(v) if v else v

    @field_validator("timeframe")
    @classmethod
    def _v_tf(cls, v: str | None) -> str | None:
        return validate_timeframe(v) if v else v

    @field_validator("date_from", "date_to")
    @classmethod
    def _v_date(cls, v: str | None) -> str | None:
        return validate_date(v) if v else v


class TaskProgressArgs(BaseModel):
    project: str | None = Field(
        None, description="If set, restrict events to lines mentioning this project."
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str | None) -> str | None:
        return validate_project_name(v) if v else v


class EngineStartArgs(BaseModel):
    timeout_seconds: float = Field(
        90.0,
        ge=10.0,
        le=300.0,
        description="Max seconds to wait for the HTTP API ready signal.",
    )


class EngineStopArgs(BaseModel):
    timeout_seconds: float = Field(
        15.0, ge=2.0, le=120.0, description="Max seconds to wait for graceful shutdown."
    )


class EngineLogStreamArgs(BaseModel):
    duration_seconds: float = Field(
        30.0,
        ge=2.0,
        le=600.0,
        description="Total time to stream the engine log (2 s – 10 min).",
    )
    poll_interval_seconds: float = Field(
        2.0, ge=0.5, le=30.0, description="How often to forward newly captured lines."
    )
    project: str | None = Field(
        None, description="If set, only forward lines that mention this project."
    )
    progress: bool = Field(
        True, description="Also extract Builder/Optimizer progress events from the stream."
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str | None) -> str | None:
        return validate_project_name(v) if v else v


class ProjectPrecheckArgs(BaseModel):
    project: str = Field(..., description="Project name to precheck.")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


class CfxSetInstrumentArgs(BaseModel):
    project: str = Field(..., description="Project to retarget.")
    symbol: str = Field(..., description="Target symbol (e.g. 'BTCUSDT'). Must exist in data.db DATA table.")
    timeframe: str = Field("M1", description="Target timeframe (e.g. 'M1', 'H1', 'M5').")
    dat_symbol: str | None = Field(
        None,
        description=(
            "Override the <Symbol name=...> value. Defaults to '<symbol>_<TF>' if the "
            "history file follows that pattern (e.g. 'GBPJPY_M1_dukas'), or just '<symbol>_<TF>' "
            "(e.g. 'BTCUSDT_M1') when no broker suffix exists."
        ),
    )
    snapshot: bool = Field(True, description="Take a snapshot before patching.")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("symbol")
    @classmethod
    def _v_symbol(cls, v: str) -> str:
        return validate_symbol(v)

    @field_validator("timeframe")
    @classmethod
    def _v_tf(cls, v: str) -> str:
        return validate_timeframe(v)


class CfxApplyPatchArgs(BaseModel):
    project: str = Field(..., description="Project name. Patches in-place; an auto-snapshot is taken first.")
    symbol: str | None = Field(None, description="Replace `symbol=` attributes throughout task XMLs.")
    timeframe: str | None = Field(None, description="Replace `timeframe=` attributes (only if value looks like a TF).")
    date_from: str | None = Field(None, description="Replace `dateFrom=` (yyyy.MM.dd). Skipped on tasks where dateFrom=0.")
    date_to: str | None = Field(None, description="Replace `dateTo=` (yyyy.MM.dd). Skipped on tasks where dateTo=0.")
    snapshot: bool = Field(True, description="Take a snapshot before patching (default True). Use False for ephemeral patches.")
    snapshot_label: str | None = Field(None, description="Optional label for the snapshot file.")

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

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


class ProjectLoadAndStartArgs(BaseModel):
    project: str = Field(..., description="Project name. CANNOT contain spaces — sqcli's HTTP parser splits on whitespace.")
    sync_databanks: list[str] | None = Field(
        None,
        description=(
            "Databank names to sync from disk into JVM memory before starting. "
            "Common values: 'Results' for Retester, 'Strategies to optimize' for Optimizer. "
            "Omit to skip sync entirely."
        ),
    )
    sync_wait_seconds: float = Field(
        8.0, ge=0.0, le=300.0,
        description="Seconds to wait after syncfromfiles before starting, to ensure load completes (sqcli's syncfromfiles is async)."
    )
    only_task: int | None = Field(None, ge=1, le=999)
    from_task: int | None = Field(None, ge=1, le=999)

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        v = validate_project_name(v)
        if " " in v:
            raise ValueError(
                f"project name {v!r} contains spaces — sqcli's HTTP API parser cannot reference it. "
                "Clone with an underscore-only name first."
            )
        return v


class ProjectForceRemoveArgs(BaseModel):
    project: str = Field(..., description="Project name to remove from BOTH engine internal state AND filesystem.")
    delete_directory: bool = Field(True, description="If True, rm -rf the project directory after engine removal.")


class EngineHelpArgs(BaseModel):
    command: str | None = Field(
        None,
        description=(
            "sqcli command to query (e.g. 'project', 'databank', 'tools'). Omit to "
            "get the top-level help (list of all commands)."
        ),
    )

    @field_validator("command")
    @classmethod
    def _v_command(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > 64:
            raise ValueError("command too long")
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("command must be alphanumeric (with - and _ allowed)")
        return v


class ProjectWaitForCompletionArgs(BaseModel):
    project: str = Field(..., description="Project name to wait on.")
    databank: str = Field(
        "Results",
        description="Databank to poll for stalled growth (typically 'Results' for Builders/Retesters).",
    )
    max_wait_seconds: float = Field(
        3600.0,
        ge=10.0,
        le=86_400.0,
        description=(
            "Hard ceiling on the wait (10s..24h). 1h default fits most Retesters; "
            "raise it for big Builder runs."
        ),
    )
    poll_interval_seconds: float = Field(
        30.0, ge=2.0, le=600.0,
        description="How often to recheck status + databank count.",
    )
    stable_polls_required: int = Field(
        3,
        ge=1,
        le=20,
        description=(
            "How many consecutive polls with zero databank growth AND status not running "
            "before we declare the project done. Lower = faster detection, higher = more robust."
        ),
    )
    require_log_completion_marker: bool = Field(
        False,
        description=(
            "If True, also require 'All tasks completed' or a project_finished log event "
            "before returning ok. If False, count stable-status polling as enough."
        ),
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("databank")
    @classmethod
    def _v_databank(cls, v: str) -> str:
        from sq_mcp._validation import validate_databank_name
        return validate_databank_name(v)


class ProjectRunToCompletionArgs(BaseModel):
    project: str = Field(..., description="Project name (must be underscore-only — no spaces).")
    databank: str = Field("Results", description="Databank to poll for growth.")
    sync_databanks: list[str] | None = Field(
        None,
        description=(
            "Databanks to sync from disk before starting (e.g. ['Strategies to retest'] "
            "for a Retester run). Use this if you just dropped .sqx files into the input "
            "databank — fixes the syncfromfiles race condition."
        ),
    )
    sync_wait_seconds: float = Field(8.0, ge=0.0, le=300.0)
    only_task: int | None = Field(None, ge=1, le=999)
    from_task: int | None = Field(None, ge=1, le=999)
    max_wait_seconds: float = Field(3600.0, ge=10.0, le=86_400.0)
    poll_interval_seconds: float = Field(30.0, ge=2.0, le=600.0)
    stable_polls_required: int = Field(3, ge=1, le=20)
    force_sync_final: bool = Field(
        True,
        description=(
            "After completion, call `-databank action=synctofiles` so JVM-resident results "
            "are flushed to disk. Critical because most databanks default to "
            "syncType=Auto-sync never."
        ),
    )

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        v = validate_project_name(v)
        if " " in v:
            raise ValueError(
                f"project name {v!r} contains spaces — sqcli's HTTP API parser cannot reference it. "
                "Clone with an underscore-only name first."
            )
        return v

    @field_validator("databank")
    @classmethod
    def _v_databank(cls, v: str) -> str:
        from sq_mcp._validation import validate_databank_name
        return validate_databank_name(v)


class PipelineBuildFilterRetestArgs(BaseModel):
    """One-shot Builder→Filter→Retester pipeline."""
    builder_project: str = Field(
        ...,
        description=(
            "Existing Builder project. Set start_builder=False to skip and use whatever's "
            "already in its Results databank."
        ),
    )
    retester_project: str = Field(
        ...,
        description=(
            "Existing Retester project. The pipeline will copy filtered Builder survivors "
            "into the Retester's input databank (typically 'Strategies to retest')."
        ),
    )
    retester_input_databank: str = Field(
        "Strategies to retest",
        description="Retester input databank name. SQ X default is 'Strategies to retest'.",
    )
    top_n: int = Field(
        30, ge=1, le=5000,
        description="Max strategies to promote from Builder → Retester.",
    )
    start_builder: bool = Field(
        True,
        description="If True, kick off the Builder. If False, skip straight to filter step.",
    )
    start_retester: bool = Field(
        True,
        description="If True, kick off the Retester after promoting. If False, stop after copying.",
    )
    # Builder phase filter
    builder_filter_min_trades: int | None = Field(20, ge=0)
    builder_filter_max_drawdown_pct: float | None = Field(None, ge=0, le=100)
    builder_filter_min_profit_to_dd: float | None = Field(1.5, ge=0)
    builder_filter_min_fitness_is: float | None = Field(None)
    builder_sort_by: Literal[
        "fitness_is", "fitness_full", "profit_to_dd_ratio", "net_profit",
    ] = Field("profit_to_dd_ratio")
    # Pipeline timing
    max_wait_builder_seconds: float = Field(
        7200.0,
        ge=10.0,
        le=172_800.0,
        description="Hard ceiling on Builder wait (10s..48h). Default 2h.",
    )
    max_wait_retester_seconds: float = Field(
        3600.0,
        ge=10.0,
        le=86_400.0,
    )
    poll_interval_seconds: float = Field(60.0, ge=5.0, le=600.0)
    stable_polls_required: int = Field(3, ge=1, le=20)
    sync_wait_seconds: float = Field(
        10.0, ge=0.0, le=120.0,
        description="Wait after syncing the retester input databank before starting.",
    )
    dry_run: bool = Field(
        False,
        description="If True, run filter/promote but skip the engine start/wait phases.",
    )

    @field_validator("builder_project", "retester_project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        v = validate_project_name(v)
        if " " in v:
            raise ValueError(
                f"project name {v!r} contains spaces — sqcli's HTTP API parser cannot reference it."
            )
        return v


# ---- waiter & pipeline helpers ----------------------------------------------


async def _wait_for_completion(
    eng: EngineClient,
    *,
    project: str,
    databank: str,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    stable_polls_required: int,
    require_log_completion_marker: bool,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Shared logic for waiting until a project ends. Returns a structured report.

    Done is declared when any of:
      * status text contains 'finished'/'idle'/'stopped' AND databank growth has
        been zero for `stable_polls_required` consecutive polls;
      * (optionally) the engine log emits 'All tasks completed' since the call started.
    Always returns within max_wait_seconds (caller checks `timed_out`).
    """
    deadline = time.time() + max_wait_seconds
    start_time = time.time()
    db_dir = eng.config.projects_dir / project / "databanks" / databank
    prev_count = _count_sqx(db_dir)
    stable_count = 0
    snapshots: list[dict[str, Any]] = []
    completion_marker_seen = False
    baseline_log_len = len(eng.recent_log)

    if ctx is not None:
        await ctx.info(
            f"waiting on {project!r} (max={max_wait_seconds:.0f}s, poll={poll_interval_seconds:.0f}s)"
        )

    while time.time() < deadline:
        loop_start = time.time()
        elapsed = loop_start - start_time

        # status
        status_text = ""
        try:
            status_text = await eng.call(f"-project action=status name={project}", timeout=30.0)
        except EngineError:
            pass
        status_lower = status_text.lower()
        looks_idle = bool(
            status_text
            and any(t in status_lower for t in ("finished", "idle", "stopped"))
            and not any(t in status_lower for t in ("running", "in progress", "active"))
        )

        # databank count
        current_count = _count_sqx(db_dir)
        delta = current_count - prev_count

        # log markers since baseline
        if not completion_marker_seen:
            for line in eng.recent_log[baseline_log_len:]:
                if (
                    "all tasks completed" in line.lower()
                    or re.search(rf"Project\s+{re.escape(project)}\s+finished", line, re.IGNORECASE)
                ):
                    completion_marker_seen = True
                    break

        snapshot = {
            "elapsed": round(elapsed, 1),
            "count": current_count,
            "delta": delta,
            "looks_idle": looks_idle,
            "completion_marker": completion_marker_seen,
            "stable_count": stable_count,
        }
        snapshots.append(snapshot)
        if ctx is not None and (delta != 0 or looks_idle or completion_marker_seen):
            await ctx.info(
                f"[wait/{project}] t={elapsed:.0f}s n={current_count} (+{delta}) "
                f"idle={looks_idle} done={completion_marker_seen}"
            )

        if looks_idle and delta == 0:
            stable_count += 1
        else:
            stable_count = 0
        prev_count = current_count

        if completion_marker_seen:
            return _wait_result(
                ok=True,
                reason="completion_marker",
                elapsed=elapsed,
                final_count=current_count,
                snapshots=snapshots,
            )
        if (
            stable_count >= stable_polls_required
            and (completion_marker_seen or not require_log_completion_marker)
        ):
            return _wait_result(
                ok=True,
                reason=f"stable_for_{stable_count}_polls",
                elapsed=elapsed,
                final_count=current_count,
                snapshots=snapshots,
            )

        sleep_remaining = max(0.0, poll_interval_seconds - (time.time() - loop_start))
        if sleep_remaining > 0:
            await asyncio.sleep(sleep_remaining)

    return _wait_result(
        ok=False,
        reason="timeout",
        elapsed=time.time() - start_time,
        final_count=_count_sqx(db_dir),
        snapshots=snapshots,
        timed_out=True,
    )


def _wait_result(
    *,
    ok: bool,
    reason: str,
    elapsed: float,
    final_count: int,
    snapshots: list[dict[str, Any]],
    timed_out: bool = False,
) -> dict[str, Any]:
    """Compose the final wait report — last 10 snapshots + summary."""
    return {
        "ok": ok,
        "completed": ok,
        "timed_out": timed_out,
        "reason": reason,
        "elapsed_seconds": round(elapsed, 1),
        "final_strategy_count": final_count,
        "polls": len(snapshots),
        "tail_snapshots": snapshots[-10:],
    }


def _count_sqx(db_dir: Path) -> int:
    """Count .sqx files in a databank directory.

    Returns 0 (not an exception) for missing/unreadable directories — callers
    poll this every interval during long runs and we don't want a transient
    permission glitch to break the waiter.
    """
    if not db_dir.exists():
        return 0
    try:
        return sum(1 for _ in db_dir.rglob("*.sqx"))
    except OSError:
        return 0

    @field_validator("project")
    @classmethod
    def _v_project(cls, v: str) -> str:
        return validate_project_name(v)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "List all SQ X projects in the user workspace. Falls back to a "
            "filesystem scan of <projects_dir>/*/project.cfx when the engine "
            "command is unavailable (Build 143 returns 'Error: Not implemented')."
        )
    )
    async def project_list(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            fs_projects = _scan_projects_fs(eng.config.projects_dir)
            try:
                text = await eng.call("-project action=list")
            except EngineError as exc:
                return {
                    "ok": True,
                    "projects": [p["name"] for p in fs_projects],
                    "projects_detail": fs_projects,
                    "source": "filesystem",
                    "fallback_reason": f"engine error: {exc}",
                }
            r = parse_listing_response(text, "projects")
            if r["ok"] and r["projects"]:
                return r | {"source": "engine"}
            return {
                "ok": True,
                "projects": [p["name"] for p in fs_projects],
                "projects_detail": fs_projects,
                "source": "filesystem",
                "fallback_reason": (
                    "engine returned errors" if not r["ok"] else "engine returned empty list"
                ),
                "engine_errors": r.get("errors"),
                "engine_raw": r.get("raw"),
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Start a project (Build / Retest / Optimize / WalkForward / etc.). Returns immediately by default; use project_status to poll.")
    async def project_start(args: ProjectStartArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            await ctx.info(f"starting project {args.name!r}")
            if args.only_task is not None:
                cmd = f"-project action=startOnlyTask name={args.name} task={args.only_task}"
            elif args.from_task is not None:
                cmd = f"-project action=startFromTask name={args.name} task={args.from_task}"
            else:
                cmd = f"-project action=start name={args.name}"
            text = await eng.call(cmd)
            return parse_response(text) | {"project": args.name, "wait_blocking": args.wait}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Stop a running project.")
    async def project_stop(args: ProjectNameArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            await ctx.info(f"stopping project {args.name!r}")
            text = await eng.call(f"-project action=stop name={args.name}")
            return parse_response(text) | {"project": args.name}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Pause a running project (most project types support this).")
    async def project_pause(args: ProjectNameArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            text = await eng.call(f"-project action=pause name={args.name}")
            return parse_response(text) | {"project": args.name}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Resume a paused project.")
    async def project_resume(args: ProjectNameArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            text = await eng.call(f"-project action=resume name={args.name}")
            return parse_response(text) | {"project": args.name}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Get current status of a project (running / idle / progress %). "
            "Falls back to filesystem + engine-log-tail inspection if the SQ build "
            "does not support `-project action=status`."
        )
    )
    async def project_status(args: ProjectNameArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            try:
                text = await eng.call(f"-project action=status name={args.name}")
            except EngineError as exc:
                return _project_status_fs(eng, args.name) | {"engine_error": str(exc)}
            resp = parse_response(text)
            if resp["ok"]:
                return resp | {
                    "project": args.name,
                    "lines": parse_list(text),
                    "source": "engine",
                }
            return _project_status_fs(eng, args.name) | {
                "engine_error": resp.get("errors"),
                "engine_raw": resp.get("raw"),
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Inspect a project's .cfx config file (tasks, databanks, version) without modifying it.")
    async def project_inspect_cfx(args: ProjectNameArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.name / "project.cfx"
            if not cfx_path.exists():
                return {"ok": False, "error": f"project.cfx not found at {cfx_path}"}
            info = parse_cfx(cfx_path)
            return {"ok": True, **info.as_dict()}
        except (EngineError, ValidationError, ValueError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Save / load a project's configuration from a .cfx file.")
    async def project_config(args: ProjectConfigArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            path = resolve_safe_path(args.file, must_exist=(args.action == "load"))
            if args.action == "load":
                text = await eng.call(f"-project action=loadconfig name={args.name} file={path}")
            else:
                text = await eng.call(f"-project action=saveconfig name={args.name} file={path}")
            return parse_response(text) | {"project": args.name, "file": str(path)}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Remove (delete) a project from the workspace. Irreversible.")
    async def project_remove(args: ProjectNameArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            await ctx.warning(f"removing project {args.name!r} — irreversible")
            text = await eng.call(f"-project action=remove name={args.name}")
            return parse_response(text) | {"project": args.name}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Clone an existing project to a new name, optionally rewriting symbol / "
            "timeframe / dateFrom / dateTo across every task XML inside the .cfx. "
            "Pure filesystem operation — does not touch the running engine. The new "
            "project becomes visible to sqcli after engine restart or `-project action=loadconfig`."
        )
    )
    async def project_clone(args: ProjectCloneArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            pdir = eng.config.projects_dir
            src_dir = pdir / args.source
            dst_dir = pdir / args.dest
            src_cfx = src_dir / "project.cfx"
            if not src_cfx.is_file():
                return {
                    "ok": False,
                    "error": f"source project.cfx not found: {src_cfx}",
                    "source": args.source,
                }
            if dst_dir.exists():
                return {
                    "ok": False,
                    "error": f"destination project already exists: {dst_dir}",
                    "dest": args.dest,
                }
            await ctx.info(
                f"cloning {args.source!r} → {args.dest!r} "
                f"(symbol={args.symbol}, tf={args.timeframe}, "
                f"dateFrom={args.date_from}, dateTo={args.date_to})"
            )
            dst_dir.mkdir(parents=True, exist_ok=False)
            (dst_dir / "databanks").mkdir(exist_ok=True)
            try:
                report = _clone_cfx_zip(
                    src_cfx,
                    dst_dir / "project.cfx",
                    new_name=args.dest,
                    symbol=args.symbol,
                    timeframe=args.timeframe,
                    date_from=args.date_from,
                    date_to=args.date_to,
                )
            except Exception as exc:
                # Cleanup half-created project on failure
                try:
                    if (dst_dir / "project.cfx").exists():
                        (dst_dir / "project.cfx").unlink()
                    if (dst_dir / "databanks").exists():
                        (dst_dir / "databanks").rmdir()
                    dst_dir.rmdir()
                except OSError:
                    pass
                return safe_error_payload(exc)
            return {
                "ok": True,
                "source": args.source,
                "dest": args.dest,
                "dest_cfx": str(dst_dir / "project.cfx"),
                "rewrites": {
                    "symbol": args.symbol,
                    "timeframe": args.timeframe,
                    "date_from": args.date_from,
                    "date_to": args.date_to,
                },
                "changes": report["total_changes"],
                "per_file_changes": report["per_file"],
                "note": (
                    "Restart sqcli or call project_config(action='load') so the engine picks up the new project."
                ),
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Return the last N lines of the sqcli engine's stdout log (already noise-filtered). "
            "Useful for debugging why a project_start failed or what the engine was doing at a given time."
        )
    )
    async def engine_log_tail(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            tail = eng.recent_log
            return {
                "ok": True,
                "lines": tail[-100:],
                "total_buffered": len(tail),
                "fatal_error": eng._state.fatal_error,
                "running": eng.is_running,
            }
        except EngineError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Extract a broker/source/dataType/barType registry from every existing project's "
            ".cfx file. The H2 broker DB is locked while sqcli runs, so use this to discover the "
            "internal numeric codes needed when hand-crafting task XMLs for new symbols. "
            "Returns two maps: `data_symbols` (keyed by data-side symbol name, with source/barType) "
            "and `instruments` (keyed by instrument name, with broker/dataType)."
        )
    )
    async def broker_registry(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            pdir = eng.config.projects_dir
            if not pdir.exists():
                return {"ok": False, "error": f"projects dir not found: {pdir}"}
            all_rows: list[dict] = []
            scanned: list[str] = []
            failed: list[dict] = []
            for proj_dir in sorted(pdir.iterdir(), key=lambda p: p.name.lower()):
                cfx = proj_dir / "project.cfx"
                if not cfx.is_file():
                    continue
                scanned.append(proj_dir.name)
                try:
                    rows = _extract_broker_entries(cfx, proj_dir.name)
                except Exception as exc:  # noqa: BLE001
                    failed.append({"project": proj_dir.name, "error": str(exc)})
                    continue
                all_rows.extend(rows)

            data_symbols: dict[str, list[dict]] = {}
            instruments: dict[str, list[dict]] = {}
            for r in all_rows:
                bucket_map = data_symbols if r["kind"] == "data_symbol" else instruments
                combo = {k: r.get(k) for k in r if k not in ("kind", "key", "project", "task_xml")}
                combo_tuple = tuple(sorted(combo.items()))
                bucket = bucket_map.setdefault(r["key"], [])
                existing = next(
                    (b for b in bucket if tuple(sorted({k: b[k] for k in combo}.items())) == combo_tuple),
                    None,
                )
                if existing is None:
                    bucket.append({**combo, "seen_in": [r["project"]]})
                elif r["project"] not in existing["seen_in"]:
                    existing["seen_in"].append(r["project"])

            return {
                "ok": True,
                "projects_scanned": scanned,
                "projects_scanned_count": len(scanned),
                "total_entries": len(all_rows),
                "data_symbols": data_symbols,
                "instruments": instruments,
                "data_symbols_count": len(data_symbols),
                "instruments_count": len(instruments),
                "failed": failed,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Structural validation of a .cfx archive. Reports the project name and "
            "version, every task with an exists flag, missing files referenced by "
            "config.xml, orphan task XMLs present in the zip but not referenced, and "
            "malformed XML. Pass either `path=` (any .cfx) or `project=` (existing project)."
        )
    )
    async def cfx_validate(args: CfxValidateArgs, ctx: Context) -> dict:
        try:
            if not args.path and not args.project:
                return {"ok": False, "error": "must supply either path or project"}
            eng = get_engine(ctx)
            if args.path:
                cfx_path = resolve_safe_path(args.path, must_exist=True)
            else:
                cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            return _validate_cfx_structure(cfx_path) | {"cfx_path": str(cfx_path)}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Snapshot a project's project.cfx to a timestamped backup "
            "(project.cfx.bak.<UTC-ts>[.label]) so subsequent project_clone / manual "
            "edits are rollback-safe. Pass list_existing=True to enumerate existing "
            "snapshots without creating a new one."
        )
    )
    async def project_snapshot(args: ProjectSnapshotArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            pdir = eng.config.projects_dir / args.project
            cfx_path = pdir / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found at {cfx_path}"}
            existing_info: list[dict[str, Any]] = []
            for p in sorted(pdir.glob("project.cfx.bak.*")):
                try:
                    stat = p.stat()
                    existing_info.append(
                        {
                            "path": str(p),
                            "size": stat.st_size,
                            "mtime": datetime.fromtimestamp(
                                stat.st_mtime, tz=timezone.utc
                            ).isoformat(),
                        }
                    )
                except OSError:
                    existing_info.append(
                        {"path": str(p), "size": None, "mtime": None}
                    )
            if args.list_existing:
                return {
                    "ok": True,
                    "project": args.project,
                    "cfx_path": str(cfx_path),
                    "snapshots": existing_info,
                    "snapshot_count": len(existing_info),
                }
            await ctx.info(f"snapshotting {cfx_path}")
            dst = _make_snapshot(cfx_path, label=args.label)
            return {
                "ok": True,
                "project": args.project,
                "snapshot_path": str(dst),
                "size": dst.stat().st_size,
                "previous_snapshots": existing_info,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Create a new project by finding a template that uses a target instrument "
            "or data symbol, and cloning it with optional symbol/TF/date rewrites. "
            "Picks the most recently modified matching source (or `prefer_source` if "
            "set). Use broker_registry first to discover valid instrument/data_symbol keys."
        )
    )
    async def project_create_from_template(
        args: ProjectCreateFromTemplateArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            pdir = eng.config.projects_dir
            dst_dir = pdir / args.dest
            if dst_dir.exists():
                return {
                    "ok": False,
                    "error": f"destination project already exists: {dst_dir}",
                    "dest": args.dest,
                }
            if not args.instrument and not args.data_symbol:
                return {
                    "ok": False,
                    "error": "must provide either instrument or data_symbol",
                }
            best_cfx, candidates = _find_template_source(
                pdir,
                instrument=args.instrument,
                data_symbol=args.data_symbol,
                prefer_source=args.prefer_source,
            )
            if best_cfx is None:
                return {
                    "ok": False,
                    "error": (
                        f"no source project uses instrument={args.instrument!r} or "
                        f"data_symbol={args.data_symbol!r}. "
                        "Run broker_registry to see available keys."
                    ),
                    "candidates": [],
                }
            eff_symbol = args.symbol or args.data_symbol
            await ctx.info(
                f"creating {args.dest!r} from template {best_cfx.parent.name!r} "
                f"(symbol={eff_symbol}, tf={args.timeframe}, dateFrom={args.date_from})"
            )
            dst_dir.mkdir(parents=True, exist_ok=False)
            (dst_dir / "databanks").mkdir(exist_ok=True)
            try:
                report = _clone_cfx_zip(
                    best_cfx,
                    dst_dir / "project.cfx",
                    new_name=args.dest,
                    symbol=eff_symbol,
                    timeframe=args.timeframe,
                    date_from=args.date_from,
                    date_to=args.date_to,
                )
            except Exception as exc:  # noqa: BLE001 — cleanup before re-raising
                try:
                    if (dst_dir / "project.cfx").exists():
                        (dst_dir / "project.cfx").unlink()
                    if (dst_dir / "databanks").exists():
                        (dst_dir / "databanks").rmdir()
                    dst_dir.rmdir()
                except OSError:
                    pass
                return safe_error_payload(exc)
            return {
                "ok": True,
                "dest": args.dest,
                "dest_cfx": str(dst_dir / "project.cfx"),
                "source": best_cfx.parent.name,
                "source_cfx": str(best_cfx),
                "candidates_count": len(candidates),
                "candidates": [
                    {"project": c["project"], "match_count": len(c["matches"])}
                    for c in candidates
                ],
                "rewrites": {
                    "symbol": eff_symbol,
                    "timeframe": args.timeframe,
                    "date_from": args.date_from,
                    "date_to": args.date_to,
                },
                "changes": report["total_changes"],
                "per_file_changes": report["per_file"],
                "note": (
                    "Restart sqcli or call project_config(action='load') so the engine "
                    "picks up the new project."
                ),
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Scan the buffered sqcli log tail for Builder/Optimizer progress markers "
            "(Built N strategies, Generation N/M, Backtest N/M, Best fitness, Progress %, "
            "task/project lifecycle events). Returns the list of detected events plus a "
            "`latest` dict keyed by event kind. Optionally filter by project name."
        )
    )
    async def task_progress(args: TaskProgressArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            tail = eng.recent_log
            result = _parse_progress_from_log(tail, project=args.project)
            return {
                "ok": True,
                "project": args.project,
                "running": eng.is_running,
                "log_lines_scanned": len(tail),
                **result,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Start the sqcli engine. First tries to attach to an already-running sqcli on "
            "the configured HTTP port (no spawn). Only spawns a new subprocess if nothing is "
            "listening. Reports which mode was used."
        )
    )
    async def engine_start(args: EngineStartArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            if eng.is_running:
                return {
                    "ok": True,
                    "already_running": True,
                    "running": True,
                    "attached": eng.attached,
                    "http_url": eng.config.http_url,
                    "sqcli": str(eng.config.sqcli),
                }
            await ctx.info("probing for external sqcli; will spawn if absent")
            t0 = time.time()
            mode = await eng.attach_or_start(timeout=args.timeout_seconds)
            elapsed = time.time() - t0
            return {
                "ok": True,
                "already_running": False,
                "running": eng.is_running,
                "mode": mode,
                "attached": eng.attached,
                "elapsed_seconds": round(elapsed, 2),
                "http_url": eng.config.http_url,
                "sqcli": str(eng.config.sqcli),
            }
        except EngineError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Stop the sqcli engine. If the plugin spawned the subprocess: sends `-exit`, "
            "then SIGTERM, then SIGKILL. If the engine was attached (externally spawned): "
            "leaves the external process alone, only detaches the plugin from it."
        )
    )
    async def engine_stop(args: EngineStopArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            if not eng.is_running:
                return {"ok": True, "was_running": False, "running": False}
            was_attached = eng.attached
            if was_attached:
                await ctx.info("detaching from external sqcli (process left alive)")
            else:
                await ctx.warning("stopping sqcli engine subprocess")
            t0 = time.time()
            await eng.stop(timeout=args.timeout_seconds)
            elapsed = time.time() - t0
            return {
                "ok": True,
                "was_running": True,
                "was_attached": was_attached,
                "running": eng.is_running,
                "elapsed_seconds": round(elapsed, 2),
            }
        except EngineError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Report engine state: running, attached/spawned, http_url, pid (None for "
            "attached), log path being tailed (if any), recent log line count, fatal error."
        )
    )
    async def engine_status(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            return {
                "ok": True,
                "running": eng.is_running,
                "attached": eng.attached,
                "pid": eng.pid,
                "http_url": eng.config.http_url,
                "sqcli": str(eng.config.sqcli),
                "log_path": str(eng.config.log_path) if eng.config.log_path else None,
                "recent_log_lines": len(eng.recent_log),
                "fatal_error": eng._state.fatal_error,
            }
        except EngineError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Stream the engine log for a bounded duration. Polls the buffered log every "
            "poll_interval_seconds and emits an MCP `info` notification each time new "
            "lines appear (so attached clients see incremental updates). Also extracts "
            "Builder/Optimizer progress events from the stream when `progress=True`. "
            "Returns the aggregated lines and events at the end."
        )
    )
    async def engine_log_stream(
        args: EngineLogStreamArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            deadline = time.time() + args.duration_seconds
            seen_count = len(eng.recent_log)  # baseline: don't replay past lines
            all_new_lines: list[str] = []
            all_progress: list[dict[str, Any]] = []
            needle = args.project.lower() if args.project else None
            await ctx.info(
                f"engine_log_stream started: duration={args.duration_seconds}s "
                f"interval={args.poll_interval_seconds}s"
                + (f" project={args.project!r}" if args.project else "")
            )
            while time.time() < deadline:
                current = list(eng.recent_log)
                new_lines = current[seen_count:]
                seen_count = len(current)
                if needle:
                    new_lines = [ln for ln in new_lines if needle in ln.lower()]
                if new_lines:
                    preview = " | ".join(ln[:80] for ln in new_lines[-3:])
                    await ctx.info(f"[engine-log +{len(new_lines)}] {preview}")
                    all_new_lines.extend(new_lines)
                    if args.progress:
                        chunk = _parse_progress_from_log(new_lines, project=args.project)
                        all_progress.extend(chunk["events"])
                await asyncio.sleep(args.poll_interval_seconds)
            return {
                "ok": True,
                "project": args.project,
                "duration_seconds": args.duration_seconds,
                "lines": all_new_lines,
                "line_count": len(all_new_lines),
                "progress_events": all_progress if args.progress else None,
                "running": eng.is_running,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Query the engine for license info: product/build, license type (Trial / "
            "Subscription / Permanent), expiry date, license code, computed days_remaining. "
            "Use this BEFORE kicking off a long Builder run so you know if the trial will "
            "expire mid-run."
        )
    )
    async def license_info(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            raw = await eng.call("-license action=info")
            parsed = _parse_license_info(raw)
            if parsed.get("expires_iso"):
                try:
                    exp = datetime.fromisoformat(parsed["expires_iso"])
                    now = datetime.now(timezone.utc).replace(tzinfo=None)
                    parsed["days_remaining"] = (exp - now).days
                    parsed["expiring_soon"] = parsed["days_remaining"] <= 7
                except ValueError:
                    pass
            return {"ok": True, "raw": raw.strip(), **parsed}
        except EngineError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Pre-flight readiness check for running a project. Verifies: engine running, "
            "license has time left, project.cfx exists + is structurally valid, every "
            "referenced symbol is present in SQ's SQLite registry, history .dat files exist "
            "for the referenced date range, and the project isn't already running. Returns "
            "a structured issues list (severity: blocker / warning / info) so callers can "
            "decide whether to proceed."
        )
    )
    async def project_precheck(args: ProjectPrecheckArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            issues: list[dict[str, Any]] = []
            project_dir = eng.config.projects_dir / args.project
            cfx_path = project_dir / "project.cfx"

            checks: dict[str, Any] = {
                "engine_running": eng.is_running,
                "cfx_exists": cfx_path.is_file(),
            }

            # Engine
            if not eng.is_running:
                issues.append({"severity": "blocker", "code": "ENGINE_DOWN",
                               "message": "engine is not running — start it first"})

            # License
            try:
                raw = await eng.call("-license action=info")
                lic = _parse_license_info(raw)
                checks["license"] = lic
                if lic.get("expires_iso"):
                    try:
                        exp = datetime.fromisoformat(lic["expires_iso"])
                        now = datetime.now(timezone.utc).replace(tzinfo=None)
                        days = (exp - now).days
                        checks["license"]["days_remaining"] = days
                        if days < 0:
                            issues.append({"severity": "blocker", "code": "LICENSE_EXPIRED",
                                           "message": f"license expired on {lic['expires']}"})
                        elif days < 1:
                            issues.append({"severity": "warning", "code": "LICENSE_EXPIRES_SOON",
                                           "message": f"license expires in {days} days ({lic['expires']})"})
                    except ValueError:
                        pass
            except EngineError as exc:
                issues.append({"severity": "warning", "code": "LICENSE_QUERY_FAILED",
                               "message": str(exc)})

            # cfx
            if not cfx_path.is_file():
                issues.append({"severity": "blocker", "code": "CFX_MISSING",
                               "message": f"project.cfx not found at {cfx_path}"})
            else:
                cfx_check = _validate_cfx_structure(cfx_path)
                checks["cfx"] = {
                    "project_name": cfx_check.get("project_name"),
                    "tasks": len(cfx_check.get("tasks", [])),
                    "ok": cfx_check.get("ok"),
                    "issues": cfx_check.get("issues", []),
                }
                if not cfx_check.get("ok"):
                    for cfx_issue in cfx_check.get("issues", []):
                        issues.append({"severity": "blocker", "code": "CFX_INVALID",
                                       "message": cfx_issue})

                # Symbol presence
                referenced = _referenced_symbols_from_cfx(cfx_path)
                checks["referenced_symbols"] = sorted(referenced)
                missing_symbols: list[str] = []
                history_root = eng.config.history_dir
                missing_data_dirs: list[str] = []
                if history_root.exists():
                    for sym in referenced:
                        # Try to find the base symbol (strip _M1_dukas suffixes etc.)
                        base_candidates = [sym, sym.split("_")[0]]
                        if not any((history_root / c).is_dir() for c in base_candidates):
                            missing_data_dirs.append(sym)
                checks["missing_history_dirs"] = missing_data_dirs

                # Registry presence
                try:
                    from sq_mcp.tools.symbols import _open_data_registry, _query_data_registry
                    con = _open_data_registry(eng.config.data_dir)
                    if con is not None:
                        try:
                            for sym in referenced:
                                base = sym.split("_")[0]
                                rows = _query_data_registry(con, symbol=base)
                                if not rows:
                                    missing_symbols.append(sym)
                        finally:
                            con.close()
                        checks["missing_in_registry"] = missing_symbols
                        for sym in missing_symbols:
                            issues.append({"severity": "warning", "code": "SYMBOL_NOT_IN_REGISTRY",
                                           "message": f"symbol {sym!r} not found in data.db (may need import)"})
                except Exception:  # noqa: BLE001
                    pass
                for sym in missing_data_dirs:
                    issues.append({"severity": "warning", "code": "NO_LOCAL_HISTORY",
                                   "message": f"no local .dat history dir for {sym!r}"})

                # Date range
                dates = _referenced_dates_from_cfx(cfx_path)
                checks["referenced_dates"] = dates

            # Engine status for this project (already running?)
            try:
                status_text = await eng.call(f"-project action=status name={args.project}")
                checks["engine_status_raw"] = status_text.strip()
                if "running" in status_text.lower():
                    issues.append({"severity": "warning", "code": "ALREADY_RUNNING",
                                   "message": f"project {args.project!r} reports running"})
            except EngineError as exc:
                checks["engine_status_error"] = str(exc)

            blockers = [i for i in issues if i["severity"] == "blocker"]
            return {
                "ok": not blockers,
                "project": args.project,
                "ready": not blockers,
                "blocker_count": len(blockers),
                "warning_count": sum(1 for i in issues if i["severity"] == "warning"),
                "issues": issues,
                "checks": checks,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Retarget a project to a different instrument by rewriting every `<Symbol>` and "
            "`<InstrumentInfo>` block from the SQLite data.db registry. Use this to convert "
            "a forex-Dukascopy project into a crypto/Binance project (or any cross-asset "
            "migration). Auto-snapshots first. Reads authoritative tickSize, pointValue, "
            "commissions, swap, broker_id, source code etc. from the registry — no guessing."
        )
    )
    async def cfx_set_instrument(args: CfxSetInstrumentArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}

            meta = _resolve_instrument_metadata(
                eng.config.data_dir, symbol=args.symbol, timeframe=args.timeframe
            )
            if meta is None:
                return {
                    "ok": False,
                    "error": f"symbol {args.symbol!r} (TF {args.timeframe}) not found in data.db",
                }
            if not meta.get("instrument"):
                await ctx.warning(
                    f"no INSTRUMENTS row for {args.symbol!r}; will rewrite <Symbol> only"
                )

            # Resolve the canonical <Symbol name=...> value
            dat_symbol = args.dat_symbol or f"{args.symbol}_{args.timeframe}"

            snapshot_path: Path | None = None
            if args.snapshot:
                snapshot_path = _make_snapshot(cfx_path, label=f"set_instrument_{args.symbol}")
                await ctx.info(f"snapshot: {snapshot_path.name}")

            symbol_rewrites = 0
            instr_rewrites = 0
            per_file: dict[str, dict[str, int]] = {}
            buf = io.BytesIO()
            with zipfile.ZipFile(cfx_path, "r") as src, zipfile.ZipFile(
                buf, "w", compression=zipfile.ZIP_DEFLATED
            ) as dst:
                for item in src.infolist():
                    raw = src.read(item.filename)
                    if _is_task_xml(item.filename):
                        try:
                            root = safe_fromstring(raw)
                        except etree.XMLSyntaxError:
                            dst.writestr(item, raw)
                            continue
                        file_sym = 0
                        file_instr = 0
                        for el in root.iter():
                            tag = etree.QName(el.tag).localname
                            if tag == "Symbol" and el.get("name") is not None:
                                _project_metadata_onto_symbol(
                                    el, meta, dat_symbol=dat_symbol, timeframe=args.timeframe
                                )
                                file_sym += 1
                            elif tag == "InstrumentInfo":
                                _project_metadata_onto_instrumentinfo(el, meta)
                                file_instr += 1
                        if file_sym or file_instr:
                            per_file[item.filename] = {
                                "symbols": file_sym,
                                "instruments": file_instr,
                            }
                            symbol_rewrites += file_sym
                            instr_rewrites += file_instr
                            raw = etree.tostring(
                                root, encoding="UTF-8", xml_declaration=True, standalone=True
                            )
                    dst.writestr(item, raw)
            _atomic_write_bytes(cfx_path, buf.getvalue())
            return {
                "ok": True,
                "project": args.project,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "dat_symbol": dat_symbol,
                "snapshot_path": str(snapshot_path) if snapshot_path else None,
                "symbol_rewrites": symbol_rewrites,
                "instrument_rewrites": instr_rewrites,
                "files_touched": len(per_file),
                "per_file": per_file,
                "registry_data_row": meta.get("data"),
                "registry_instrument_row": meta.get("instrument"),
            }
        except (EngineError, ValidationError, OSError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Patch an existing project's .cfx in-place — rewrite symbol / timeframe / "
            "dateFrom / dateTo across all task XMLs without cloning. Takes an auto-snapshot "
            "first by default (rollback via the .cfx.bak.* file). Use this to retarget an "
            "active project (e.g. change the date range for next run) without losing the "
            "results databank attached to the project directory."
        )
    )
    async def cfx_apply_patch(args: CfxApplyPatchArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            if not any((args.symbol, args.timeframe, args.date_from, args.date_to)):
                return {"ok": False, "error": "at least one of symbol/timeframe/date_from/date_to must be set"}

            snapshot_path: Path | None = None
            if args.snapshot:
                snapshot_path = _make_snapshot(cfx_path, label=args.snapshot_label)
                await ctx.info(f"snapshot saved: {snapshot_path.name}")

            total_changes: Counter = Counter()
            per_file: dict[str, dict[str, int]] = {}
            inverted_ranges: list[dict[str, Any]] = []
            patched_files: dict[str, bytes] = {}
            with zipfile.ZipFile(cfx_path, "r") as src:
                for item in src.infolist():
                    raw = src.read(item.filename)
                    if _is_task_xml(item.filename):
                        patched, c = _patch_task_xml(
                            raw,
                            symbol=args.symbol,
                            timeframe=args.timeframe,
                            date_from=args.date_from,
                            date_to=args.date_to,
                        )
                        if c:
                            per_file[item.filename] = dict(c)
                            total_changes.update(c)
                            raw = patched
                            # Validate: after patching, every element with both dateFrom+dateTo
                            # set as real dates (not "0") must satisfy dateFrom <= dateTo
                            try:
                                root = safe_fromstring(patched)
                                for el in root.iter():
                                    df = el.get("dateFrom")
                                    dt = el.get("dateTo")
                                    if not df or not dt or df == "0" or dt == "0":
                                        continue
                                    df_v = _date_parse_any(df)
                                    dt_v = _date_parse_any(dt)
                                    if df_v is not None and dt_v is not None and df_v > dt_v:
                                        inverted_ranges.append({
                                            "file": item.filename,
                                            "element": etree.QName(el.tag).localname,
                                            "dateFrom": df,
                                            "dateTo": dt,
                                        })
                            except etree.XMLSyntaxError:
                                pass
                    patched_files[item.filename] = raw

            if inverted_ranges:
                # Roll back to snapshot if we took one — otherwise just refuse to write
                if snapshot_path is not None:
                    shutil.copy2(snapshot_path, cfx_path)
                return {
                    "ok": False,
                    "error": (
                        f"patch would create {len(inverted_ranges)} inverted date range(s) "
                        "(dateFrom > dateTo). Refusing to write. "
                        + ("Snapshot restored." if snapshot_path else "No snapshot to restore.")
                    ),
                    "inverted_ranges": inverted_ranges[:10],
                    "hint": "patch BOTH date_from and date_to together when shifting windows",
                }

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as dst:
                # preserve original ZipInfo metadata where available
                with zipfile.ZipFile(cfx_path, "r") as src:
                    for item in src.infolist():
                        dst.writestr(item, patched_files[item.filename])
            _atomic_write_bytes(cfx_path, buf.getvalue())
            return {
                "ok": True,
                "project": args.project,
                "cfx_path": str(cfx_path),
                "snapshot_path": str(snapshot_path) if snapshot_path else None,
                "changes": dict(total_changes),
                "per_file": per_file,
                "files_patched": len(per_file),
            }
        except (EngineError, ValidationError, OSError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Atomic loadconfig + syncfromfiles + wait + start. Fixes the race condition "
            "where calling `-project action=start` immediately after `-databank action="
            "syncfromfiles` causes the project to run BEFORE the strategies finish loading "
            "from disk (resulting in 'No strategies to retest/optimize'). This tool sleeps "
            "`sync_wait_seconds` after the sync call, then issues start. Use for Retester "
            "and Optimizer projects where you've just placed .sqx files into the input "
            "databank. Project name MUST be underscore-only — sqcli's parser can't quote "
            "names with spaces."
        )
    )
    async def project_load_and_start(args: ProjectLoadAndStartArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx}"}

            steps: list[dict[str, Any]] = []

            t0 = time.time()
            text = await eng.call(f"-project action=loadconfig name={args.project} file={cfx}")
            steps.append({"step": "loadconfig", "elapsed": round(time.time() - t0, 2), "raw": text.strip()[:200]})
            if "Error" in text:
                return {"ok": False, "error": "loadconfig failed", "steps": steps}

            if args.sync_databanks:
                for db in args.sync_databanks:
                    t0 = time.time()
                    text = await eng.call(
                        f'-databank action=syncfromfiles project={args.project} name={_quote_if_space(db)}'
                    )
                    steps.append({"step": f"syncfromfiles({db})", "elapsed": round(time.time() - t0, 2),
                                  "raw": text.strip()[:200]})

                await ctx.info(f"waiting {args.sync_wait_seconds}s for sync to settle")
                await asyncio.sleep(args.sync_wait_seconds)
                steps.append({"step": "wait", "elapsed": args.sync_wait_seconds})

            t0 = time.time()
            if args.only_task is not None:
                cmd = f"-project action=startOnlyTask name={args.project} task={args.only_task}"
            elif args.from_task is not None:
                cmd = f"-project action=startFromTask name={args.project} task={args.from_task}"
            else:
                cmd = f"-project action=start name={args.project}"
            text = await eng.call(cmd)
            steps.append({"step": "start", "elapsed": round(time.time() - t0, 2), "raw": text.strip()[:200]})

            failed = "Error" in text or "Nothing to" in text or "No strategies" in text
            return {
                "ok": not failed,
                "project": args.project,
                "steps": steps,
                "warning": "engine reported empty input — sync race likely; try larger sync_wait_seconds" if failed else None,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Forcefully remove a project from BOTH the engine's internal state AND the "
            "filesystem, in the correct order. Just rm -rf'ing the directory leaves a "
            "phantom in JVM memory that breaks subsequent reuses of the same name (loadconfig "
            "creates 'NAME(2)' variants and start operates on the wrong instance). This tool "
            "calls `-project action=stop` + `action=remove` BEFORE deleting the directory."
        )
    )
    async def project_force_remove(args: ProjectForceRemoveArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            project_dir = eng.config.projects_dir / args.project
            steps: list[dict[str, Any]] = []

            for action in ("stop", "remove"):
                try:
                    text = await eng.call(f"-project action={action} name={args.project}")
                    steps.append({"action": action, "raw": text.strip()[:200]})
                except EngineError as exc:
                    steps.append({"action": action, "error": str(exc)})

            removed_from_disk = False
            if args.delete_directory and project_dir.exists():
                shutil.rmtree(project_dir)
                removed_from_disk = True
                steps.append({"action": "rm_dir", "path": str(project_dir)})

            return {
                "ok": True,
                "project": args.project,
                "removed_from_disk": removed_from_disk,
                "directory_existed": project_dir.exists() if not removed_from_disk else True,
                "steps": steps,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Block until a project finishes. Combines THREE completion signals so we don't "
            "rely on any single one: (a) `-project action=status` reports finished/idle, "
            "(b) databank strategy count is stable for N consecutive polls, (c) (optional) "
            "log emits an 'All tasks completed' / 'project finished' marker. Returns a "
            "structured report with the last 10 snapshots and final strategy count. "
            "Hard-bounded by max_wait_seconds so the agent never blocks indefinitely."
        )
    )
    async def project_wait_for_completion(
        args: ProjectWaitForCompletionArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            result = await _wait_for_completion(
                eng,
                project=args.project,
                databank=args.databank,
                max_wait_seconds=args.max_wait_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                stable_polls_required=args.stable_polls_required,
                require_log_completion_marker=args.require_log_completion_marker,
                ctx=ctx,
            )
            return {**result, "project": args.project, "databank": args.databank}
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Atomic start-and-wait. Loads the project's .cfx, optionally syncs input "
            "databanks (fixing the syncfromfiles race condition that produces 'No "
            "strategies to retest'), kicks off the project, then blocks until completion. "
            "After done, force-syncs the result databank so all strategies are flushed to "
            "disk (most databanks default to syncType=Auto-sync never, leaving results "
            "JVM-resident and invisible to file scans). Returns a full execution timeline "
            "plus the final databank count. Use this for unattended single-project runs."
        )
    )
    async def project_run_to_completion(
        args: ProjectRunToCompletionArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}

            timeline: list[dict[str, Any]] = []

            t0 = time.time()
            text = await eng.call(
                f"-project action=loadconfig name={args.project} file={cfx_path}",
                timeout=120.0,
            )
            timeline.append({"step": "loadconfig", "elapsed": round(time.time() - t0, 2), "raw": text.strip()[:200]})
            if "Error" in text:
                return {"ok": False, "error": "loadconfig failed", "timeline": timeline}

            # input databank sync if requested
            if args.sync_databanks:
                for db in args.sync_databanks:
                    t = time.time()
                    text = await eng.call(
                        f'-databank action=syncfromfiles project={args.project} name={_quote_if_space(db)}',
                        timeout=300.0,
                    )
                    timeline.append({"step": f"syncfromfiles({db})", "elapsed": round(time.time() - t, 2),
                                     "raw": text.strip()[:200]})
                await ctx.info(f"waiting {args.sync_wait_seconds}s for sync to settle")
                await asyncio.sleep(args.sync_wait_seconds)
                timeline.append({"step": "sync_wait", "elapsed": args.sync_wait_seconds})

            # start
            t = time.time()
            if args.only_task is not None:
                cmd = f"-project action=startOnlyTask name={args.project} task={args.only_task}"
            elif args.from_task is not None:
                cmd = f"-project action=startFromTask name={args.project} task={args.from_task}"
            else:
                cmd = f"-project action=start name={args.project}"
            text = await eng.call(cmd, timeout=60.0)
            timeline.append({"step": "start", "elapsed": round(time.time() - t, 2), "raw": text.strip()[:200]})

            if "Error" in text or "Nothing to" in text or "No strategies" in text:
                return {
                    "ok": False,
                    "project": args.project,
                    "error": f"engine refused start: {text.strip()[:200]}",
                    "timeline": timeline,
                    "hint": "Empty input databank? Try larger sync_wait_seconds or check the input databank.",
                }

            # wait
            wait_result = await _wait_for_completion(
                eng,
                project=args.project,
                databank=args.databank,
                max_wait_seconds=args.max_wait_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                stable_polls_required=args.stable_polls_required,
                require_log_completion_marker=False,
                ctx=ctx,
            )
            timeline.append({"step": "wait", **wait_result})

            # force sync the result databank if requested
            if args.force_sync_final and wait_result.get("completed"):
                t = time.time()
                try:
                    text = await eng.call(
                        f"-databank action=synctofiles project={args.project} name={_quote_if_space(args.databank)}",
                        timeout=300.0,
                    )
                    timeline.append({"step": "synctofiles", "elapsed": round(time.time() - t, 2),
                                     "raw": text.strip()[:200]})
                except EngineError as exc:
                    timeline.append({"step": "synctofiles", "error": str(exc)})

            final_count = _count_sqx(eng.config.projects_dir / args.project / "databanks" / args.databank)
            return {
                "ok": wait_result.get("completed", False),
                "project": args.project,
                "databank": args.databank,
                "completed": wait_result.get("completed", False),
                "timed_out": wait_result.get("timed_out", False),
                "elapsed_seconds": wait_result.get("elapsed_seconds"),
                "final_strategy_count": final_count,
                "timeline": timeline,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "End-to-end Builder → Filter → Retester pipeline. Single autonomous call:\n"
            "  1. (optional) start the Builder project\n"
            "  2. wait for Builder completion + force-sync Results\n"
            "  3. filter Builder survivors by trades / drawdown / profit-to-DD / fitness\n"
            "  4. promote top-N filtered survivors into the Retester's input databank\n"
            "  5. (optional) start the Retester with proper syncfromfiles handling\n"
            "  6. wait for Retester completion + force-sync its Results\n"
            "Returns a full step-by-step timeline plus survivor counts at each phase. "
            "Both projects must exist (use project_create_from_template first if not). "
            "Set start_builder=False to skip phase 1 (use whatever Results already exist). "
            "Set dry_run=True to do filter+promote only, without starting any engine work."
        )
    )
    async def pipeline_build_filter_retest(
        args: PipelineBuildFilterRetestArgs, ctx: Context
    ) -> dict:
        try:
            eng = get_engine(ctx)
            timeline: list[dict[str, Any]] = []
            t_pipeline_start = time.time()

            # Validate both projects exist
            for proj in (args.builder_project, args.retester_project):
                cfx = eng.config.projects_dir / proj / "project.cfx"
                if not cfx.is_file():
                    return {
                        "ok": False,
                        "error": f"project.cfx not found for {proj!r}: {cfx}",
                        "hint": "Create the project (e.g. via project_create_from_template).",
                    }

            # ---- Phase 1: Builder ----
            if args.start_builder and not args.dry_run:
                await ctx.info(f"pipeline phase 1/4: starting Builder {args.builder_project!r}")
                t = time.time()
                builder_load = await eng.call(
                    f"-project action=loadconfig name={args.builder_project} "
                    f"file={eng.config.projects_dir / args.builder_project / 'project.cfx'}",
                    timeout=120.0,
                )
                timeline.append({"phase": "builder_load", "elapsed": round(time.time() - t, 2),
                                 "raw": builder_load.strip()[:200]})
                t = time.time()
                builder_start = await eng.call(
                    f"-project action=start name={args.builder_project}", timeout=60.0
                )
                timeline.append({"phase": "builder_start", "elapsed": round(time.time() - t, 2),
                                 "raw": builder_start.strip()[:200]})

                wait = await _wait_for_completion(
                    eng,
                    project=args.builder_project,
                    databank="Results",
                    max_wait_seconds=args.max_wait_builder_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                    stable_polls_required=args.stable_polls_required,
                    require_log_completion_marker=False,
                    ctx=ctx,
                )
                timeline.append({"phase": "builder_wait", **wait})
                if not wait.get("completed"):
                    return {
                        "ok": False,
                        "phase_failed": "builder_wait",
                        "error": "Builder did not complete within max_wait_builder_seconds.",
                        "timeline": timeline,
                    }

                # force-sync Builder Results
                t = time.time()
                try:
                    sync_text = await eng.call(
                        f"-databank action=synctofiles project={args.builder_project} name=Results",
                        timeout=300.0,
                    )
                    timeline.append({"phase": "builder_synctofiles", "elapsed": round(time.time() - t, 2),
                                     "raw": sync_text.strip()[:200]})
                except EngineError as exc:
                    timeline.append({"phase": "builder_synctofiles", "error": str(exc)})

            # ---- Phase 2: Filter & rank ----
            await ctx.info("pipeline phase 2/4: filtering Builder Results")
            from sq_mcp.parsers.sqx import derive_metrics, parse_sqx
            builder_results_dir = (
                eng.config.projects_dir / args.builder_project / "databanks" / "Results"
            )
            if not builder_results_dir.exists():
                return {
                    "ok": False,
                    "phase_failed": "filter",
                    "error": f"Builder Results dir missing: {builder_results_dir}",
                    "timeline": timeline,
                }

            candidates: list[dict[str, Any]] = []
            scanned = 0
            for sqx_path in sorted(builder_results_dir.rglob("*.sqx")):
                scanned += 1
                try:
                    info = parse_sqx(sqx_path)
                    m = derive_metrics(info)
                except (ValueError, OSError):
                    continue
                if args.builder_filter_min_trades is not None and (m.get("trades") or 0) < args.builder_filter_min_trades:
                    continue
                if args.builder_filter_max_drawdown_pct is not None:
                    dd = m.get("drawdown_pct")
                    if dd is None or dd > args.builder_filter_max_drawdown_pct:
                        continue
                if args.builder_filter_min_profit_to_dd is not None:
                    r = m.get("profit_to_dd_ratio")
                    if r is None or r < args.builder_filter_min_profit_to_dd:
                        continue
                if args.builder_filter_min_fitness_is is not None:
                    f = m.get("fitness_is")
                    if f is None or f < args.builder_filter_min_fitness_is:
                        continue
                candidates.append({"path": sqx_path, "metrics": m,
                                   "hash": (info.fingerprint.trades_hash if info.fingerprint else None)})

            def _sort_key(c: dict) -> float:
                v = c["metrics"].get(args.builder_sort_by)
                return v if isinstance(v, (int, float)) else float("-inf")

            candidates.sort(key=_sort_key, reverse=True)
            picks = candidates[: args.top_n]
            timeline.append({
                "phase": "filter",
                "scanned": scanned,
                "survivors": len(candidates),
                "promoted": len(picks),
                "sort_by": args.builder_sort_by,
            })

            if not picks:
                return {
                    "ok": False,
                    "phase_failed": "filter",
                    "error": "No strategies passed the filter criteria.",
                    "scanned": scanned,
                    "timeline": timeline,
                    "hint": "Relax the filter thresholds (e.g. lower builder_filter_min_profit_to_dd).",
                }

            # ---- Phase 3: Promote ----
            retester_input_dir = (
                eng.config.projects_dir / args.retester_project / "databanks" / args.retester_input_databank
            )
            retester_input_dir.mkdir(parents=True, exist_ok=True)
            existing_hashes = _existing_dest_hashes(retester_input_dir)
            promoted: list[dict[str, Any]] = []
            for pick in picks:
                if pick["hash"] and pick["hash"] in existing_hashes:
                    continue
                src_p: Path = pick["path"]
                dst_p = retester_input_dir / src_p.name
                if dst_p.exists():
                    stem, suffix = dst_p.stem, dst_p.suffix
                    n = 2
                    while (retester_input_dir / f"{stem}_v{n}{suffix}").exists():
                        n += 1
                    dst_p = retester_input_dir / f"{stem}_v{n}{suffix}"
                if not args.dry_run:
                    shutil.copy2(src_p, dst_p)
                promoted.append({"src": str(src_p), "dest": str(dst_p),
                                 "metrics": pick["metrics"], "hash": pick["hash"]})
                if pick["hash"]:
                    existing_hashes.add(pick["hash"])
            timeline.append({"phase": "promote", "count": len(promoted)})

            # ---- Phase 4: Retester ----
            if args.start_retester and not args.dry_run:
                await ctx.info(f"pipeline phase 4/4: starting Retester {args.retester_project!r}")
                t = time.time()
                retester_load = await eng.call(
                    f"-project action=loadconfig name={args.retester_project} "
                    f"file={eng.config.projects_dir / args.retester_project / 'project.cfx'}",
                    timeout=120.0,
                )
                timeline.append({"phase": "retester_load", "elapsed": round(time.time() - t, 2),
                                 "raw": retester_load.strip()[:200]})

                t = time.time()
                try:
                    sync_text = await eng.call(
                        f'-databank action=syncfromfiles project={args.retester_project} '
                        f'name={_quote_if_space(args.retester_input_databank)}',
                        timeout=300.0,
                    )
                    timeline.append({"phase": "retester_syncfromfiles", "elapsed": round(time.time() - t, 2),
                                     "raw": sync_text.strip()[:200]})
                except EngineError as exc:
                    timeline.append({"phase": "retester_syncfromfiles", "error": str(exc)})

                await ctx.info(f"waiting {args.sync_wait_seconds}s for retester sync")
                await asyncio.sleep(args.sync_wait_seconds)

                t = time.time()
                retester_start = await eng.call(
                    f"-project action=start name={args.retester_project}", timeout=60.0
                )
                timeline.append({"phase": "retester_start", "elapsed": round(time.time() - t, 2),
                                 "raw": retester_start.strip()[:200]})
                if "Error" in retester_start or "No strategies" in retester_start:
                    return {
                        "ok": False,
                        "phase_failed": "retester_start",
                        "error": f"Retester refused start: {retester_start.strip()[:200]}",
                        "timeline": timeline,
                    }

                wait = await _wait_for_completion(
                    eng,
                    project=args.retester_project,
                    databank="Results",
                    max_wait_seconds=args.max_wait_retester_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                    stable_polls_required=args.stable_polls_required,
                    require_log_completion_marker=False,
                    ctx=ctx,
                )
                timeline.append({"phase": "retester_wait", **wait})
                if not wait.get("completed"):
                    return {
                        "ok": False,
                        "phase_failed": "retester_wait",
                        "error": "Retester did not complete within max_wait_retester_seconds.",
                        "timeline": timeline,
                    }

                # final sync
                try:
                    sync_text = await eng.call(
                        f"-databank action=synctofiles project={args.retester_project} name=Results",
                        timeout=300.0,
                    )
                    timeline.append({"phase": "retester_synctofiles", "raw": sync_text.strip()[:200]})
                except EngineError as exc:
                    timeline.append({"phase": "retester_synctofiles", "error": str(exc)})

            retester_results_dir = (
                eng.config.projects_dir / args.retester_project / "databanks" / "Results"
            )
            retester_final_count = _count_sqx(retester_results_dir)

            return {
                "ok": True,
                "builder_project": args.builder_project,
                "retester_project": args.retester_project,
                "builder_scanned": scanned,
                "filter_survivors": len(candidates),
                "promoted_to_retester": len(promoted),
                "retester_final_count": retester_final_count,
                "elapsed_seconds": round(time.time() - t_pipeline_start, 2),
                "promoted_paths": [p["dest"] for p in promoted],
                "dry_run": args.dry_run,
                "timeline": timeline,
            }
        except (EngineError, ValidationError, OSError) as exc:
            return safe_error_payload(exc)


    @mcp.tool(
        description=(
            "Query sqcli's built-in help. Omit `command` for the top-level command "
            "list; pass `command='project'` (etc.) for action/parameter details. The "
            "engine truncates large help text at ~1KB and appends '(N more lines, M "
            "bytes total)' — we surface that footer so the caller knows the response "
            "is partial. Useful for discovering what's actually supported in the "
            "running build before issuing a command."
        )
    )
    async def engine_help(args: EngineHelpArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            if args.command:
                cmd = f"-h -{args.command}"
            else:
                cmd = "-h"
            text = await eng.call(cmd, timeout=15.0)
            stripped = text.strip()
            truncated_marker = None
            m = re.search(r"\((\d+) more lines?,\s*(\d+) bytes total\)", stripped)
            if m:
                truncated_marker = {
                    "more_lines": int(m.group(1)),
                    "total_bytes": int(m.group(2)),
                }
            return {
                "ok": True,
                "command": args.command,
                "raw": stripped,
                "lines": parse_list(stripped),
                "truncated": truncated_marker is not None,
                "truncated_marker": truncated_marker,
            }
        except (EngineError, ValidationError) as exc:
            return safe_error_payload(exc)


def _existing_dest_hashes(dest_dir: Path) -> set[str]:
    """Best-effort hash dedupe set for the pipeline (avoids re-import on rerun).

    Defensive: silently degrades to an empty set on permission/IO errors so a
    single corrupt file doesn't break a multi-hour pipeline call.
    """
    from sq_mcp.parsers.sqx import parse_sqx
    out: set[str] = set()
    if not dest_dir.exists():
        return out
    try:
        paths = list(dest_dir.rglob("*.sqx"))
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
