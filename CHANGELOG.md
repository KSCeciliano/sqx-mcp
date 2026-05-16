# Changelog

All notable changes to this project will be documented in this file. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.1.0] — initial implementation

### Added

- **MCP server core** (`src/sq_mcp/server.py`) — FastMCP server with lifespan-managed engine.
- **Engine layer** (`engine.py`) — spawns `sqcli -gui` once and keeps it alive; HTTP client to `:5050`; noise-filtered stdout consumer; bounded retries; lock-serialized calls; graceful shutdown.
- **File parsers** (`parsers/mq5.py`, `parsers/sqx.py`, `parsers/cfx.py`) — pure-Python static analysis of MQL5 Expert Advisors, `.sqx` strategy archives and `.cfx` project configs.
- **Risk-rule engine** for MQL5 — detects `NO_STOP_LOSS`, `NO_PROFIT_TARGET`, `VERY_SHORT_HOLD`, `DEFAULT_MAGIC_NUMBER`, `HIGH_MAX_LOTS`, `MM_FIXED_AMOUNT_NO_SL`, `VERIFY_OOS`. Crypto symbols escalate `NO_STOP_LOSS` to `critical`.
- **Active build monitor** (`monitor.py`) — background asyncio task per project that polls fitness, count and status; evaluates `STALLED_GROWTH`, `LOW_MEDIAN_FITNESS`, `OOS_DEGRADATION`, `PROJECT_NOT_RUNNING`; can auto-stop a wasteful build.
- **30+ MCP tools** across `tools/{projects,databanks,data,symbols,analysis,monitor}.py`, each with Pydantic input schemas and structured error returns.
- **Input validation** (`_validation.py`) — strict project / databank / symbol / timeframe / date / path validators run at every tool boundary; blocks argument injection.
- **6 slash commands** (`commands/`): `/sq:analyze-mq5`, `/sq:test-strategy`, `/sq:walk-forward`, `/sq:monitor-build`, `/sq:portfolio-build`, `/sq:data-fix`.
- **4 subagents** (`agents/`): `sq-strategy-reviewer`, `sq-build-watcher`, `sq-data-curator`, `sq-portfolio-architect`.
- **Plugin packaging** — `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.mcp.json` for Claude Code marketplace install.
- **Distribution** — `pyproject.toml` for PyPI publishing; `uvx sq-mcp@latest` install path.
- **Documentation** — `README.md`, `docs/INSTALL.md`, `docs/TOOLS.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md`, `examples/`.
- **Test suite** — 131 tests across 7 files; 125 pass, 6 skipped (require live SQ X / fixture env vars). Includes a synthetic-fixture suite that catches each historical bug.
- **Integration smoke harness** (`scripts/integration_smoke.py`) — drives every tool category end-to-end against a live SQ X.

### Fixed during initial development

- **Bug 1**: `mq5` parser captured `bool LongEntrySignal = false;` declaration instead of the actual rule body. Now scoped to the `// Rule: Trading signals` block.
- **Bug 2**: `mq5` parser flagged false-positive SL/PT presence by picking up assignments in helper code (`sl = HistoryOrderGetDouble(...)`). Now scoped to entry rule blocks only.
- **Bug 3**: engine fired "ready" on early JVM logs, leading to `Error: CLI not ready.` from every subsequent call. Now waits for `HTTP API started` and probes with a real CLI command until it returns valid output.
- **Bug 4**: sqcli HTTP API doesn't decode `+` → space or `%3D` → `=`. Now hand-roll URL with `_encode_cmd()` (only encodes characters that must be encoded for HTTP transit).
- **Bug 5**: `databank action=count` returns `Error: Not implemented.` in Build 143. Now falls back to `databank action=list` + client-side count, both in the `databank_count` tool and the monitor.

### Windows compatibility (0.1.1)

- **Fix W1** — `engine.py`: spawn `sqcli.exe` with `creationflags=CREATE_NO_WINDOW`. Without this flag the JVM pops a visible console window on every server start.
- **Fix W2** — `engine.py` `_encode_cmd()`: convert `\` to `/` before URL-encoding. SQ X is Java-based and accepts forward-slash paths on every OS; leaving native Windows paths literal causes httpx to %5C-encode them, which sqcli does not URL-decode.
- **Fix W3** — `config.py`: `shutil.which()` lookup now uses platform-correct executable name (`sqcli.exe` on Windows). The previous implicit `PATHEXT` lookup worked but was fragile.
- **Doc** — `engine.py` shutdown chain comment: clarified that on Windows `proc.terminate()` and `proc.kill()` both call `TerminateProcess`, so the second escalation is redundant but harmless.
- **Tests** — added 4 new tests in `test_engine_helpers.py` for `_encode_cmd` covering: literal `=` / `-`, `%20` (not `+`) for space, Windows backslash → forward slash, quoted strategy-list round-trip. 129 tests pass, 6 skipped, ruff clean.

### Known limitations

- `.sqx` `orders.bin` and `dailyEquity.bin` are Java-serialized; not parsed externally.
- Result Plugin SDK (Java) is out of scope.
- Linux Fedora 43 verified end-to-end. Windows fixes are code-correct and unit-tested but not yet exercised against a live Windows install — needs a Windows tester to confirm `CREATE_NO_WINDOW` actually suppresses the JVM console and that path interpolation reaches sqcli intact.
- macOS install paths configured but unverified.
- `databank_load` / `data_import` round trips not run end-to-end (would mutate workspace / require external data).
