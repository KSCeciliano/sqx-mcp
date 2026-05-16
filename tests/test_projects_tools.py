"""Unit tests for the new pure-Python helpers in tools/projects.py and tools/_common.py.

These exercise the clone / registry / fallback logic in isolation (no engine needed).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from sq_mcp.tools import _common as common_mod
from sq_mcp.tools.databanks import _extract_sqx_contents, _scan_databanks_fs
from sq_mcp.tools.projects import (
    _clone_cfx_zip,
    _date_to_epoch_ms,
    _extract_broker_entries,
    _find_template_source,
    _is_task_xml,
    _looks_like_tf,
    _make_snapshot,
    _parse_progress_from_log,
    _patch_config_xml,
    _patch_task_xml,
    _scan_projects_fs,
    _validate_cfx_structure,
)
from sq_mcp.tools.symbols import (
    _parse_dat_header,
    _scan_symbols_fs,
    _summarize_dat,
    _summarize_symbol_dir,
)

# ---- pure helpers ---------------------------------------------------------


def test_date_to_epoch_ms_known_values():
    # 2023-01-01 UTC == 1672531200000 (confirmed against epochconverter)
    assert _date_to_epoch_ms("2023.01.01") == 1672531200000
    # 1970-01-01 epoch zero
    assert _date_to_epoch_ms("1970.01.01") == 0


def test_looks_like_tf():
    assert _looks_like_tf("M1")
    assert _looks_like_tf("H1")
    assert _looks_like_tf("D1")
    assert _looks_like_tf("TICK")
    assert not _looks_like_tf("true")
    assert not _looks_like_tf("garbage")


def test_is_task_xml():
    assert _is_task_xml("Build-Task1.xml")
    assert _is_task_xml("Retest-Task2.xml")
    assert _is_task_xml("Optimize-Task1.xml")
    assert _is_task_xml("WalkForward-Task5.xml")
    assert not _is_task_xml("config.xml")
    assert not _is_task_xml("ClearDatabanks-Task8.xml")
    assert not _is_task_xml("Build-Task1.txt")


# ---- XML patching ---------------------------------------------------------


_BUILD_TASK_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Build>
  <Setup>
    <Chart symbol="GBPJPY_M1_dukas" timeframe="H1" spread="1.6"/>
  </Setup>
  <Sample dateFrom="2009.01.01" dateTo="2022.08.31"/>
  <DataPart dateFrom="1230768000000" dateTo="1661904000000" timeframe="true"/>
  <Other dateFrom="0" dateTo="0"/>
  <Symbol name="GBPJPY_M1_dukas" source="2" barType="1" precision="M1">
    <InstrumentInfo instrument="GBPJPY_dukascopy" broker="3" dataType="3" exchange="Forex"/>
  </Symbol>
</Build>
"""


def test_patch_task_xml_rewrites_all_targets():
    out, changes = _patch_task_xml(
        _BUILD_TASK_XML,
        symbol="BTCUSDT",
        timeframe="H4",
        date_from="2023.01.01",
        date_to="2025.12.31",
    )
    root = etree.fromstring(out)
    # Chart.symbol became BTCUSDT
    chart = root.find(".//Chart")
    assert chart.get("symbol") == "BTCUSDT"
    assert chart.get("timeframe") == "H4"
    # Sample (string-form dates) updated
    sample = root.find("Sample")
    assert sample.get("dateFrom") == "2023.01.01"
    assert sample.get("dateTo") == "2025.12.31"
    # DataPart (epoch-form) updated to epoch
    dp = root.find("DataPart")
    assert dp.get("dateFrom") == str(_date_to_epoch_ms("2023.01.01"))
    assert dp.get("dateTo") == str(_date_to_epoch_ms("2025.12.31"))
    # timeframe="true" boolean is preserved (not a real TF)
    assert dp.get("timeframe") == "true"
    # zero-valued dates left alone
    other = root.find("Other")
    assert other.get("dateFrom") == "0"
    assert other.get("dateTo") == "0"
    # Symbol.name treated as a symbol attr (no — it's `name`, the patcher only rewrites `symbol`)
    # but Symbol still has source/barType intact
    sym = root.find("Symbol")
    assert sym.get("name") == "GBPJPY_M1_dukas"
    assert sym.get("source") == "2"

    # counts: Chart.symbol (1), Chart.timeframe (1), Sample.dateFrom/To (2),
    # DataPart.dateFrom/To (2). Other was 0-valued so skipped.
    assert changes["symbol"] == 1
    assert changes["timeframe"] == 1
    assert changes["dateFrom"] == 2
    assert changes["dateTo"] == 2


