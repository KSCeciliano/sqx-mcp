"""MCP tool registrations.

Each submodule exposes a `register(mcp)` function that attaches its tools
to the FastMCP instance. server.py imports them all and calls register().
"""

from sq_mcp.tools import analysis, data, databanks, monitor, projects, symbols

__all__ = ["projects", "databanks", "data", "symbols", "analysis", "monitor"]
