"""Shared helpers for MCP tools."""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import Context

from sq_mcp.engine import EngineClient, EngineError


def get_engine(ctx: Context) -> EngineClient:
    """Pull the EngineClient from the FastMCP lifespan context.

    Raises EngineError if the lifespan context is missing or wrong type.
    """
    eng = getattr(ctx.request_context, "lifespan_context", None)
    if not isinstance(eng, EngineClient):
        raise EngineError(
            "EngineClient missing from lifespan context — server boot failed?"
        )
    return eng


# Common patterns that sqcli returns inside its responses (after _strip_noise).
_OK_PATTERNS = (
    re.compile(r"^All tasks completed", re.MULTILINE),
    re.compile(r"^OK", re.MULTILINE),
)
_ERR_PATTERNS = (
    re.compile(r"^Error[:\s]", re.MULTILINE),
    re.compile(r"^Exception[:\s]", re.MULTILINE),
)

# Lines we filter from "data" lists — engine boot banner etc.
_BOILERPLATE_PREFIXES: tuple[str, ...] = (
    "All tasks completed",
    "Synchronizing databanks",
    "Synchronization finished",
    "Bye",
    "Exit app",
    "Starting StrategyQuant X",
    "Server started on port",
    "HTTP API started",
    "SQX version",
    "Hardware ID",
    "Verifying license",
    "High priority",
    "Using:",
    "Preparing thread executors",
    "Data loaded in",
    "Projects loaded in",
    "Params:",
    "----",
    "Unrecognized command",
)

# Match sqcli's "operation finished, output went to redirect file" markers
# and timestamped header lines like "18:16:00 List of available databanks".
_STATUS_ONLY_PATTERNS = (
    re.compile(r"^\d{2}:\d{2}:\d{2}\s+List of"),
    re.compile(r"\b(Data|Databanks|Strategies|Projects|Instruments|Symbols)\s+listed\.?\s*$"),
)


def is_status_only_response(text: str) -> bool:
    """True iff the response body looks like sqcli's redirected-output status
    (no real data — the actual list went to a `>` file).
    """
    non_noise = [
        line for line in parse_list(text or "")
        if line  # parse_list already strips boilerplate
    ]
    if not non_noise:
        return False
    return all(
        any(p.search(line) for p in _STATUS_ONLY_PATTERNS) for line in non_noise
    )


def parse_response(text: str) -> dict[str, Any]:
    """Wrap a raw sqcli text response with success / error flags."""
    error_msgs = [m.group(0) for m in (p.search(text) for p in _ERR_PATTERNS) if m]
    return {
        "ok": not error_msgs,
        "errors": error_msgs,
        "raw": (text or "").strip(),
    }


def parse_listing_response(
    text: str,
    key: str,
    *,
    drop: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Wrap a listing-style sqcli response with proper ok=False on errors.

    Build 143 returns "Error: Not implemented." (and similar) as the response
    body for `-project action=list`, `-data action=list`, etc. Without this
    guard, parse_list treats that line as a real entry and the caller wrongly
    reports ok=True with one bogus "entry".
    """
    error_msgs = [m.group(0) for m in (p.search(text) for p in _ERR_PATTERNS) if m]
    if error_msgs:
        return {
            "ok": False,
            "errors": error_msgs,
            key: [],
            "raw": (text or "").strip(),
        }
    return {
        "ok": True,
        key: parse_list(text, drop=drop),
        "raw": (text or "").strip(),
    }


def parse_list(text: str, *, drop: tuple[str, ...] = ()) -> list[str]:
    """Return non-noise data lines (skipping known boilerplate)."""
    skip = _BOILERPLATE_PREFIXES + drop
    out: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if any(s.startswith(p) for p in skip):
            continue
        out.append(s)
    return out


def safe_error_payload(exc: Exception) -> dict[str, Any]:
    """Render an exception as a structured tool-result payload (never raises)."""
    return {
        "ok": False,
        "error": str(exc) or repr(exc),
        "error_type": type(exc).__name__,
    }
