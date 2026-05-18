"""Strategy lineage tracking — record parent → child evolution.

When you re-build, retest, or fork a strategy, the resulting .sqx is a
*descendant* of the prior version. Tracking that genealogy lets you:

- Find every iteration of a strategy back to its root.
- Tell whether a "new" strategy is genuinely new or a tweak.
- Spot families of strategies (same root) that should be diversified
  against each other.
- Audit which iterations were promoted live vs. dropped.

Persistence: a ``lineage`` namespace inside the state JSON store.
Each node is an entry; edges live as a ``parent`` field pointing to
another node's id.

Tools:

- ``lineage_register`` — record a new strategy node with optional
  parent and notes.
- ``lineage_link`` — set the parent of an already-registered node
  (use when you backfill genealogy).
- ``lineage_ancestors`` — walk parents up from a node.
- ``lineage_descendants`` — walk children down from a node.
- ``lineage_tree`` — produce a nested tree representation.
- ``lineage_get`` — read a single node's metadata.
- ``lineage_list`` — list every node.
- ``lineage_remove`` — delete a node (does NOT cascade — children
  will have parent set to None).

All persistence goes through the same state.py atomic-write helpers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.state import _read_state, _state_path, _write_state_atomic

LINEAGE_NAMESPACE = "lineage"


# ---- argument schemas ------------------------------------------------------


class LineageRegisterArgs(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=256)
    parent_id: str | None = Field(None, max_length=256)
    label: str | None = Field(None, max_length=256)
    notes: str | None = Field(None, max_length=2000)
    metadata: dict[str, Any] | None = None


class LineageLinkArgs(BaseModel):
    node_id: str
    parent_id: str | None = None


class LineageIdArgs(BaseModel):
    node_id: str


# ---- helpers ---------------------------------------------------------------


def _get_lineage(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    data = state.setdefault("data", {})
    return data.setdefault(LINEAGE_NAMESPACE, {})


def _register(
    state: dict[str, Any],
    *,
    node_id: str,
    parent_id: str | None,
    label: str | None,
    notes: str | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    lineage = _get_lineage(state)
    if node_id in lineage:
        return {"ok": False, "error": f"node already exists: {node_id}"}
    if parent_id is not None and parent_id not in lineage:
        return {
            "ok": False,
            "error": f"parent_id {parent_id} not registered — register the parent first or use lineage_link",
        }
    now = datetime.now(timezone.utc).isoformat()
    node = {
        "node_id": node_id,
        "parent_id": parent_id,
        "label": label,
        "notes": notes,
        "metadata": metadata or {},
        "created_at": now,
    }
    lineage[node_id] = node
    return {"ok": True, "node": node}


def _link(
    state: dict[str, Any], *, node_id: str, parent_id: str | None
) -> dict[str, Any]:
    lineage = _get_lineage(state)
    if node_id not in lineage:
        return {"ok": False, "error": f"node not registered: {node_id}"}
    if parent_id is not None and parent_id not in lineage:
        return {"ok": False, "error": f"parent_id {parent_id} not registered"}
    if parent_id == node_id:
        return {"ok": False, "error": "node cannot be its own parent"}
    # Cycle check: walk up from would-be parent; if we reach node_id, reject.
    if parent_id is not None and _has_ancestor(lineage, parent_id, node_id):
        return {
            "ok": False,
            "error": f"cycle detected: {parent_id} is already a descendant of {node_id}",
        }
    lineage[node_id]["parent_id"] = parent_id
    return {"ok": True, "node": lineage[node_id]}


def _has_ancestor(
    lineage: dict[str, dict[str, Any]], start: str, ancestor: str
) -> bool:
    cur = start
    seen = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        if cur == ancestor:
            return True
        node = lineage.get(cur)
        if node is None:
            return False
        cur = node.get("parent_id")
    return False


def _ancestors(
    lineage: dict[str, dict[str, Any]], node_id: str
) -> list[str]:
    out: list[str] = []
    cur = node_id
    seen = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        node = lineage.get(cur)
        if node is None:
            break
        p = node.get("parent_id")
        if p is None:
            break
        out.append(p)
        cur = p
    return out


def _descendants(
    lineage: dict[str, dict[str, Any]], node_id: str
) -> list[str]:
    children_by_parent: dict[str, list[str]] = {}
    for nid, n in lineage.items():
        p = n.get("parent_id")
        if p is not None:
            children_by_parent.setdefault(p, []).append(nid)
    out: list[str] = []
    stack = [node_id]
    seen = set()
    while stack:
        cur = stack.pop()
        for child in children_by_parent.get(cur, []):
            if child in seen:
                continue
            seen.add(child)
            out.append(child)
            stack.append(child)
    return out


def _build_tree(
    lineage: dict[str, dict[str, Any]], node_id: str
) -> dict[str, Any]:
    if node_id not in lineage:
        return {"error": f"unknown node: {node_id}"}
    children_by_parent: dict[str, list[str]] = {}
    for nid, n in lineage.items():
        p = n.get("parent_id")
        if p is not None:
            children_by_parent.setdefault(p, []).append(nid)

    def _node(nid: str) -> dict[str, Any]:
        n = dict(lineage[nid])
        n["children"] = [_node(c) for c in sorted(children_by_parent.get(nid, []))]
        return n

    return _node(node_id)


def _remove(
    state: dict[str, Any], node_id: str
) -> dict[str, Any]:
    lineage = _get_lineage(state)
    if node_id not in lineage:
        return {"ok": False, "error": f"node not registered: {node_id}"}
    lineage.pop(node_id)
    # Detach children
    detached = []
    for nid, n in lineage.items():
        if n.get("parent_id") == node_id:
            n["parent_id"] = None
            detached.append(nid)
    return {"ok": True, "removed": node_id, "detached_children": detached}


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Register a strategy in the lineage tree. node_id is any stable "
            "identifier (typically .sqx relpath or trades_hash). Optional "
            "parent_id, label, notes, metadata. Fails if node_id already exists "
            "or parent_id is missing."
        )
    )
    async def lineage_register(args: LineageRegisterArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            state = _read_state(p)
            out = _register(
                state,
                node_id=args.node_id,
                parent_id=args.parent_id,
                label=args.label,
                notes=args.notes,
                metadata=args.metadata,
            )
            if out["ok"]:
                _write_state_atomic(p, state)
            return out
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Set or clear the parent of an existing node. Detects cycles and "
            "rejects them. Pass parent_id=null to detach."
        )
    )
    async def lineage_link(args: LineageLinkArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            state = _read_state(p)
            out = _link(state, node_id=args.node_id, parent_id=args.parent_id)
            if out["ok"]:
                _write_state_atomic(p, state)
            return out
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "List every ancestor (parent, grandparent, …) of a node. Returns "
            "an ordered list root-direction. Empty list if the node has no "
            "parent."
        )
    )
    async def lineage_ancestors(args: LineageIdArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            state = _read_state(_state_path(eng))
            ancestors = _ancestors(_get_lineage(state), args.node_id)
            return {"ok": True, "node_id": args.node_id, "ancestors": ancestors}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "List every descendant (children, grandchildren, …) of a node. "
            "Returns a flat list in depth-first order."
        )
    )
    async def lineage_descendants(args: LineageIdArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            state = _read_state(_state_path(eng))
            descendants = _descendants(_get_lineage(state), args.node_id)
            return {"ok": True, "node_id": args.node_id, "descendants": descendants}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Produce a nested tree rooted at node_id. Each node has its "
            "metadata + a children array. Useful for rendering or recursive "
            "audits."
        )
    )
    async def lineage_tree(args: LineageIdArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            state = _read_state(_state_path(eng))
            return {"ok": True, "tree": _build_tree(_get_lineage(state), args.node_id)}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Return a single node's metadata. None if the node is not "
            "registered."
        )
    )
    async def lineage_get(args: LineageIdArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            state = _read_state(_state_path(eng))
            node = _get_lineage(state).get(args.node_id)
            return {"ok": True, "node": node}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "List every lineage node. Returns the full nodes dict."
        )
    )
    async def lineage_list(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            state = _read_state(_state_path(eng))
            lineage = _get_lineage(state)
            return {"ok": True, "count": len(lineage), "nodes": dict(lineage)}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Remove a node. Does NOT cascade: children of the removed node "
            "have their parent set to None. Returns the list of detached "
            "children."
        )
    )
    async def lineage_remove(args: LineageIdArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            state = _read_state(p)
            out = _remove(state, args.node_id)
            if out["ok"]:
                _write_state_atomic(p, state)
            return out
        except EngineError as e:
            return safe_error_payload(e)
