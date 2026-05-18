"""Unit tests for symbol_intel helpers."""

from __future__ import annotations

import os
import time
from pathlib import Path

from sq_mcp.tools.symbol_intel import (
    _aliases,
    _coverage_gaps,
    _freshness_rows,
    _matrix_row,
    _parse_tf_from_filename,
)

# ---- _aliases --------------------------------------------------------------


def test_aliases_crypto_usdt() -> None:
    out = _aliases("BTCUSDT")
    assert out["base"] == "BTC"
    assert out["quote"] == "USDT"
    assert "BTC/USDT" in out["aliases"]
    assert "BTC-USDT" in out["aliases"]
    assert "BTCUSDT" in out["aliases"]


def test_aliases_crypto_lowercase_input() -> None:
    out = _aliases("ethusd")
    assert out["base"] == "ETH"
    assert out["quote"] == "USD"


def test_aliases_strips_slash() -> None:
    out = _aliases("BTC/USDT")
    assert out["canonical"] == "BTCUSDT"


def test_aliases_forex_pair() -> None:
    out = _aliases("EURUSD")
    assert out["base"] == "EUR"
    assert out["quote"] == "USD"
    assert "EUR/USD" in out["aliases"]


def test_aliases_unknown_falls_back() -> None:
    # Three-letter known forex won't match crypto pattern; goes to forex 6-char
    out = _aliases("XYZ")
    # 3 chars → not 6 letters → just canonical
    assert out["aliases"] == ["XYZ"]


# ---- _parse_tf_from_filename ----------------------------------------------


def test_parse_tf_standard() -> None:
    assert _parse_tf_from_filename("BTCUSDT_M1.dat") == "M1"
    assert _parse_tf_from_filename("EURUSD_H4.dat") == "H4"


def test_parse_tf_no_underscore() -> None:
    assert _parse_tf_from_filename("oddname.dat") is None


def test_parse_tf_compound_symbol() -> None:
    # Symbol with underscore in it
    assert _parse_tf_from_filename("BTC_USDT_M5.dat") == "M5"


# ---- _matrix_row -----------------------------------------------------------


def test_matrix_row_all_present(tmp_path: Path) -> None:
    sym_dir = tmp_path / "BTCUSDT"
    sym_dir.mkdir()
    dats = []
    for tf in ("M1", "M5", "H1"):
        p = sym_dir / f"BTCUSDT_{tf}.dat"
        p.write_bytes(b"X" * 1000)
        dats.append(p)
    row = _matrix_row("BTCUSDT", dats, ["M1", "M5", "H1"], include_size=True)
    assert row["has_count"] == 3
    assert row["missing"] == []
    assert row["M1"]["present"] is True
    assert "size_mb" in row["M1"]


def test_matrix_row_some_missing(tmp_path: Path) -> None:
    sym_dir = tmp_path / "BTCUSDT"
    sym_dir.mkdir()
    p = sym_dir / "BTCUSDT_M1.dat"
    p.write_bytes(b"X" * 100)
    row = _matrix_row("BTCUSDT", [p], ["M1", "H1", "D1"], include_size=False)
    assert row["has_count"] == 1
    assert "H1" in row["missing"]
    assert "D1" in row["missing"]


def test_matrix_row_no_files(tmp_path: Path) -> None:
    row = _matrix_row("BTCUSDT", [], ["M1", "M5"], include_size=False)
    assert row["has_count"] == 0
    assert row["missing"] == ["M1", "M5"]


# ---- _freshness_rows -------------------------------------------------------


def test_freshness_rows_flags_old_files(tmp_path: Path) -> None:
    sym_dir = tmp_path / "BTCUSDT"
    sym_dir.mkdir()
    old = sym_dir / "BTCUSDT_M1.dat"
    old.write_bytes(b"X" * 100)
    # Backdate mtime by 30 days
    old_mtime = time.time() - 30 * 86400
    os.utime(old, (old_mtime, old_mtime))

    new = sym_dir / "BTCUSDT_H1.dat"
    new.write_bytes(b"X" * 100)
    # Force fresh mtime
    os.utime(new, None)

    rows = _freshness_rows(tmp_path, stale_days=7)
    by_tf = {r["timeframe"]: r for r in rows}
    assert by_tf["M1"]["is_stale"] is True
    assert by_tf["H1"]["is_stale"] is False


def test_freshness_rows_empty(tmp_path: Path) -> None:
    rows = _freshness_rows(tmp_path, stale_days=7)
    assert rows == []


# ---- _coverage_gaps --------------------------------------------------------


def test_coverage_gaps_finds_missing(tmp_path: Path) -> None:
    sym_dir = tmp_path / "BTCUSDT"
    sym_dir.mkdir()
    (sym_dir / "BTCUSDT_M1.dat").write_bytes(b"X")
    requested = [
        {"symbol": "BTCUSDT", "timeframe": "M1"},  # have
        {"symbol": "BTCUSDT", "timeframe": "H1"},  # missing
        {"symbol": "ETHUSDT", "timeframe": "M1"},  # missing symbol
    ]
    gaps = _coverage_gaps(requested, tmp_path)
    assert len(gaps) == 2
    rels = {(g["symbol"], g["timeframe"]) for g in gaps}
    assert ("BTCUSDT", "H1") in rels
    assert ("ETHUSDT", "M1") in rels


def test_coverage_gaps_empty_when_all_present(tmp_path: Path) -> None:
    sym_dir = tmp_path / "BTCUSDT"
    sym_dir.mkdir()
    (sym_dir / "BTCUSDT_M1.dat").write_bytes(b"X")
    requested = [{"symbol": "BTCUSDT", "timeframe": "M1"}]
    gaps = _coverage_gaps(requested, tmp_path)
    assert gaps == []
