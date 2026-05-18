# Install

## Prerequisites

- **StrategyQuant X** installed locally. Linux, macOS, or Windows.
- **Python 3.10+** OR `uv` / `uvx` (recommended — `uvx` handles the Python install for you).
- An MCP-capable client (Claude Code, Claude Desktop, Cline, Continue, etc.).

## Path 1 — Claude Code plugin (one command)

```
/plugin marketplace add yourname/sq-mcp
/plugin install sq-mcp@sq-mcp
```

That's it. Claude Code:
1. Pulls this repo,
2. Reads `.mcp.json` and registers the `strategyquant` server (`uvx sq-mcp@latest`),
3. Loads slash commands from `commands/` and subagents from `agents/`.

Verify:
```
/plugin list
```

You should see `sq-mcp` enabled.

## Path 2 — Plain MCP client

Drop this into your client's MCP config (e.g. `~/.claude.json` for Claude Code, `claude_desktop_config.json` for Claude Desktop):

```json
{
  "mcpServers": {
    "strategyquant": {
      "command": "uvx",
      "args": ["sq-mcp@latest"],
      "env": {
        "SQX_HOME": "/home/you/Apps/StrategyQuantX"
      }
    }
  }
}
```

Restart the client. Tools should appear under `mcp__strategyquant__*`.

## Path 3 — From source (development)

```bash
git clone https://github.com/DAVIDAROCA27/sqx-mcp ~/Apps/sqx-mcp
cd ~/Apps/sqx-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Run the server manually to test:
```bash
sq-mcp
# or:
python -m sq_mcp
```

Point your MCP config at the local copy:
```json
{
  "mcpServers": {
    "strategyquant": {
      "command": "/home/you/Apps/sq-mcp/.venv/bin/sq-mcp"
    }
  }
}
```

## Verifying the install

In Claude Code:

```
/sq:analyze-mq5 ~/Downloads/anyStrategy.mq5
```

If the analyzer runs and prints findings without contacting SQ X, the file-only path works. To verify engine-backed tools, ask:

> List all SQ X projects.

Claude should call `mcp__strategyquant__project_list` and return your project names (`Builder`, `Retester`, etc.). First call may take ~5–10s while the JVM warms up.

## Common issues

- **`sqcli not found`** — set `SQX_HOME` env var explicitly. Auto-detect tries `~/Apps/StrategyQuantX`, `/opt/StrategyQuantX`, the standard Windows install dirs, and `/Applications/StrategyQuantX.app/...`.
- **`HTTP API never answered`** — usually means `sqcli -gui` failed to start. Run it manually and watch for license errors. If your install is the "no-check" build, set `SQX_LICENSE_NOCHECK=1`.
- **Tools time out** — first call boots the JVM; allow 10s. If repeat calls also time out, the server lost its subprocess — restart the client.
