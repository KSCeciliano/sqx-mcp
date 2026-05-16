"""Input validation helpers used by all MCP tools.

Why this exists: the LLM controls every input that reaches the SQ engine
(project names, file paths, symbols). A misformatted or malicious input
could either crash sqcli, inject extra arguments, or escape into the
filesystem. We validate at the tool boundary.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Identifiers that show up as sqcli arguments. Must not contain spaces, =, &,
# quotes, or shell metacharacters that the sqcli parser splits on.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._\- ]{1,128}$")
_SAFE_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._\-:/]{1,32}$")
_SAFE_TIMEFRAME_RE = re.compile(r"^(?:auto|TICK|M\d{1,3}|H\d{1,2}|D\d{1,2}|W\d{1,2}|MN\d{0,2}|Intraday)$")
_SAFE_DATE_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


class ValidationError(ValueError):
    """Raised when an MCP tool input fails validation."""


def validate_project_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValidationError("project name must be a non-empty string")
    if not _SAFE_NAME_RE.match(name):
        raise ValidationError(
            f"invalid project name {name!r}: only letters, digits, dot, underscore, "
            "hyphen and space are allowed (1–128 chars)"
        )
    return name


def validate_databank_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValidationError("databank name must be a non-empty string")
    if not _SAFE_NAME_RE.match(name):
        raise ValidationError(f"invalid databank name {name!r}")
    return name


def validate_symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not symbol:
        raise ValidationError("symbol must be a non-empty string")
    if not _SAFE_SYMBOL_RE.match(symbol):
        raise ValidationError(
            f"invalid symbol {symbol!r}: must match {_SAFE_SYMBOL_RE.pattern}"
        )
    return symbol


def validate_timeframe(tf: str) -> str:
    if not isinstance(tf, str) or not tf:
        raise ValidationError("timeframe must be a non-empty string")
    if not _SAFE_TIMEFRAME_RE.match(tf):
        raise ValidationError(
            f"invalid timeframe {tf!r}: expected auto/TICK/M*/H*/D*/W*/MN*/Intraday"
        )
    return tf


def validate_date(date: str) -> str:
    if not isinstance(date, str) or not _SAFE_DATE_RE.match(date):
        raise ValidationError(f"invalid date {date!r}: expected yyyy.MM.dd")
    return date


def validate_strategy_list(strategies: list[str]) -> list[str]:
    if not isinstance(strategies, list):
        raise ValidationError("strategies must be a list of strings")
    for s in strategies:
        if not isinstance(s, str) or "," in s or '"' in s or "\n" in s:
            raise ValidationError(
                f"strategy name {s!r} contains forbidden characters (comma, quote, newline)"
            )
    return strategies


def resolve_safe_path(path: str | Path, *, must_exist: bool = False) -> Path:
    """Resolve a user-provided path. Optionally require existence.

    Note: we deliberately do NOT restrict the path to a sandbox — sq-mcp runs
    on the user's machine, with the user's privileges. The validation here
    is to catch typos and obvious mistakes, not to enforce a security boundary
    against the user themselves.
    """
    if not isinstance(path, (str, Path)):
        raise ValidationError(f"path must be string or Path, got {type(path).__name__}")
    raw = str(path).strip()
    if not raw:
        raise ValidationError("path must be a non-empty string")
    p = Path(os.path.expanduser(raw)).resolve()
    if must_exist and not p.exists():
        raise ValidationError(f"path does not exist: {p}")
    return p