def test_patch_task_xml_partial_args():
    """Only patch what was passed; leave others untouched."""
    out, changes = _patch_task_xml(
        _BUILD_TASK_XML, symbol=None, timeframe=None, date_from="2024.06.01", date_to=None
    )
    root = etree.fromstring(out)
    assert root.find(".//Chart").get("symbol") == "GBPJPY_M1_dukas"  # unchanged
    assert root.find("Sample").get("dateFrom") == "2024.06.01"  # changed
    assert root.find("Sample").get("dateTo") == "2022.08.31"  # unchanged
    assert "symbol" not in changes
    assert changes["dateFrom"] == 2  # Sample + DataPart both have dateFrom


def test_patch_config_xml_renames_project():
    cfg = b'<?xml version="1.0"?><Project name="OldName" version="143.0"></Project>'
    out = _patch_config_xml(cfg, "NewName")
    root = etree.fromstring(out)
    assert root.get("name") == "NewName"
    assert root.get("version") == "143.0"


# ---- in-memory cfx clone --------------------------------------------------


def _make_cfx(tmp_path: Path) -> Path:
    cfx = tmp_path / "source.cfx"
    with zipfile.ZipFile(cfx, "w") as z:
        z.writestr(
            "config.xml",
            b'<?xml version="1.0"?><Project name="Source" version="143.0">'
            b'<Tasks><Task type="Build" name="b" active="true" taskXMLFile="Build-Task1.xml"/></Tasks>'
            b'</Project>',
        )
        z.writestr("Build-Task1.xml", _BUILD_TASK_XML)
        z.writestr("README.txt", b"unrelated payload, must be copied as-is")
    return cfx


def test_clone_cfx_zip_rewrites_and_renames(tmp_path):
    src = _make_cfx(tmp_path)
    dst = tmp_path / "dest.cfx"
    report = _clone_cfx_zip(
        src, dst, new_name="Cloned", symbol="BTCUSDT", timeframe="H1",
        date_from="2023.01.01", date_to=None,
    )
    assert dst.exists()
    with zipfile.ZipFile(dst) as z:
        # README.txt copied verbatim
        assert z.read("README.txt") == b"unrelated payload, must be copied as-is"
        # config.xml renamed
        cfg = etree.fromstring(z.read("config.xml"))
        assert cfg.get("name") == "Cloned"
        # Build task patched
        task = etree.fromstring(z.read("Build-Task1.xml"))
        assert task.find(".//Chart").get("symbol") == "BTCUSDT"
        assert task.find("Sample").get("dateFrom") == "2023.01.01"
    assert report["total_changes"]["symbol"] >= 1
    assert "Build-Task1.xml" in report["per_file"]


def test_clone_cfx_zip_no_rewrites_just_renames(tmp_path):
    """With no symbol/tf/date args, only config.xml's project name changes."""
    src = _make_cfx(tmp_path)
    dst = tmp_path / "dest.cfx"
    report = _clone_cfx_zip(
        src, dst, new_name="Cloned", symbol=None, timeframe=None,
        date_from=None, date_to=None,
    )
    assert report["total_changes"] == {}
    assert report["per_file"] == {}
    with zipfile.ZipFile(dst) as z:
        cfg = etree.fromstring(z.read("config.xml"))
        assert cfg.get("name") == "Cloned"
        # task XML should be byte-identical to source
        assert z.read("Build-Task1.xml") == _BUILD_TASK_XML


