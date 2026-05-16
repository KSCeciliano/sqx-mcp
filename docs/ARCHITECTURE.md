# Architecture

## Goals

1. **Native MCP** — every operation that makes sense over MCP is a typed tool with a Pydantic schema, not a raw shell pass-through.
2. **One JVM, many calls** — pay the StrategyQuant X start cost once per server lifetime, not once per tool call.
3. **Useful before SQ X starts** — the file-parsing tools (`analyze_mq5_file`, `sqx_inspect`, `inspect_cfx`) work without the engine, so the plugin gives value the moment a strategy file lands on disk.
4. **Active, not passive** — the monitor module isn't just a status reader, it can stop a wasteful build.
5. **Anyone can install** — `uvx`-distributed, no Python toolchain required from end users.

## Process model

```
┌──────────────────────────┐
│ MCP client (Claude Code) │
└──────────────┬───────────┘
               │ stdio (JSON-RPC)
               ▼
┌──────────────────────────────────────────┐
│ sq-mcp (Python, FastMCP)                 │
│  - lifespan: spawn sqcli, await ready    │
│  - per tool call: HTTP /call?cmd=...     │
│  - background tasks: monitor pollers     │
└──────────────┬───────────────────────────┘
               │ subprocess (stdout merged with stderr,
               │  noise filtered) + httpx -> :5050
               ▼
┌──────────────────────────────────────────┐
│ sqcli -gui (Java)                        │
│  - HTTP CLI on :5050                     │
│  - GUI on :8080 (unused)                 │
└──────────────────────────────────────────┘
```

## Components

### `sq_mcp.config.SqConfig`
Resolves install paths (`SQX_HOME`, platform defaults), HTTP URL, license-check toggle. Frozen dataclass.

### `sq_mcp.engine.EngineClient`
Owns the `sqcli` subprocess and an `httpx.AsyncClient`. `start()` spawns and waits for the "HTTP API started" log line, then probes the endpoint. `call(cmd, **params)` issues `GET /call?cmd=...` and strips known noise patterns. `stop()` is graceful with kill fallback.

### `sq_mcp.parsers.*`
- `mq5.analyze_mq5(path)` — regex-based static analysis of SQ-generated MQL5. Builds an `Mq5Strategy` object then runs `_run_rules` to populate findings.
- `sqx.parse_sqx(path)` — reads `.sqx` ZIP, parses `settings.xml` via lxml, extracts fitness attributes.
- `cfx.parse_cfx(path)` — reads `.cfx` ZIP, parses `config.xml`, lists tasks + databanks.

All parsers are pure-Python and engine-free.

### `sq_mcp.monitor.MonitorManager`
Holds a dict of `MonitorSession`s. Each session runs an asyncio task that, on every interval:
1. `databank count` (cheap)
2. `project status`
3. parses up to 10 most-recent `.sqx` files in the databank folder for fitness
4. evaluates rules, records alerts
5. takes the configured action (`alert_only` / `pause_project` / `stop_project`)

Rules are user-extensible via the `MonitorRule` dataclass.

### `sq_mcp.tools.*`
Each module exposes a `register(mcp)` function decorated with `@mcp.tool`. Pydantic models for inputs make schemas auto-generated and inputs validated before they ever hit the engine.

### `sq_mcp.server`
`FastMCP("strategyquant", lifespan=_lifespan)`. The lifespan boots the engine, yields it as the lifespan context (so every tool can grab it via `ctx.request_context.lifespan_context`), and tears down monitor sessions before the engine on shutdown.

## Why ZIP-parsing the file formats matters

`.sqx` and `.cfx` are JAR/ZIP files. The XML inside is well-formed and stable across SQ X builds (the binary parts — `orders.bin`, `dailyEquity.bin` — are Java-serialized and version-coupled, so we leave those to `sqcli -tools`).

This means:
- **Fitness IS/OOS** can be read in milliseconds without booting SQ X — useful for the monitor's polling loop, where we sample 10 recent `.sqx` files per cycle.
- **Project structure** can be inspected before the user makes a destructive change (`project_inspect_cfx` is read-only).

## Why a custom monitor instead of relying on SQ's UI

SQ X's GUI shows progress, but you can't query it programmatically and you can't trigger stop-rules from it. The monitor closes that gap: same data SQ shows you, but as a background task an LLM can act on. The killer use case is the unattended overnight run that's been generating curve-fit garbage for 4 hours — `auto_stop_on_critical=True` saves you the wasted CPU and the next-morning disappointment.
