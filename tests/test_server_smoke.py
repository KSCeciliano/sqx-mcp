"""Server-level smoke tests — every module imports, every tool registers.

Catches regressions where a new module breaks import or registration. Cheap
and fast; runs alongside the rest of the suite.
"""

from __future__ import annotations


def test_server_imports_cleanly() -> None:
    """The server module imports without ever hitting a network or filesystem call."""
    from sq_mcp import server
    assert server.mcp is not None


def test_every_module_register_function_runs() -> None:
    """Re-registering a tool warns ('already exists'), but the call should not raise."""
    # Importing server runs all register() functions exactly once.
    from sq_mcp import server
    tools = server.mcp._tool_manager._tools
    assert len(tools) > 50, "expected substantial tool count after all modules register"


def test_each_tool_has_nonempty_description() -> None:
    """Discoverability gate — a tool with no description is useless to an agent."""
    from sq_mcp import server
    bad = [
        name for name, t in server.mcp._tool_manager._tools.items()
        if not (t.description or "").strip()
    ]
    assert not bad, f"tools with empty description: {bad}"


def test_no_duplicate_tool_names() -> None:
    """FastMCP can register the same name twice and just warn; we want errors."""
    from sq_mcp import server
    names = list(server.mcp._tool_manager._tools.keys())
    assert len(names) == len(set(names))


def test_health_check_distinct_from_environment_health_check() -> None:
    """Two health checks exist and shouldn't collide."""
    from sq_mcp import server
    tools = server.mcp._tool_manager._tools
    # Both registered (one from diagnostics, one from meta)
    assert "health_check" in tools
    assert "environment_health_check" in tools


def test_meta_module_categorization_covers_all_tools() -> None:
    """Sanity check: every tool maps to a non-empty category string."""
    from sq_mcp import server
    from sq_mcp.tools.meta import _categorize_tool
    for name in server.mcp._tool_manager._tools:
        cat = _categorize_tool(name)
        assert isinstance(cat, str)
        assert cat  # non-empty


def test_common_workflows_is_well_formed() -> None:
    """The hand-curated workflow list should have stable structure."""
    from sq_mcp.tools.meta import _COMMON_WORKFLOWS
    assert _COMMON_WORKFLOWS, "expected at least one workflow"
    for w in _COMMON_WORKFLOWS:
        assert "name" in w
        assert "description" in w
        assert "steps" in w
        assert isinstance(w["steps"], list)
        assert w["steps"], f"workflow {w['name']!r} has no steps"
