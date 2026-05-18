"""Unit tests for trade_analysis helpers."""

from __future__ import annotations

from pathlib import Path

from sq_mcp.tools.trade_analysis import _parse_float, _parse_trade_csv, _pick_column

# ---- _pick_column ---------------------------------------------------------


def test_pick_column_case_insensitive() -> None:
    header = ["openTime", "profit", "Symbol"]
    assert _pick_column(header, ("Profit",)) == "profit"


def test_pick_column_returns_first_match() -> None:
    header = ["P/L", "Net"]
    # Both candidates present — should return first listed candidate
    assert _pick_column(header, ("Profit", "Net", "P/L")) == "Net"


def test_pick_column_returns_none_when_absent() -> None:
    assert _pick_column(["a", "b"], ("Profit",)) is None


# ---- _parse_float ---------------------------------------------------------


def test_parse_float_dot_decimal() -> None:
    assert _parse_float("3.14", decimal_sep=".") == 3.14


def test_parse_float_comma_decimal_swaps_separator() -> None:
    # European format: "1.234,56" → 1234.56
    assert _parse_float("1.234,56", decimal_sep=",") == 1234.56
    assert _parse_float("3,14", decimal_sep=",") == 3.14


def test_parse_float_empty_returns_none() -> None:
    assert _parse_float("", decimal_sep=".") is None
    assert _parse_float("   ", decimal_sep=".") is None


def test_parse_float_invalid_returns_none() -> None:
    assert _parse_float("hello", decimal_sep=".") is None


# ---- _parse_trade_csv ----------------------------------------------------


def _write_csv(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "trades.csv"
    p.write_text(content, encoding="utf-8")
    return p


def test_parse_trade_csv_basic(tmp_path: Path) -> None:
    csv_text = "OpenTime,CloseTime,Symbol,Direction,Profit\n"
    csv_text += "2024-01-01,2024-01-02,BTCUSDT,Long,10.5\n"
    csv_text += "2024-01-02,2024-01-03,BTCUSDT,Long,-5.0\n"
    csv_text += "2024-01-03,2024-01-04,BTCUSDT,Short,20.0\n"
    p = _write_csv(tmp_path, csv_text)
    out = _parse_trade_csv(p, delimiter=",", decimal_sep=".")
    assert out["trade_count"] == 3
    assert out["wins"] == 2
    assert out["losses"] == 1
    assert out["win_rate"] == round(2 / 3, 4)
    assert out["gross_profit"] == 30.5
    assert out["gross_loss"] == 5.0
    assert out["profit_factor"] == round(30.5 / 5.0, 4)
    assert out["best_trade"] == 20.0
    assert out["worst_trade"] == -5.0
    assert out["max_win_streak"] == 1  # not consecutive: W L W
    assert out["max_loss_streak"] == 1
    assert out["by_direction"] == {"Long": 2, "Short": 1}
    assert out["by_symbol"] == {"BTCUSDT": 3}


def test_parse_trade_csv_consecutive_wins(tmp_path: Path) -> None:
    csv_text = "Profit\n"
    csv_text += "\n".join(["5", "10", "15", "-5", "-10", "20", "25"])
    p = _write_csv(tmp_path, csv_text)
    out = _parse_trade_csv(p, delimiter=",", decimal_sep=".")
    # First 3 wins, 2 losses, 2 wins → max win streak 3, max loss streak 2
    assert out["max_win_streak"] == 3
    assert out["max_loss_streak"] == 2


def test_parse_trade_csv_breakeven_resets_streak(tmp_path: Path) -> None:
    csv_text = "Profit\n5\n10\n0\n20\n"
    p = _write_csv(tmp_path, csv_text)
    out = _parse_trade_csv(p, delimiter=",", decimal_sep=".")
    # Streak: W W B W → breakeven resets, then one win
    assert out["max_win_streak"] == 2
    assert out["breakeven"] == 1


def test_parse_trade_csv_european_locale(tmp_path: Path) -> None:
    csv_text = "Profit\n"
    csv_text += "10,5\n"
    csv_text += "-5,25\n"
    p = _write_csv(tmp_path, csv_text)
    out = _parse_trade_csv(p, delimiter=";", decimal_sep=",")
    assert out["trade_count"] == 2
    assert out["wins"] == 1
    assert out["best_trade"] == 10.5
    assert out["worst_trade"] == -5.25


def test_parse_trade_csv_no_profit_column(tmp_path: Path) -> None:
    csv_text = "Time,Direction\n2024,Long\n"
    p = _write_csv(tmp_path, csv_text)
    out = _parse_trade_csv(p, delimiter=",", decimal_sep=".")
    assert "error" in out
    assert "Profit" in out["error"] or "profit" in out["error"]


def test_parse_trade_csv_empty(tmp_path: Path) -> None:
    p = _write_csv(tmp_path, "")
    out = _parse_trade_csv(p, delimiter=",", decimal_sep=".")
    assert "error" in out
    assert "empty CSV" in out["error"]


def test_parse_trade_csv_skips_rows_with_bad_numeric(tmp_path: Path) -> None:
    csv_text = "Profit\n10\nbroken\n20\n"
    p = _write_csv(tmp_path, csv_text)
    out = _parse_trade_csv(p, delimiter=",", decimal_sep=".")
    assert out["trade_count"] == 2
    assert out["skipped"] == 1


def test_parse_trade_csv_profit_factor_when_no_losses(tmp_path: Path) -> None:
    # All wins → profit factor is undefined (None)
    csv_text = "Profit\n10\n20\n30\n"
    p = _write_csv(tmp_path, csv_text)
    out = _parse_trade_csv(p, delimiter=",", decimal_sep=".")
    assert out["profit_factor"] is None
    assert out["gross_loss"] == 0
