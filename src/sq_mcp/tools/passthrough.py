"""Escape hatch — direct sqcli HTTP API passthrough.

Most of the plugin wraps specific sqcli commands with safety, validation, and
nicer return shapes. But the agent occasionally needs to run a command this
plugin doesn't directly support (a future SQ build adds a new command, the
user wants to debug something raw, etc.).

This module exposes a single tool, ``engine_call``, that pipes a vetted
command string through to ``EngineClient.call()`` and returns the raw response.

Safety guards:

- Command must start with ``-`` (sqcli convention) so it can't be hijacked.
- Maximum length 2048 chars.
- Refuses commands that include shell metacharacters (``;``, ``|``, ``$()``)
  — those would be sent literally to sqcli, which doesn't interpret them,
  but blocking them protects us from accidental URL-encoding chaos.
- Refuses ``rm``, ``shutdown`` etc. by string match.

This is not a replacement for the typed tools — those still give better
validation. Use ``engine_call`` only when no typed tool fits.
"""

from __future__ import annotations

import re

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload

_FORBIDDEN_TOKENS = (
    "rm ", "rm\t", "shutdown", "reboot", "; ", "&& ", "|| ", "$(", "`",
)


class EngineCallArgs(BaseModel):
    command: str = Field(
        ...,
        min_length=2,
        max_length=2048,
        description=(
            "Full sqcli command string starting with '-'. Example: "
            "'-databank action=list project=Builder name=Results'."
        ),
    )
    timeout_seconds: float = Field(
        30.0,
        ge=1.0,
        le=600.0,
        description="HTTP call timeout. The default suits short commands.",
    )

    @field_validator("command")
    @classmethod
    def _v_command(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("-"):
            raise ValueError("command must start with '-' (sqcli convention)")
        if "\n" in v or "\r" in v:
            raise ValueError("command must be a single line")
        low = v.lower()
        for token in _FORBIDDEN_TOKENS:
            if token in low:
                raise ValueError(
                    f"command contains forbidden substring {token!r}. "
                    "Use a typed sq_mcp tool instead."
                )
        return v


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Escape-hatch passthrough to sqcli's HTTP API. Pass any command "
            "(e.g. '-project action=status name=Builder') and get the raw "
            "engine response. Use only when no typed tool covers your need — "
            "typed tools validate args and decode SQ-specific oddities. Blocks "
            "shell metacharacters and obvious destructive tokens."
        )
    )
    async def engine_call(args: EngineCallArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            text = await eng.call(args.command, timeout=args.timeout_seconds)
            stripped = text.strip()
            truncation_marker = None
            m = re.search(r"\((\d+) more lines?,\s*(\d+) bytes total\)", stripped)
            if m:
                truncation_marker = {
                    "more_lines": int(m.group(1)),
                    "total_bytes": int(m.group(2)),
                }
            return {
                "ok": True,
                "command": args.command,
                "response_length": len(text),
                "truncation_marker": truncation_marker,
                "raw": text,
                "raw_preview": stripped[:2000],
            }
        except EngineError as exc:
            return safe_error_payload(exc)


__all__ = ["EngineCallArgs", "register"]
