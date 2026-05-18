"""Unit tests for broker_data_integrity helpers."""

from __future__ import annotations

from pathlib import Path

from sq_mcp.tools.integrity import (
    _compare_indexes,
    _index_disk_files,
    _index_registry_rows,
)

# ---- _index_disk_files ----------------------------------------------------


def test_index_disk_files_handles_three_part_filenames(tmp_path: Path) -> None:
    # symbol_TF_source.dat
    btc = tmp_path / "BTCUSDT"
    btc.mkdir()
    (btc / "BTCUSDT_M1_binance.dat").write_bytes(b"x" * 128)
    (btc / "BTCUSDT_H1_binance.dat").write_bytes(b"x" * 128)
    out = _index_disk_files(tmp_path)
    assert ("BTCUSDT", "M1") in out
    assert ("BTCUSDT", "H1") in out
    assert len(out[("BTCUSDT", "M1")]) == 1


def test_index_disk_files_handles_two_part_filenames(tmp_path: Path) -> None:
    # symbol_TF.dat (no source suffix)
    eu = tmp_path / "EURUSD"
    eu.mkdir()
    (eu / "EURUSD_M5.dat").write_bytes(b"x" * 64)
    out = _index_disk_files(tmp_path)
    assert ("EURUSD", "M5") in out


def test_index_disk_files_empty_dir_no_crash(tmp_path: Path) -> None:
    out = _index_disk_files(tmp_path)
    assert out == {}


def test_index_disk_files_skips_files_without_underscore(tmp_path: Path) -> None:
    odd = tmp_path / "weirdsym"
    odd.mkdir()
    (odd / "noseparator.dat").write_bytes(b"x")
    out = _index_disk_files(tmp_path)
    assert out == {}


def test_index_disk_files_missing_root(tmp_path: Path) -> None:
    out = _index_disk_files(tmp_path / "does-not-exist")
    assert out == {}


# ---- _index_registry_rows -------------------------------------------------


def test_index_registry_rows_groups_by_symbol_tf() -> None:
    rows = [
        {"SYMBOL": "BTCUSDT", "TIMEFRAME": "M1"},
        {"SYMBOL": "BTCUSDT", "TIMEFRAME": "H1"},
        {"USYMBOL": "BTCUSDT", "TIMEFRAME": "M1"},  # also indexed
        {"SYMBOL": None, "TIMEFRAME": "M1"},  # dropped
    ]
    out = _index_registry_rows(rows)
    assert ("BTCUSDT", "M1") in out
    assert len(out[("BTCUSDT", "M1")]) == 2
    assert ("BTCUSDT", "H1") in out


def test_index_registry_rows_handles_instrument_fallback() -> None:
    rows = [{"INSTRUMENT": "GBPUSD", "TIMEFRAME": "M30"}]
    out = _index_registry_rows(rows)
    assert ("GBPUSD", "M30") in out


# ---- _compare_indexes -----------------------------------------------------


def test_compare_indexes_pure_match() -> None:
    disk = {("BTCUSDT", "M1"): []}
    reg = {("BTCUSDT", "M1"): []}
    cmp = _compare_indexes(disk, reg)
    assert cmp["matched_pairs"] == [("BTCUSDT", "M1")]
    assert cmp["disk_only_pairs"] == []
    assert cmp["registry_only_pairs"] == []


def test_compare_indexes_finds_disk_orphans() -> None:
    disk = {("BTCUSDT", "M1"): [], ("EURUSD", "M5"): []}
    reg = {("BTCUSDT", "M1"): []}
    cmp = _compare_indexes(disk, reg)
    assert ("EURUSD", "M5") in cmp["disk_only_pairs"]
    assert ("BTCUSDT", "M1") in cmp["matched_pairs"]


def test_compare_indexes_finds_registry_orphans() -> None:
    disk = {("BTCUSDT", "M1"): []}
    reg = {("BTCUSDT", "M1"): [], ("BTCUSDT", "H1"): []}
    cmp = _compare_indexes(disk, reg)
    assert ("BTCUSDT", "H1") in cmp["registry_only_pairs"]


def test_compare_indexes_handles_both_empty() -> None:
    cmp = _compare_indexes({}, {})
    assert cmp == {
        "matched_pairs": [],
        "disk_only_pairs": [],
        "registry_only_pairs": [],
    }
