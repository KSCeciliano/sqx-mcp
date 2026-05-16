"""FastMCP server for StrategyQuant X.

Run with `sq-mcp` after `pip install sq-mcp` (or `uvx sq-mcp@latest`).

Stdio transport: stdout is reserved for JSON-RPC, all logs MUST go to stderr.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from sq_mcp.config import detect_config
from sq_mcp.engine import EngineClient
from sq_mcp.tools import analysis, data, databanks, projects, symbols
from sq_mcp.tools import monitor as monitor_tools
from sq_mcp.tools.monitor import _MANAGER  # noqa: F401  (referenced for shutdown)

# stderr-only logging so we don't corrupt stdio JSON-RPC
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("sq_mcp")


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[EngineClient]:
    """Boot SQ X engine once, share the EngineClient with every tool call."""
    config = detect_config()
    log.info("SQ X home: %s (valid=%s)", config.sqx_home, config.is_valid)
    if not config.is_valid:
        log.warning(
            "sqcli not found — engine-backed tools will fail. "
            "File-only analysis tools (analyze_mq5_file, sqx_inspect, inspect_cfx) still work."
        )
        # We still yield an EngineClient so file-parser tools can use config paths,
        # but engine.start() will be deferred and may raise.
        yield EngineClient(config)
        return

    engine = EngineClient(config)
    try:
        mode = await engine.attach_or_start()
        log.info("SQ X engine %s on %s", mode, config.http_url)
        yield engine
    finally:
        # tear down monitor sessions before engine
        from sq_mcp.tools import monitor as _m
        if _m._MANAGER is not None:
            await _m._MANAGER.stop_all()
        await engine.stop()


mcp = FastMCP(
    "strategyquant",
    instructions=(
        "Drive a local StrategyQuant X installation: list/run/stop projects, "
        "manage databanks, import historical data, monitor builds in real time, "
        "and statically analyze .mq5 / .sqx / .cfx files for risk and configuration issues."
    ),
    lifespan=_lifespan,
)


# Register all tool groups
projects.register(mcp)
databanks.register(mcp)
data.register(mcp)
symbols.register(mcp)
analysis.register(mcp)
monitor_tools.register(mcp)


def main() -> None:
    """Entry point used by the `sq-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