# ---- broker registry extraction -------------------------------------------


def test_extract_broker_entries(tmp_path):
    cfx = _make_cfx(tmp_path)
    rows = _extract_broker_entries(cfx, "TestProj")
    kinds = {r["kind"] for r in rows}
    assert "data_symbol" in kinds  # from <Symbol name=...>
    assert "instrument" in kinds  # from <InstrumentInfo instrument=...>
    sym = next(r for r in rows if r["kind"] == "data_symbol")
    assert sym["key"] == "GBPJPY_M1_dukas"
    assert sym["source"] == "2"
    assert sym["barType"] == "1"
    inst = next(r for r in rows if r["kind"] == "instrument")
    assert inst["key"] == "GBPJPY_dukascopy"
    assert inst["broker"] == "3"
    assert inst["dataType"] == "3"


def test_extract_broker_entries_handles_bad_zip(tmp_path):
    bad = tmp_path / "broken.cfx"
    bad.write_bytes(b"not a zip")
    assert _extract_broker_entries(bad, "x") == []


# ---- fs scanners ----------------------------------------------------------


def test_scan_projects_fs(tmp_path):
    (tmp_path / "ProjA").mkdir()
    (tmp_path / "ProjA" / "project.cfx").write_bytes(b"x")
    (tmp_path / "ProjB").mkdir()
    (tmp_path / "ProjB" / "project.cfx").write_bytes(b"y" * 100)
    (tmp_path / "OrphanDir").mkdir()  # no cfx — should be skipped
    (tmp_path / "lonefile.txt").write_text("noise")
    out = _scan_projects_fs(tmp_path)
    names = [p["name"] for p in out]
    assert names == ["ProjA", "ProjB"]
    assert out[1]["cfx_size"] == 100


def test_scan_projects_fs_missing_root(tmp_path):
    assert _scan_projects_fs(tmp_path / "does_not_exist") == []


def test_scan_symbols_fs(tmp_path):
    eurusd = tmp_path / "EURUSD_M1_dukas"
    eurusd.mkdir()
    (eurusd / "EURUSD_M1_dukas.dat").write_bytes(b"x" * 50)
    (eurusd / "EURUSD_H1_dukas.dat").write_bytes(b"y" * 30)
    btc = tmp_path / "BTCUSDT"
    btc.mkdir()
    (btc / "BTCUSDT_M1_binance.dat").write_bytes(b"z" * 100)
    empty = tmp_path / "Empty"  # no .dat — skipped
    empty.mkdir()
    out = _scan_symbols_fs(tmp_path)
    syms = {s["symbol"] for s in out}
    assert syms == {"EURUSD_M1_dukas", "BTCUSDT"}
    btc_entry = next(s for s in out if s["symbol"] == "BTCUSDT")
    assert btc_entry["dat_files"] == 1
    assert btc_entry["total_bytes"] == 100


def test_scan_databanks_fs(tmp_path):
    pdir = tmp_path / "MyProj"
    db_root = pdir / "databanks"
    (db_root / "Results").mkdir(parents=True)
    (db_root / "Results" / "strat1.sqx").write_bytes(b"x")
    (db_root / "Final").mkdir()
    out = _scan_databanks_fs(pdir)
    names = {d["name"] for d in out}
    assert names == {"Results", "Final"}
    res = next(d for d in out if d["name"] == "Results")
    assert res["sqx_count"] == 1


# ---- status-only response detection ---------------------------------------


