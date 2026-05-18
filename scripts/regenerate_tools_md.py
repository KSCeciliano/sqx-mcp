#!/usr/bin/env python3
"""Regenerate TOOLS.md from the live FastMCP registry.

Usage: python scripts/regenerate_tools_md.py > TOOLS.md

Run after adding new tools to keep the per-category catalog in sync. The
script imports the server (which registers every tool) and dumps a Markdown
file grouped by the heuristic categorizer in `tools/meta.py`.
"""

from __future__ import annotations

import sys

from sq_mcp import server
from sq_mcp.tools.meta import _categorize_tool


def main() -> int:
    tools = server.mcp._tool_manager._tools  # noqa: SLF001
    by_cat: dict[str, list[tuple[str, str]]] = {}
    for name, tool in sorted(tools.items()):
        cat = _categorize_tool(name)
        by_cat.setdefault(cat, []).append((name, (tool.description or "").strip()))

    print("# sq-mcp tool catalog")
    print()
    print(
        f"{len(tools)} MCP tools, grouped by heuristic category. "
        "Generated from the live server."
    )
    print()
    print(
        "All tool names are namespaced `mcp__strategyquant__*` when called "
        "from an MCP client."
    )
    print()
    for cat in sorted(by_cat):
        print(f"## {cat} ({len(by_cat[cat])})")
        print()
        for name, desc in sorted(by_cat[cat]):
            d = desc.replace("\n", " ").strip()
            if len(d) > 240:
                d = d[:240] + "..."
            print(f"- **{name}** — {d}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
