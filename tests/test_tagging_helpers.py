"""Unit tests for tagging helpers."""

from __future__ import annotations

from sq_mcp.tools.tagging import (
    _add_tags,
    _list_all,
    _query_all,
    _query_any,
    _remove_tags,
    _rename_tag,
    _untag,
)


def _empty_state() -> dict:
    return {"version": 1, "data": {}}


# ---- _add_tags -------------------------------------------------------------


def test_add_tags_to_new_strategy() -> None:
    state = _empty_state()
    out = _add_tags(state, "s1", ["prod", "trend"])
    assert out["ok"] is True
    assert out["added"] == ["prod", "trend"]
    assert state["data"]["tags"]["s1"] == ["prod", "trend"]


def test_add_tags_idempotent() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["prod"])
    out = _add_tags(state, "s1", ["prod"])
    # Adding existing tag is no-op for "added"
    assert out["added"] == []


def test_add_tags_merges_with_existing() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["prod"])
    out = _add_tags(state, "s1", ["trend"])
    assert sorted(state["data"]["tags"]["s1"]) == ["prod", "trend"]
    assert out["added"] == ["trend"]


def test_add_tags_max_limit_enforced() -> None:
    state = _empty_state()
    # Pre-populate to 49 tags
    _add_tags(state, "s1", [f"tag{i}" for i in range(49)])
    # Adding 2 more would exceed 50
    out = _add_tags(state, "s1", ["new1", "new2"])
    assert out["ok"] is False
    assert "50" in out["error"]


# ---- _remove_tags ----------------------------------------------------------


def test_remove_existing_tags() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["a", "b", "c"])
    out = _remove_tags(state, "s1", ["b"])
    assert out["removed"] == ["b"]
    assert sorted(state["data"]["tags"]["s1"]) == ["a", "c"]


def test_remove_nonexistent_tag_silent() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["a"])
    out = _remove_tags(state, "s1", ["zzz"])
    assert out["removed"] == []
    assert state["data"]["tags"]["s1"] == ["a"]


def test_remove_all_tags_deletes_key() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["a"])
    _remove_tags(state, "s1", ["a"])
    # Key should be removed when no tags remain
    assert "s1" not in state["data"]["tags"]


def test_remove_from_missing_strategy_safe() -> None:
    state = _empty_state()
    out = _remove_tags(state, "ghost", ["any"])
    assert out["ok"] is True
    assert out["current_tags"] == []


# ---- _untag ----------------------------------------------------------------


def test_untag_clears_everything() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["a", "b", "c"])
    out = _untag(state, "s1")
    assert sorted(out["removed"]) == ["a", "b", "c"]
    assert "s1" not in state["data"]["tags"]


def test_untag_nonexistent_safe() -> None:
    state = _empty_state()
    out = _untag(state, "ghost")
    assert out["removed"] == []


# ---- _query_any / _query_all -----------------------------------------------


def test_query_any_finds_or_matches() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["prod", "trend"])
    _add_tags(state, "s2", ["test", "meanrev"])
    _add_tags(state, "s3", ["prod", "meanrev"])
    matches = _query_any(state, ["meanrev"])
    assert "s2" in matches
    assert "s3" in matches
    assert "s1" not in matches


def test_query_any_two_tags_union() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["prod"])
    _add_tags(state, "s2", ["test"])
    _add_tags(state, "s3", ["other"])
    matches = _query_any(state, ["prod", "test"])
    assert set(matches.keys()) == {"s1", "s2"}


def test_query_all_only_intersection() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["prod", "trend"])
    _add_tags(state, "s2", ["prod"])
    _add_tags(state, "s3", ["prod", "trend", "extra"])
    matches = _query_all(state, ["prod", "trend"])
    assert set(matches.keys()) == {"s1", "s3"}


def test_query_no_results_returns_empty() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["a"])
    matches = _query_any(state, ["zzz"])
    assert matches == {}


# ---- _list_all -------------------------------------------------------------


def test_list_all_returns_both_directions() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["prod"])
    _add_tags(state, "s2", ["prod", "test"])
    out = _list_all(state)
    assert out["n_strategies"] == 2
    assert out["n_distinct_tags"] == 2
    # Inverse map
    assert sorted(out["tag_to_strategies"]["prod"]) == ["s1", "s2"]
    assert out["tag_to_strategies"]["test"] == ["s2"]


def test_list_all_empty_state() -> None:
    state = _empty_state()
    out = _list_all(state)
    assert out["n_strategies"] == 0
    assert out["n_distinct_tags"] == 0


# ---- _rename_tag -----------------------------------------------------------


def test_rename_tag_updates_all_strategies() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["mean-rev"])
    _add_tags(state, "s2", ["mean-rev", "extra"])
    out = _rename_tag(state, "mean-rev", "meanrev")
    assert out["renamed_in"] == 2
    # Both strategies have new tag, neither has old
    assert "meanrev" in state["data"]["tags"]["s1"]
    assert "mean-rev" not in state["data"]["tags"]["s1"]
    assert "meanrev" in state["data"]["tags"]["s2"]
    assert "extra" in state["data"]["tags"]["s2"]


def test_rename_tag_to_same_is_noop() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["x"])
    out = _rename_tag(state, "x", "x")
    assert out["renamed_in"] == 0


def test_rename_nonexistent_tag_safe() -> None:
    state = _empty_state()
    _add_tags(state, "s1", ["a"])
    out = _rename_tag(state, "ghost", "haunted")
    assert out["renamed_in"] == 0
    # Original untouched
    assert state["data"]["tags"]["s1"] == ["a"]
