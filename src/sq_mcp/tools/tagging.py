"""Strategy tagging — organize strategies by user-defined labels.

Persists tags in the same JSON store ``state.py`` uses, under a
``tags`` namespace. Tags are arbitrary strings (e.g. ``production``,
``meanrev``, ``do_not_deploy``, ``BTCUSDT-only``) — the user chooses
the vocabulary. Tagging is purely additive: nothing is renamed,
deleted, or moved.

Tools:

- ``strategy_tag_add`` — attach one or more tags to a strategy
  (identified by an arbitrary key, typically the .sqx relpath or a
  trade hash). Idempotent.
- ``strategy_tag_remove`` — remove specific tags from a strategy.
- ``strategy_untag`` — remove every tag from a strategy.
- ``strategy_query_by_tag`` — list every strategy matching ANY of the
  given tags (OR semantics). Returns ``{strategy_key: [tags]}``.
- ``strategy_query_by_tag_all`` — same but AND semantics.
- ``strategy_tag_list_all`` — return the full mapping of tag → list of
  strategies and strategy → list of tags. Useful for a one-shot
  overview.
- ``strategy_tag_rename`` — rename a tag everywhere it appears.

Persistence: all state is held in ``.sq_mcp_state.json`` under
``data.tags.{strategy_key: [tags]}``. Inspect / back up that file
directly if you need raw access.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.state import _read_state, _state_path, _write_state_atomic

TAG_NAMESPACE = "tags"
MAX_TAG_LEN = 64
MAX_TAGS_PER_STRATEGY = 50


# ---- argument schemas ------------------------------------------------------


class TagAddArgs(BaseModel):
    strategy_key: str = Field(..., min_length=1, max_length=512)
    tags: list[str] = Field(..., min_length=1, max_length=20)

    @field_validator("tags")
    @classmethod
    def _v(cls, tags: list[str]) -> list[str]:
        out = []
        for t in tags:
            t2 = t.strip()
            if not t2 or len(t2) > MAX_TAG_LEN:
                raise ValueError(f"tag must be 1-{MAX_TAG_LEN} chars after stripping")
            out.append(t2)
        return out


class TagRemoveArgs(BaseModel):
    strategy_key: str = Field(..., min_length=1, max_length=512)
    tags: list[str] = Field(..., min_length=1, max_length=20)


class TagUntagArgs(BaseModel):
    strategy_key: str = Field(..., min_length=1, max_length=512)


class TagQueryArgs(BaseModel):
    tags: list[str] = Field(..., min_length=1, max_length=20)


class TagRenameArgs(BaseModel):
    old_tag: str = Field(..., min_length=1, max_length=MAX_TAG_LEN)
    new_tag: str = Field(..., min_length=1, max_length=MAX_TAG_LEN)


# ---- helpers ---------------------------------------------------------------


def _get_tag_map(state: dict[str, Any]) -> dict[str, list[str]]:
    data = state.setdefault("data", {})
    return data.setdefault(TAG_NAMESPACE, {})


def _ensure_distinct_sorted(tags: list[str]) -> list[str]:
    return sorted(set(tags))


def _add_tags(state: dict[str, Any], key: str, new_tags: list[str]) -> dict[str, Any]:
    tag_map = _get_tag_map(state)
    current = set(tag_map.get(key, []))
    merged = current.union(set(new_tags))
    if len(merged) > MAX_TAGS_PER_STRATEGY:
        return {
            "ok": False,
            "error": f"strategy would exceed {MAX_TAGS_PER_STRATEGY} tags",
        }
    tag_map[key] = _ensure_distinct_sorted(list(merged))
    added = sorted(set(new_tags) - current)
    return {"ok": True, "added": added, "current_tags": tag_map[key]}


def _remove_tags(state: dict[str, Any], key: str, to_remove: list[str]) -> dict[str, Any]:
    tag_map = _get_tag_map(state)
    if key not in tag_map:
        return {"ok": True, "removed": [], "current_tags": []}
    before = set(tag_map[key])
    target = set(to_remove)
    remaining = before - target
    if not remaining:
        del tag_map[key]
        current = []
    else:
        tag_map[key] = _ensure_distinct_sorted(list(remaining))
        current = tag_map[key]
    return {
        "ok": True,
        "removed": sorted(before & target),
        "current_tags": current,
    }


def _untag(state: dict[str, Any], key: str) -> dict[str, Any]:
    tag_map = _get_tag_map(state)
    if key in tag_map:
        removed = tag_map.pop(key)
        return {"ok": True, "removed": removed}
    return {"ok": True, "removed": []}


def _query_any(state: dict[str, Any], query_tags: list[str]) -> dict[str, list[str]]:
    tag_map = _get_tag_map(state)
    q = set(query_tags)
    out: dict[str, list[str]] = {}
    for key, tags in tag_map.items():
        if q.intersection(set(tags)):
            out[key] = list(tags)
    return out


def _query_all(state: dict[str, Any], query_tags: list[str]) -> dict[str, list[str]]:
    tag_map = _get_tag_map(state)
    q = set(query_tags)
    out: dict[str, list[str]] = {}
    for key, tags in tag_map.items():
        if q.issubset(set(tags)):
            out[key] = list(tags)
    return out


def _list_all(state: dict[str, Any]) -> dict[str, Any]:
    tag_map = _get_tag_map(state)
    # Build tag → strategies inverse
    inverse: dict[str, list[str]] = {}
    for key, tags in tag_map.items():
        for t in tags:
            inverse.setdefault(t, []).append(key)
    for t in inverse:
        inverse[t].sort()
    return {
        "strategy_to_tags": dict(sorted(tag_map.items())),
        "tag_to_strategies": dict(sorted(inverse.items())),
        "n_strategies": len(tag_map),
        "n_distinct_tags": len(inverse),
    }


def _rename_tag(state: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    tag_map = _get_tag_map(state)
    if old == new:
        return {"ok": True, "renamed_in": 0, "note": "old == new, no-op"}
    affected = 0
    for key, tags in list(tag_map.items()):
        if old in tags:
            new_tags = sorted(set([new if t == old else t for t in tags]))
            tag_map[key] = new_tags
            affected += 1
    return {"ok": True, "renamed_in": affected, "old_tag": old, "new_tag": new}


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Attach one or more tags to a strategy. strategy_key is an arbitrary "
            "stable identifier (typically the .sqx relpath or trades_hash). "
            "Idempotent — re-adding the same tag is a no-op. Persists to the "
            "state JSON store."
        )
    )
    async def strategy_tag_add(args: TagAddArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            state = _read_state(p)
            out = _add_tags(state, args.strategy_key, args.tags)
            if out.get("ok"):
                _write_state_atomic(p, state)
            return {**out, "strategy_key": args.strategy_key}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Remove specific tags from a strategy. Tags not present on the "
            "strategy are silently ignored. Returns which tags were actually "
            "removed and the remaining tag list. Persists to state JSON."
        )
    )
    async def strategy_tag_remove(args: TagRemoveArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            state = _read_state(p)
            out = _remove_tags(state, args.strategy_key, args.tags)
            _write_state_atomic(p, state)
            return {**out, "strategy_key": args.strategy_key}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Remove every tag from a strategy. Returns the previously-attached "
            "tag list. Idempotent."
        )
    )
    async def strategy_untag(args: TagUntagArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            state = _read_state(p)
            out = _untag(state, args.strategy_key)
            _write_state_atomic(p, state)
            return {**out, "strategy_key": args.strategy_key}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Query strategies matching ANY of the given tags (OR semantics). "
            "Returns {strategy_key: [tags]}. Read-only."
        )
    )
    async def strategy_query_by_tag(args: TagQueryArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            state = _read_state(_state_path(eng))
            matches = _query_any(state, args.tags)
            return {"ok": True, "query_tags": args.tags, "matches": matches, "count": len(matches)}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Query strategies matching ALL of the given tags (AND semantics). "
            "Returns {strategy_key: [tags]}. Read-only."
        )
    )
    async def strategy_query_by_tag_all(args: TagQueryArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            state = _read_state(_state_path(eng))
            matches = _query_all(state, args.tags)
            return {"ok": True, "query_tags": args.tags, "matches": matches, "count": len(matches)}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "List the full tag system: strategy → tags mapping AND tag → "
            "strategies inverse. Plus counts. Read-only."
        )
    )
    async def strategy_tag_list_all(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            state = _read_state(_state_path(eng))
            return {"ok": True, **_list_all(state)}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Rename a tag everywhere it appears. Returns count of strategies "
            "affected. Useful to canonicalize ad-hoc tag spelling (e.g. "
            "'mean-rev' → 'meanrev'). Persists to state JSON."
        )
    )
    async def strategy_tag_rename(args: TagRenameArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            state = _read_state(p)
            out = _rename_tag(state, args.old_tag, args.new_tag)
            _write_state_atomic(p, state)
            return out
        except EngineError as e:
            return safe_error_payload(e)
