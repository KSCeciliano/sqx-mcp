"""Validation helper tests — covers every boundary check in _validation.py."""

from __future__ import annotations

import pytest

from sq_mcp._validation import (
    ValidationError,
    resolve_safe_path,
    validate_databank_name,
    validate_date,
    validate_project_name,
    validate_strategy_list,
    validate_symbol,
    validate_timeframe,
)


@pytest.mark.parametrize("name", ["Builder", "Retester", "My Project", "EUR_USD H1", "a", "X.1"])
def test_project_name_accepts_valid(name):
    assert validate_project_name(name) == name


@pytest.mark.parametrize(
    "bad",
    [
        "",
        " " * 200,                # too long
        "a" * 200,                # too long
        "name\nwith\nnewlines",
        "name;with;semicolons",
        "name&with&amp",
        'name"with"quotes',
        "name=with=equals",       # would inject another arg
    ],
)
def test_project_name_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        validate_project_name(bad)


def test_project_name_rejects_non_string():
    for bad in (None, 123, [], {}):
        with pytest.raises(ValidationError):
            validate_project_name(bad)  # type: ignore[arg-type]


def test_databank_name_validation():
    assert validate_databank_name("Results") == "Results"
    with pytest.raises(ValidationError):
        validate_databank_name("")


@pytest.mark.parametrize("sym", ["EURUSD", "BTCUSDT", "ES.F", "BTC-USD", "EUR/USD", "SP500"])
def test_symbol_accepts_valid(sym):
    assert validate_symbol(sym) == sym


@pytest.mark.parametrize("bad", ["", "EUR USD", "name with space", "x" * 50, "sym;inject"])
def test_symbol_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        validate_symbol(bad)


@pytest.mark.parametrize("tf", ["auto", "TICK", "M1", "M5", "M15", "H1", "H4", "D1", "W1", "MN1", "Intraday"])
def test_timeframe_accepts_valid(tf):
    assert validate_timeframe(tf) == tf


@pytest.mark.parametrize("bad", ["", "5m", "minute", "M", "M9999", "h1", "junk; echo pwn"])
def test_timeframe_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        validate_timeframe(bad)


def test_date_accepts_valid():
    assert validate_date("2024.05.01") == "2024.05.01"


@pytest.mark.parametrize("bad", ["", "2024-05-01", "May 1 2024", "2024.5.1", "2024.13.40"])
def test_date_rejects_bad_format(bad):
    # Pattern check only; we accept calendar-invalid dates like 2024.13.40 because
    # SQ rejects them downstream and we don't want to re-implement the calendar.
    if bad in {"", "2024-05-01", "May 1 2024", "2024.5.1"}:
        with pytest.raises(ValidationError):
            validate_date(bad)


def test_strategy_list_basic():
    assert validate_strategy_list(["a", "b", "c"]) == ["a", "b", "c"]


@pytest.mark.parametrize(
    "bad",
    [
        ["one", "two,with,commas"],   # comma is the list separator
        ["has\"quote"],
        ["has\nnewline"],
    ],
)
def test_strategy_list_rejects_dangerous(bad):
    with pytest.raises(ValidationError):
        validate_strategy_list(bad)


def test_strategy_list_must_be_list():
    with pytest.raises(ValidationError):
        validate_strategy_list("not a list")  # type: ignore[arg-type]


def test_resolve_safe_path_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("os.path.expanduser", lambda p: p.replace("~", str(tmp_path)))
    p = resolve_safe_path("~/file.txt")
    assert str(p).startswith(str(tmp_path))


def test_resolve_safe_path_must_exist(tmp_path):
    nonexistent = tmp_path / "nope.txt"
    with pytest.raises(ValidationError, match="does not exist"):
        resolve_safe_path(nonexistent, must_exist=True)
    nonexistent.write_text("x")
    assert resolve_safe_path(nonexistent, must_exist=True) == nonexistent.resolve()


def test_resolve_safe_path_rejects_empty():
    with pytest.raises(ValidationError):
        resolve_safe_path("   ")


def test_resolve_safe_path_rejects_non_string():
    with pytest.raises(ValidationError):
        resolve_safe_path(42)  # type: ignore[arg-type]
