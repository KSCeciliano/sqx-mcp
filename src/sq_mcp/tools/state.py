"""Persistent state store for cross-session agent memory.

Backed by a single JSON file at ``<projects_dir>/.sq_mcp_state.json`` (or a
custom path via ``SQ_MCP_STATE`` env var). The store is a flat key/value
mapping plus a per-key "namespace" so different agents can avoid colliding.

Typical use cases:

- Remember which magic numbers have already been assigned per account.
- Track the most recent strategy_export_pipeline run so the next session
  knows what was shipped.
- Persist user-confirmed preferences (e.g. "always block YELLOW too" — but
  the user has to set this explicitly).

This is NOT for ephemeral session state — use TaskCreate / response context
for that. State writes here are durable.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload

_STATE_LOCK = threading.Lock()


class StateGetArgs(BaseModel):
    key: str = Field(..., max_length=128)
    namespace: str = Field(
        "default", max_length=64,
        description="Logical bucket (e.g. 'magic_numbers', 'preferences', 'session_notes').",
    )

    @field_validator("key", "namespace")
    @classmethod
    def _v_safe(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_\-\.]+", v):
            raise ValueError("must be alphanumeric / _ / - / .")
        return v


class StateSetArgs(StateGetArgs):
    value: Any = Field(..., description="Any JSON-serializable value.")
    overwrite: bool = Field(
        True,
        description="If False and the key already has a value, refuse.",
    )


class StateDeleteArgs(StateGetArgs):
    pass


class StateListArgs(BaseModel):
    namespace: str | None = Field(
        None,
        description="If set, restrict to one namespace. None = all namespaces.",
    )


class StateExportArgs(BaseModel):
    output_path: str = Field(..., description="Where to write the JSON snapshot.")


class StateImportArgs(BaseModel):
    input_path: str = Field(..., description="JSON snapshot to import (as produced by state_export).")
    merge: bool = Field(
        True,
        description=(
            "If True (default), merge into existing state (incoming wins on key collision). "
            "If False, REPLACE the whole state with the imported payload."
        ),
    )


def _state_path(eng) -> Path:  # noqa: ANN001
    env = os.environ.get("SQ_MCP_STATE")
    if env:
        return Path(env).expanduser()
    return eng.config.projects_dir / ".sq_mcp_state.json"


def _read_state(p: Path) -> dict[str, Any]:
    if not p.is_file():
        return {"version": 1, "data": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        # Corrupt files (bad bytes, half-written JSON) are treated as empty
        # rather than crashing every state read.
        return {"version": 1, "data": {}}


def _write_state_atomic(p: Path, payload: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Read a value from the persistent state store at "
            "<projects_dir>/.sq_mcp_state.json (override path via SQ_MCP_STATE). "
            "Returns None if the key isn't set. Use this to recall cross-session "
            "agent memory like assigned magic numbers, user preferences, etc."
        )
    )
    async def state_get(args: StateGetArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            with _STATE_LOCK:
                state = _read_state(_state_path(eng))
            value = state.get("data", {}).get(args.namespace, {}).get(args.key)
            return {
                "ok": True,
                "namespace": args.namespace,
                "key": args.key,
                "found": value is not None,
                "value": value,
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Set a value in the persistent state store. Value can be any "
            "JSON-serializable type. With overwrite=False, refuses if the key "
            "already exists. Atomic write (tmp + os.replace) protects against "
            "corruption on crash."
        )
    )
    async def state_set(args: StateSetArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            with _STATE_LOCK:
                state = _read_state(p)
                data = state.setdefault("data", {})
                ns = data.setdefault(args.namespace, {})
                if args.key in ns and not args.overwrite:
                    return {
                        "ok": False,
                        "error": (
                            f"key {args.namespace}.{args.key!r} already exists and "
                            "overwrite=False"
                        ),
                        "existing_value": ns[args.key],
                    }
                old_value = ns.get(args.key)
                ns[args.key] = args.value
                state["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
                _write_state_atomic(p, state)
            return {
                "ok": True,
                "namespace": args.namespace,
                "key": args.key,
                "old_value": old_value,
                "new_value": args.value,
                "state_path": str(p),
            }
        except (EngineError, OSError, TypeError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Remove a key from the persistent state store. Reports whether the "
            "key existed and what value was removed."
        )
    )
    async def state_delete(args: StateDeleteArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            with _STATE_LOCK:
                state = _read_state(p)
                ns = state.get("data", {}).get(args.namespace, {})
                existed = args.key in ns
                old_value = ns.pop(args.key, None)
                if existed:
                    state["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
                    _write_state_atomic(p, state)
            return {
                "ok": True,
                "namespace": args.namespace,
                "key": args.key,
                "existed": existed,
                "removed_value": old_value,
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "List the state store contents — namespaces, keys per namespace, "
            "and (optionally truncated) value preview. Use to discover what's "
            "been remembered from past sessions."
        )
    )
    async def state_list(args: StateListArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            with _STATE_LOCK:
                state = _read_state(_state_path(eng))
            data = state.get("data", {})
            namespaces = sorted(data.keys())
            out_ns: dict[str, dict[str, Any]] = {}
            for ns_name in namespaces:
                if args.namespace and ns_name != args.namespace:
                    continue
                ns_payload = data.get(ns_name) or {}
                entries: dict[str, Any] = {}
                for k, v in ns_payload.items():
                    preview = v
                    if isinstance(preview, str) and len(preview) > 200:
                        preview = preview[:200] + "…"
                    entries[k] = preview
                out_ns[ns_name] = {
                    "key_count": len(ns_payload),
                    "entries": entries,
                }
            return {
                "ok": True,
                "state_path": str(_state_path(eng)),
                "last_updated": state.get("last_updated"),
                "namespace_count": len(out_ns),
                "namespaces": out_ns,
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Export the persistent state store to a JSON file. Use this to back "
            "up state before risky operations, or to transfer state across "
            "machines. The exported file is the exact on-disk JSON, so the "
            "schema is documented by the state_set tool's behavior."
        )
    )
    async def state_export(args: StateExportArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            with _STATE_LOCK:
                state = _read_state(_state_path(eng))
            from sq_mcp._validation import resolve_safe_path
            out = resolve_safe_path(args.output_path, must_exist=False)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
            )
            data = state.get("data", {})
            return {
                "ok": True,
                "output_path": str(out),
                "namespace_count": len(data),
                "total_keys": sum(len(v) for v in data.values()),
                "exported_bytes": out.stat().st_size,
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Import a JSON snapshot (from state_export) into the persistent "
            "state store. merge=True (default): merge per-namespace, incoming "
            "wins on key collision. merge=False: REPLACE the entire store "
            "with the incoming payload — destructive."
        )
    )
    async def state_import(args: StateImportArgs, ctx: Context) -> dict:
        try:
            from sq_mcp._validation import resolve_safe_path
            eng = get_engine(ctx)
            p_in = resolve_safe_path(args.input_path, must_exist=True)
            try:
                incoming = json.loads(p_in.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"input JSON invalid: {exc}"}
            if not isinstance(incoming, dict) or "data" not in incoming:
                return {
                    "ok": False,
                    "error": "input does not look like a state-store snapshot",
                }
            p_out = _state_path(eng)
            with _STATE_LOCK:
                if args.merge:
                    current = _read_state(p_out)
                    cur_data = current.setdefault("data", {})
                    for ns, kv in (incoming.get("data") or {}).items():
                        cur_ns = cur_data.setdefault(ns, {})
                        cur_ns.update(kv)
                    current["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
                    _write_state_atomic(p_out, current)
                    namespaces_touched = len(incoming.get("data") or {})
                else:
                    incoming["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
                    _write_state_atomic(p_out, incoming)
                    namespaces_touched = len(incoming.get("data") or {})
            return {
                "ok": True,
                "input_path": str(p_in),
                "state_path": str(p_out),
                "merge_mode": args.merge,
                "namespaces_touched": namespaces_touched,
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "StateDeleteArgs",
    "StateExportArgs",
    "StateGetArgs",
    "StateImportArgs",
    "StateListArgs",
    "StateSetArgs",
    "_read_state",
    "_state_path",
    "_write_state_atomic",
    "register",
]