def test_is_status_only_response():
    assert common_mod.is_status_only_response("18:16:00 List of available databanks\nDatabanks listed.")
    assert common_mod.is_status_only_response("Data listed.")
    assert common_mod.is_status_only_response("Symbols listed.")
    # actual data should NOT be flagged
    assert not common_mod.is_status_only_response("EURUSD\nGBPJPY\nBTCUSDT")
    assert not common_mod.is_status_only_response("")  # empty is not "status-only" — separate concern
    # mixed: header + real data → not status-only (some lines aren't markers)
    assert not common_mod.is_status_only_response(
        "18:16:00 List of available databanks\nEURUSD\nBTCUSDT\nDatabanks listed."
    )


def test_parse_listing_response_detects_error():
    r = common_mod.parse_listing_response("Error: Not implemented.", "projects")
    assert r["ok"] is False
    assert r["errors"] == ["Error:"]
    assert r["projects"] == []


def test_parse_listing_response_happy_path():
    r = common_mod.parse_listing_response("ProjA\nProjB\nProjC", "projects")
    assert r["ok"] is True
    assert r["projects"] == ["ProjA", "ProjB", "ProjC"]


# ---- cfx structural validation --------------------------------------------


def test_validate_cfx_structure_happy(tmp_path):
    cfx = _make_cfx(tmp_path)
    r = _validate_cfx_structure(cfx)
    assert r["ok"] is True
    assert r["project_name"] == "Source"
    assert r["version"] == "143.0"
    assert len(r["tasks"]) == 1
    assert r["tasks"][0]["xml_file"] == "Build-Task1.xml"
    assert r["tasks"][0]["exists"] is True
    assert r["missing_files"] == []
    assert r["orphan_task_xmls"] == []
    assert r["task_xml_parse_errors"] == []
    assert r["issues"] == []


def test_validate_cfx_structure_missing_task_xml(tmp_path):
    cfx = tmp_path / "broken.cfx"
    with zipfile.ZipFile(cfx, "w") as z:
        z.writestr(
            "config.xml",
            b'<?xml version="1.0"?><Project name="X">'
            b'<Tasks><Task type="Build" name="b" active="true" taskXMLFile="Build-Task1.xml"/></Tasks>'
            b'</Project>',
        )
    r = _validate_cfx_structure(cfx)
    assert r["ok"] is False
    assert "Build-Task1.xml" in r["missing_files"]
    assert any("missing file" in i for i in r["issues"])


def test_validate_cfx_structure_orphan_xml(tmp_path):
    cfx = tmp_path / "orphan.cfx"
    with zipfile.ZipFile(cfx, "w") as z:
        z.writestr(
            "config.xml",
            b'<?xml version="1.0"?><Project name="X"><Tasks/></Project>',
        )
        z.writestr("Build-OrphanTask.xml", b'<?xml version="1.0"?><Build/>')
    r = _validate_cfx_structure(cfx)
    assert r["ok"] is False
    assert "Build-OrphanTask.xml" in r["orphan_task_xmls"]


def test_validate_cfx_structure_missing_config(tmp_path):
    cfx = tmp_path / "nocfg.cfx"
    with zipfile.ZipFile(cfx, "w") as z:
        z.writestr("README.txt", b"no config here")
    r = _validate_cfx_structure(cfx)
    assert r["ok"] is False
    assert "missing config.xml" in r["error"]


def test_validate_cfx_structure_bad_zip(tmp_path):
    bad = tmp_path / "broken.cfx"
    bad.write_bytes(b"not a zip")
    r = _validate_cfx_structure(bad)
    assert r["ok"] is False
    assert "not a valid zip" in r["error"]


def test_validate_cfx_structure_malformed_task_xml(tmp_path):
    cfx = tmp_path / "bad_xml.cfx"
    with zipfile.ZipFile(cfx, "w") as z:
        z.writestr(
            "config.xml",
            b'<?xml version="1.0"?><Project name="X">'
            b'<Tasks><Task type="Build" name="b" active="true" taskXMLFile="Build-Task1.xml"/></Tasks>'
            b'</Project>',
        )
        z.writestr("Build-Task1.xml", b"<not valid xml")
    r = _validate_cfx_structure(cfx)
    assert r["ok"] is False
    assert r["task_xml_parse_errors"]
    assert r["task_xml_parse_errors"][0]["file"] == "Build-Task1.xml"


