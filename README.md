# sq-mcp

**Native StrategyQuant X integration for Claude Code (and any MCP client).**

Drive backtests, monitor builds in real time, audit MQL5 / .sqx files, and orchestrate portfolios — all from natural language.

```
You:  /sq:analyze-mq5 ~/Downloads/myStrategy.mq5
Claude: 🚨 1 critical risk found: NO_STOP_LOSS on BTCUSDT.
        Strategy uses sqMMFixedAmount but sl=0 — declared per-trade
        risk of $100 is fiction. Add an SL before going live.
```

```
You:  /sq:monitor-build Builder 60 auto-stop
Claude: Started monitor on Builder, polling every 60s, will auto-stop
        on STALLED_GROWTH / LOW_FITNESS / OOS_DEGRADATION.
        [+02m] strats=44 (+44) fit=0.41/0.28 oos/is=0.68
        [+04m] strats=86 (+42) fit=0.39/0.27 oos/is=0.69
        [+06m] strats=87 (+1)  fit=0.39/0.26 oos/is=0.67  ⚠ STALLED_GROWTH
        Project stopped automatically. Builder generated 87 strategies
        in 6 minutes then stalled — your building blocks may be too narrow.
```

## Why

StrategyQuant X is a powerful Java algo-trading platform — but its automation surface (sqcli, HTTP API on `:5050`) is awkward for an LLM to drive directly. This plugin gives Claude a clean, typed, tool-based interface so it can:

- 📊 **Drive SQ X** — list / start / stop projects, manage databanks, import data, export reports.
- 🔬 **Audit strategies before they trade** — parse `.mq5` and `.sqx` files in pure Python; flag the foot-guns (no SL, default magic number, ExitAfterBars=1 on crypto, fixed-amount MM with sl=0).
- 👁️ **Actively supervise long builds** — poll fitness, OOS/IS ratio, generation rate, and abort runs that are wasting cycles.
- 🧠 **Orchestrate** — slash commands and subagents that turn high-level intents (`/sq:test-strategy`, `/sq:walk-forward`, `/sq:portfolio-build`) into multi-step SQ workflows.

## Install

### As a Claude Code plugin (recommended)

```bash
# In Claude Code:
/plugin marketplace add yourname/sq-mcp
/plugin install sq-mcp@sq-mcp
```

After install, the MCP server `strategyquant` is auto-registered and the `/sq:*` commands appear.

### Manually as an MCP server

Add to your `~/.claude.json` (or any MCP-capable client config):

```json
{
  "mcpServers": {
    "strategyquant": {
      "command": "uvx",
      "args": ["sq-mcp@latest"],
      "env": {
        "SQX_HOME": "/path/to/StrategyQuantX",
        "SQX_HTTP_URL": "http://localhost:5050"
      }
    }
  }
}
```

`uvx` will install `sq-mcp` from PyPI on first run. No Python setup required.

### From source

```bash
git clone https://github.com/sq-mcp/sq-mcp
cd sq-mcp
pip install -e ".[dev]"
sq-mcp     # runs the MCP server on stdio
```

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `SQX_HOME` | StrategyQuant X install root (contains `sqcli`) | auto-detected: `~/Apps/StrategyQuantX`, `/opt/StrategyQuantX`, `C:/Program Files/StrategyQuant X`, `/Applications/StrategyQuantX.app/...` |
| `SQX_HTTP_URL` | Base URL of SQ HTTP API | `http://localhost:5050` |
| `SQX_HTTP_PORT` | Port to use when spawning `sqcli` | `5050` |
| `SQX_LICENSE_NOCHECK` | Use `*_nocheck` launcher when present | `1` |

The plugin spawns `sqcli -gui` once at server start and reuses the HTTP API for every tool call — no JVM-startup cost per call.

## Tools

All tools are namespaced `mcp__strategyquant__*` when invoked.

### Projects
`project_list`, `project_start`, `project_stop`, `project_pause`, `project_resume`, `project_status`, `project_inspect_cfx`, `project_config`, `project_remove`

### Databanks
`databank_list`, `databank_count`, `databank_save`, `databank_load`, `databank_clear`, `databank_export`, `sqx_inspect`

### Data
`data_import`, `data_update`, `data_export`, `data_list_local`, `data_timezones`

### Symbols / instruments
`symbol_list`, `instrument_list`, `instrument_add`, `instrument_delete`

### Static analysis (no engine needed)
`analyze_mq5_file`, `compare_mq5_files`, `inspect_cfx`

### Active monitoring
`monitor_start`, `monitor_stop`, `monitor_list`, `monitor_status`, `monitor_alerts`, `monitor_default_rules`

## Slash commands

- `/sq:analyze-mq5 <file.mq5>` — risk audit on one or more EAs.
- `/sq:test-strategy <file.sqx>` — load + retest with default robustness.
- `/sq:walk-forward <project>` — orchestrate a Walk-Forward optimization.
- `/sq:monitor-build <project> [interval] [auto-stop|alert-only]` — supervise a long run.
- `/sq:portfolio-build <folder>` — assemble a diversified portfolio.
- `/sq:data-fix <symbol> [tf]` — diagnose & repair data issues.

## Subagents

- `sq-strategy-reviewer` — second-opinion review with explicit SHIP / NO-SHIP verdict.
- `sq-build-watcher` — babysits long-running builds.
- `sq-data-curator` — keeps historical data clean.
- `sq-portfolio-architect` — designs portfolios from candidate strategies.

## Architecture

```
┌──────────────────────────────────────────────┐
│  Claude Code (or any MCP client)             │
│   ↑ stdio JSON-RPC                           │
│  ┌─────────────────────────────────────────┐ │
│  │  sq-mcp  (FastMCP, Python 3.10+)        │ │
│  │  ┌─────────────┐  ┌─────────────────┐   │ │
│  │  │ tools/*     │  │ parsers/*       │   │ │
│  │  │ (HTTP)      │  │ (.mq5 .sqx .cfx)│   │ │
│  │  └──────┬──────┘  └─────────────────┘   │ │
│  │         │                               │ │
│  │  ┌──────┴──────┐  ┌─────────────────┐   │ │
│  │  │ engine.py   │  │ monitor.py      │   │ │
│  │  │ (httpx →    │  │ (asyncio polls) │   │ │
│  │  │  :5050)     │  │                 │   │ │
│  │  └──────┬──────┘  └─────────────────┘   │ │
│  └─────────┼───────────────────────────────┘ │
│            ↓ subprocess + HTTP               │
│  ┌────────────────────────────────────────┐  │
│  │  sqcli -gui   (JVM, persistent)        │  │
│  │  └ HTTP API on localhost:5050          │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

## Development

```bash
pip install -e ".[dev]"
pytest                  # unit + smoke tests
ruff check src tests    # lint
```

To use the in-development plugin in Claude Code:

```bash
claude --plugin-dir /path/to/sq-mcp
```

## Limitations

- **`.sqx` orders / equity data**: stored as Java-serialized binary. We don't decode them — use the `databank_export` tool (or SQ's own `-tools orderstocsv`) to get CSV.
- **Result Plugin SDK** (Java): SQ X has an in-process plugin API that exposes more than `sqcli`. Out of scope here. A companion Java plugin could relay extra metrics back to this MCP via HTTP.
- **Windows / macOS**: install paths auto-detected; tested primarily on Linux. PRs welcome for platform fixes.

## License

MIT — see [LICENSE](LICENSE).
