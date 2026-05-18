"""Unit tests for lineage helpers."""

from __future__ import annotations

from sq_mcp.tools.lineage import (
    _ancestors,
    _build_tree,
    _descendants,
    _get_lineage,
    _has_ancestor,
    _link,
    _register,
    _remove,
)


def _state() -> dict:
    return {"version": 1, "data": {}}


# ---- _register -------------------------------------------------------------


def test_register_root_node() -> None:
    state = _state()
    out = _register(state, node_id="root", parent_id=None, label="Root", notes=None, metadata=None)
    assert out["ok"] is True
    assert "root" in _get_lineage(state)


def test_register_duplicate_fails() -> None:
    state = _state()
    _register(state, node_id="a", parent_id=None, label=None, notes=None, metadata=None)
    out = _register(state, node_id="a", parent_id=None, label=None, notes=None, metadata=None)
    assert out["ok"] is False
    assert "already exists" in out["error"]


def test_register_with_missing_parent_fails() -> None:
    state = _state()
    out = _register(
        state, node_id="child", parent_id="ghost", label=None, notes=None, metadata=None
    )
    assert out["ok"] is False
    assert "not registered" in out["error"]


def test_register_with_valid_parent() -> None:
    state = _state()
    _register(state, node_id="root", parent_id=None, label=None, notes=None, metadata=None)
    out = _register(state, node_id="child", parent_id="root", label=None, notes=None, metadata=None)
    assert out["ok"] is True
    assert out["node"]["parent_id"] == "root"


# ---- _link -----------------------------------------------------------------


def test_link_to_existing_parent() -> None:
    state = _state()
    _register(state, node_id="a", parent_id=None, label=None, notes=None, metadata=None)
    _register(state, node_id="b", parent_id=None, label=None, notes=None, metadata=None)
    out = _link(state, node_id="b", parent_id="a")
    assert out["ok"] is True
    assert _get_lineage(state)["b"]["parent_id"] == "a"


def test_link_detach() -> None:
    state = _state()
    _register(state, node_id="a", parent_id=None, label=None, notes=None, metadata=None)
    _register(state, node_id="b", parent_id="a", label=None, notes=None, metadata=None)
    out = _link(state, node_id="b", parent_id=None)
    assert out["ok"] is True
    assert _get_lineage(state)["b"]["parent_id"] is None


def test_link_self_parent_rejected() -> None:
    state = _state()
    _register(state, node_id="a", parent_id=None, label=None, notes=None, metadata=None)
    out = _link(state, node_id="a", parent_id="a")
    assert out["ok"] is False
    assert "own parent" in out["error"]


def test_link_cycle_rejected() -> None:
    state = _state()
    _register(state, node_id="a", parent_id=None, label=None, notes=None, metadata=None)
    _register(state, node_id="b", parent_id="a", label=None, notes=None, metadata=None)
    # Try to make a's parent = b → cycle
    out = _link(state, node_id="a", parent_id="b")
    assert out["ok"] is False
    assert "cycle" in out["error"]


# ---- _has_ancestor ---------------------------------------------------------


def test_has_ancestor_true() -> None:
    state = _state()
    _register(state, node_id="root", parent_id=None, label=None, notes=None, metadata=None)
    _register(state, node_id="child", parent_id="root", label=None, notes=None, metadata=None)
    _register(state, node_id="grandchild", parent_id="child", label=None, notes=None, metadata=None)
    lineage = _get_lineage(state)
    assert _has_ancestor(lineage, "grandchild", "root") is True
    assert _has_ancestor(lineage, "grandchild", "child") is True
    assert _has_ancestor(lineage, "child", "grandchild") is False


def test_has_ancestor_handles_unknown_node() -> None:
    state = _state()
    assert _has_ancestor(_get_lineage(state), "ghost", "also_ghost") is False


# ---- _ancestors / _descendants ---------------------------------------------


def test_ancestors_full_chain() -> None:
    state = _state()
    _register(state, node_id="r", parent_id=None, label=None, notes=None, metadata=None)
    _register(state, node_id="m", parent_id="r", label=None, notes=None, metadata=None)
    _register(state, node_id="c", parent_id="m", label=None, notes=None, metadata=None)
    out = _ancestors(_get_lineage(state), "c")
    assert out == ["m", "r"]


def test_ancestors_root_has_none() -> None:
    state = _state()
    _register(state, node_id="r", parent_id=None, label=None, notes=None, metadata=None)
    assert _ancestors(_get_lineage(state), "r") == []


def test_descendants_basic() -> None:
    state = _state()
    _register(state, node_id="r", parent_id=None, label=None, notes=None, metadata=None)
    _register(state, node_id="a", parent_id="r", label=None, notes=None, metadata=None)
    _register(state, node_id="b", parent_id="r", label=None, notes=None, metadata=None)
    _register(state, node_id="c", parent_id="a", label=None, notes=None, metadata=None)
    out = sorted(_descendants(_get_lineage(state), "r"))
    assert out == ["a", "b", "c"]


def test_descendants_leaf_returns_empty() -> None:
    state = _state()
    _register(state, node_id="r", parent_id=None, label=None, notes=None, metadata=None)
    _register(state, node_id="leaf", parent_id="r", label=None, notes=None, metadata=None)
    assert _descendants(_get_lineage(state), "leaf") == []


# ---- _build_tree -----------------------------------------------------------


def test_build_tree_nested() -> None:
    state = _state()
    _register(state, node_id="r", parent_id=None, label="Root", notes=None, metadata=None)
    _register(state, node_id="a", parent_id="r", label="A", notes=None, metadata=None)
    _register(state, node_id="b", parent_id="r", label="B", notes=None, metadata=None)
    _register(state, node_id="aa", parent_id="a", label="AA", notes=None, metadata=None)
    tree = _build_tree(_get_lineage(state), "r")
    assert tree["label"] == "Root"
    assert len(tree["children"]) == 2
    # Children should be sorted alphabetically
    child_a = next(c for c in tree["children"] if c["label"] == "A")
    assert len(child_a["children"]) == 1
    assert child_a["children"][0]["label"] == "AA"


def test_build_tree_unknown_node() -> None:
    state = _state()
    tree = _build_tree(_get_lineage(state), "ghost")
    assert "error" in tree


# ---- _remove ---------------------------------------------------------------


def test_remove_detaches_children() -> None:
    state = _state()
    _register(state, node_id="r", parent_id=None, label=None, notes=None, metadata=None)
    _register(state, node_id="a", parent_id="r", label=None, notes=None, metadata=None)
    _register(state, node_id="b", parent_id="r", label=None, notes=None, metadata=None)
    out = _remove(state, "r")
    assert out["ok"] is True
    assert sorted(out["detached_children"]) == ["a", "b"]
    assert _get_lineage(state)["a"]["parent_id"] is None
    assert "r" not in _get_lineage(state)


def test_remove_nonexistent() -> None:
    state = _state()
    out = _remove(state, "ghost")
    assert out["ok"] is False