# ---- snapshot --------------------------------------------------------------


def test_make_snapshot_creates_backup_with_label(tmp_path):
    cfx = tmp_path / "project.cfx"
    cfx.write_bytes(b"PAYLOAD")
    dst = _make_snapshot(cfx, label="before-clone")
    assert dst.exists()
    assert dst != cfx
    assert dst.read_bytes() == b"PAYLOAD"
    assert ".bak." in dst.name
    assert dst.name.endswith(".before-clone")


def test_make_snapshot_sanitizes_label(tmp_path):
    cfx = tmp_path / "project.cfx"
    cfx.write_bytes(b"X")
    dst = _make_snapshot(cfx, label="bad/label with spaces*")
    # only safe chars survive
    assert "/" not in dst.name
    assert "*" not in dst.name
    assert " " not in dst.name


def test_make_snapshot_no_label(tmp_path):
    cfx = tmp_path / "p.cfx"
    cfx.write_bytes(b"X")
    dst = _make_snapshot(cfx)
    assert ".bak." in dst.name
    # 4-digit year, T separator, Z trailer
    assert dst.name.endswith("Z")


# ---- template source lookup -----------------------------------------------


def test_find_template_source_by_instrument(tmp_path):
    # Set up two projects, only one has the target instrument
    p1 = tmp_path / "Proj1"
    p2 = tmp_path / "Proj2"
    p1.mkdir()
    p2.mkdir()
    (p1 / "project.cfx").write_bytes(_make_cfx_with_inst(b"FOO_dukascopy", b"FOO_M1_dukas"))
    (p2 / "project.cfx").write_bytes(_make_cfx_with_inst(b"GBPJPY_dukascopy", b"GBPJPY_M1_dukas"))
    best, candidates = _find_template_source(
        tmp_path, instrument="GBPJPY_dukascopy", data_symbol=None, prefer_source=None
    )
    assert best is not None
    assert best.parent.name == "Proj2"
    assert len(candidates) == 1


def test_find_template_source_by_data_symbol(tmp_path):
    p1 = tmp_path / "ProjA"
    p1.mkdir()
    (p1 / "project.cfx").write_bytes(_make_cfx_with_inst(b"GBPJPY_dukascopy", b"GBPJPY_M1_dukas"))
    best, candidates = _find_template_source(
        tmp_path, instrument=None, data_symbol="GBPJPY_M1_dukas", prefer_source=None
    )
    assert best is not None
    assert best.parent.name == "ProjA"


def test_find_template_source_prefer_source_wins(tmp_path):
    # Two projects both match; prefer_source forces a non-newest pick
    p_old = tmp_path / "Older"
    p_new = tmp_path / "Newer"
    p_old.mkdir()
    p_new.mkdir()
    (p_old / "project.cfx").write_bytes(
        _make_cfx_with_inst(b"BTCUSDT_binance", b"BTCUSDT_M1_binance")
    )
    (p_new / "project.cfx").write_bytes(
        _make_cfx_with_inst(b"BTCUSDT_binance", b"BTCUSDT_M1_binance")
    )
    best, candidates = _find_template_source(
        tmp_path,
        instrument="BTCUSDT_binance",
        data_symbol=None,
        prefer_source="Older",
    )
    assert best is not None
    assert best.parent.name == "Older"
    assert len(candidates) == 2


def test_find_template_source_no_match(tmp_path):
    p = tmp_path / "Proj"
    p.mkdir()
    (p / "project.cfx").write_bytes(_make_cfx_with_inst(b"FOO_x", b"FOO_M1"))
    best, candidates = _find_template_source(
        tmp_path, instrument="DOES_NOT_EXIST", data_symbol=None, prefer_source=None
    )
    assert best is None
    assert candidates == []


def test_find_template_source_missing_root(tmp_path):
    best, candidates = _find_template_source(
        tmp_path / "nope", instrument="X", data_symbol=None, prefer_source=None
    )
    assert best is None
    assert candidates == []


def _make_cfx_with_inst(instrument: bytes, data_symbol: bytes) -> bytes:
    """Build a minimal cfx blob with a single Build task referencing instrument + data symbol."""
    import io as _io
    buf = _io.BytesIO()
    task = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<Build><Setup>'
        b'<Chart symbol="' + data_symbol + b'" timeframe="H1"/>'
        b'</Setup>'
        b'<Symbol name="' + data_symbol + b'" source="2" barType="1">'
        b'<InstrumentInfo instrument="' + instrument + b'" broker="3" dataType="3"/>'
        b'</Symbol></Build>'
    )
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "config.xml",
            b'<?xml version="1.0"?><Project name="T" version="143.0">'
            b'<Tasks><Task type="Build" name="b" active="true" taskXMLFile="Build-Task1.xml"/></Tasks>'
            b'</Project>',
        )
        z.writestr("Build-Task1.xml", task)
    return buf.getvalue()


# ---- engine log progress parsing ------------------------------------------


def test_parse_progress_log_built_strategies():
    lines = ["18:30:00 Built 1234 strategies"]
    r = _parse_progress_from_log(lines)
    assert r["event_count"] == 1
    assert r["latest"]["built"]["data"]["count"] == "1234"
    assert r["latest"]["built"]["timestamp"] == "18:30:00"


def test_parse_progress_log_generation_and_backtest():
    lines = [
        "Generation 5/100",
        "Backtest 250 / 500",
    ]
    r = _parse_progress_from_log(lines)
    assert r["latest"]["generation"]["data"] == {"current": "5", "total": "100"}
    assert r["latest"]["backtest"]["data"] == {"current": "250", "total": "500"}


def test_parse_progress_log_best_fitness_and_pct():
    lines = [
        "Best fitness: 1.42",
        "Progress: 75.5 %",
        "Speed: 12.3 strategies/sec",
    ]
    r = _parse_progress_from_log(lines)
    assert r["latest"]["best_fitness"]["data"]["value"] == "1.42"
    assert r["latest"]["progress_pct"]["data"]["pct"] == "75.5"
    assert r["latest"]["speed"]["data"]["rate"] == "12.3"


def test_parse_progress_log_project_filter():
    lines = [
        "Project Builder started",
        "Project OtherProj started",
        "Built 50 strategies",  # no project mention
    ]
    r = _parse_progress_from_log(lines, project="Builder")
    project_events = [e for e in r["events"] if e["kind"] == "project_event"]
    # Only Builder's project_event should pass the filter
    assert any(e["data"]["project"] == "Builder" for e in project_events)
    assert not any(e["data"]["project"] == "OtherProj" for e in project_events)


def test_parse_progress_log_empty_input():
    r = _parse_progress_from_log([])
    assert r["events"] == []
    assert r["latest"] == {}
    assert r["event_count"] == 0


def test_parse_progress_log_tasks_completed():
    r = _parse_progress_from_log(["All tasks completed"])
    assert r["event_count"] == 1
    assert "tasks_completed" in r["latest"]


# ---- sqx extraction -------------------------------------------------------


def test_extract_sqx_contents_happy(tmp_path):
    sqx = tmp_path / "strat.sqx"
    with zipfile.ZipFile(sqx, "w") as z:
        z.writestr("settings.xml", b"<settings/>")
        z.writestr("version.txt", b"143.0\n")
        z.writestr("orders.bin", b"\x00\x01\x02\x03" * 100)
    r = _extract_sqx_contents(
        sqx, include_xml=True, include_text=True, include_bin_listing=True, max_text_size=10_000
    )
    assert r["ok"] is True
    text_names = {t["name"] for t in r["text_files"]}
    assert text_names == {"settings.xml", "version.txt"}
    bin_names = {b["name"] for b in r["binary_files"]}
    assert bin_names == {"orders.bin"}


def test_extract_sqx_contents_truncates(tmp_path):
    sqx = tmp_path / "big.sqx"
    big_payload = b"A" * 50_000
    with zipfile.ZipFile(sqx, "w") as z:
        z.writestr("settings.xml", big_payload)
    r = _extract_sqx_contents(
        sqx, include_xml=True, include_text=False, include_bin_listing=False, max_text_size=1000
    )
    assert r["ok"] is True
    entry = r["text_files"][0]
    assert entry["truncated"] is True
    assert len(entry["content"]) == 1000
    assert entry["size"] == 50_000


def test_extract_sqx_contents_bad_zip(tmp_path):
    bad = tmp_path / "junk.sqx"
    bad.write_bytes(b"definitely not a zip")
    r = _extract_sqx_contents(
        bad, include_xml=True, include_text=True, include_bin_listing=True, max_text_size=10_000
    )
    assert r["ok"] is False
    assert "not a valid" in r["error"]


def test_extract_sqx_contents_xml_only(tmp_path):
    sqx = tmp_path / "s.sqx"
    with zipfile.ZipFile(sqx, "w") as z:
        z.writestr("settings.xml", b"<s/>")
        z.writestr("version.txt", b"x")
    r = _extract_sqx_contents(
        sqx,
        include_xml=True,
        include_text=False,
        include_bin_listing=False,
        max_text_size=1000,
    )
    text_names = {t["name"] for t in r["text_files"]}
    assert text_names == {"settings.xml"}


# ---- history .dat header ---------------------------------------------------


def test_parse_dat_header_writeutf_strings(tmp_path):
    # Simulate Java DataOutputStream: 2-byte length + UTF-8 bytes.
    dat = tmp_path / "fake.dat"
    payload = (
        (3).to_bytes(2, "big") + b"4.2" +  # version field
        (1).to_bytes(2, "big") + b"D" +
        (8).to_bytes(2, "big") + b"ABCDEFGH" +
        b"\xff" * 100  # garbage so the parser stops
    )
    dat.write_bytes(payload)
    h = _parse_dat_header(dat)
    assert h["strings"][:3] == ["4.2", "D", "ABCDEFGH"]
    assert h["header_bytes"] == 2 + 3 + 2 + 1 + 2 + 8
    assert h["leading_hex"].startswith("00033" + "4")  # 0x00 0x03 '4' ('4'=0x34)


def test_parse_dat_header_stops_at_garbage(tmp_path):
    dat = tmp_path / "g.dat"
    dat.write_bytes(b"\xff" * 200)  # high bytes → big "length" → fails on too-long
    h = _parse_dat_header(dat)
    assert h["strings"] == []


def test_parse_dat_header_handles_missing_file(tmp_path):
    h = _parse_dat_header(tmp_path / "does_not_exist.dat")
    assert "error" in h


def test_summarize_dat_returns_full_info(tmp_path):
    dat = tmp_path / "x.dat"
    dat.write_bytes(b"X" * 1000)
    s = _summarize_dat(dat)
    assert s["name"] == "x.dat"
    assert s["size_bytes"] == 1000
    assert s["estimated_bar_count"] >= 1


def test_summarize_symbol_dir(tmp_path):
    sym = tmp_path / "EURUSD"
    sym.mkdir()
    (sym / "EURUSD_M1.dat").write_bytes(b"A" * 200)
    (sym / "EURUSD_H1.dat").write_bytes(b"B" * 100)
    r = _summarize_symbol_dir(sym)
    assert r["symbol"] == "EURUSD"
    assert r["dat_count"] == 2
    assert r["total_bytes"] == 300
    assert {f["name"] for f in r["files"]} == {"EURUSD_M1.dat", "EURUSD_H1.dat"}
