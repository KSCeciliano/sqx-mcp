# sq-mcp — agent onboarding

Read this once. After 2 minutes you should be productive.

## What this repo is

A Python MCP server that drives **StrategyQuant X** (the algorithmic-trading IDE) through its embedded HTTP API on `:5050`. It is also a Claude Code plugin: tools are surfaced as `mcp__strategyquant__*` calls, and the `commands/` directory ships slash commands like `/sq:morning`, `/sq:promote`, `/sq:compare`.

The plugin layer **adds capabilities SQ X does not have natively** — pure-Python quant tooling (VaR/CVaR, DSR, PBO, risk parity, regime split, brittleness scoring, fingerprinting, walk-forward analysis from arbitrary trade lists, alert rules, lineage tracking, stress testing, calendar effects, Pine Script generation, MT5 .set parsers, etc). Many of those capabilities **replace what SQ Ultimate / QuantAnalyzer PRO charge for**, but they are independent implementations — we do not patch, crack, emulate, or bypass any SQ license check. Refuse any request to do so.

## State at a glance

- Version: **0.4.9** (`pyproject.toml`).
- Tool count: **482** MCP tools across ~110 source modules (see `TOOLS.md` for the live catalog).
- Tests: **1571 passing**, 6 skipped (skipped require live env vars), 2 engine tests fail when the SQ X trial license is expired — that is environmental, not a code bug.
- Lint: `ruff check src/ tests/` returns clean.
- Type-check: `mypy src/sq_mcp/tools/_numerics.py src/sq_mcp/tools/{ratios,tail_risk,stats_extra,overfit_diag}.py` (pure-math leaf modules pass `--strict`; other modules pass default mode). Config in `pyproject.toml`.
- Activate the venv before any Python command: `source .venv/bin/activate`.
- The maintenance playbook (module reorg plan, mypy strictness ladder, profiling with py-spy, PyPy experiment, PyO3 surgical extensions) lives in `MAINTENANCE.md`.

## Directory map (memorize this)

```
sq-mcp/
├── pyproject.toml          # version, deps, ruff/pytest config
├── README.md  CHANGELOG.md  TOOLS.md  EXAMPLES.md  AGENT_GUIDE.md  ARCHITECTURE.md
├── commands/               # Claude Code slash commands (/sq:morning, /sq:promote, …)
├── scripts/
│   ├── regenerate_tools_md.py   # rebuild TOOLS.md from live server registry
│   └── integration_smoke.py     # end-to-end harness against a live SQ X
├── src/sq_mcp/
│   ├── server.py           # FastMCP boot + module registration (touch every time you add a module)
│   ├── engine.py           # async sqcli subprocess + httpx client + retries + URL encoding quirks
│   ├── config.py           # detect_config(): SQ X install paths per OS
│   ├── _validation.py      # resolve_safe_path + name/symbol/date validators
│   ├── parsers/            # .mq5, .sqx, .cfx parsers (no engine call)
│   └── tools/              # every MCP tool lives here, ~one module per topic
└── tests/                  # ~85 test_*_helpers.py files; tests target pure helpers, not MCP layer
```

## Patterns you must follow

**Every tool module looks like this:**
```python
class FooArgs(BaseModel):              # 1. Pydantic schema with field bounds
    items: list[float] = Field(..., min_length=10, max_length=100_000)

def _foo(items: list[float]) -> dict:   # 2. Pure helper — testable without MCP
    ...

def register(mcp: FastMCP) -> None:     # 3. Thin async wrapper that calls the helper
    @mcp.tool(description="...")
    async def foo(args: FooArgs) -> dict:
        return {"ok": True, **_foo(args.items)}
```

Then add `foo.register(mcp)` to `server.py`. Tests live in `tests/test_foo_helpers.py` and call `_foo` directly — never through the MCP layer.

**Hard invariants:**
- Pure Python — no numpy / scipy / pandas (we ship as a tiny standalone package).
- Atomic file writes for any state mutation: write to `path.with_suffix(suffix + ".tmp")` then `os.replace`. See `tools/state.py`, `projects.py::_atomic_write_bytes`, `mt5_extra.py::mt5_patch_magic_in_source`.
- Every file path that comes from a tool argument goes through `_validation.resolve_safe_path()`.
- Every engine call goes through `EngineClient.call()` — never spawn `sqcli` directly.
- Every XML parse goes through `sq_mcp._xml.safe_fromstring` / `safe_parse` — never the bare `etree.fromstring()` (XXE / billion-laughs hardening).
- Math helpers that consume caller floats validate at the boundary via `sq_mcp.tools._numerics.validate_finite_floats` and raise `NumericValidationError`; the MCP wrapper catches it and returns `safe_error_payload(exc)`.
- Exceptions inside tool bodies are caught and rendered via `_common.safe_error_payload(exc)` — never let an MCP tool raise to the transport layer.
- All MCP tool descriptions are user-facing prose — keep them precise and explanatory.

**Don'ts:**
- Don't create planning / decision / status .md files — work from conversation context.
- Don't add backwards-compatibility shims; the project is pre-1.0, breaking changes are fine.
- Don't write multi-paragraph docstrings; one-line top-of-function comments only when the *why* is non-obvious.
- Don't add emojis to source files or commit messages.

## How to run the basic loop

```bash
source .venv/bin/activate
rtk proxy python -m pytest tests/ --ignore=tests/test_engine.py -q   # full suite (~1.5s)
ruff check src/ tests/                                                 # lint
python scripts/regenerate_tools_md.py > TOOLS.md                       # after adding/removing tools
```

`rtk proxy` is needed because the global `rtk` filter swallows pytest output; everything else can run bare.

## SQ X HTTP API — known quirks (Build 143.2708)

- Endpoint is `http://localhost:5050/call?cmd=<urlencoded>` — note `/call?cmd=`, **not** `/?cmd=`.
- sqcli does **not** URL-decode `+` to space or `%3D` to `=`. `engine.py::_encode_cmd` handles this — leave `=`, `-`, `/`, `:` literal.
- Windows paths: replace `\` with `/` before encoding. SQ X accepts forward slashes on every OS.
- Broken endpoints in Build 143: `-project action=list` (Not implemented), `-databank action=count` (Not implemented), `-instrument action=list type=*` (Parameter type missing), `-data action=list / action=info` (Unrecognized action). Use the workarounds in `tools/databanks.py::databank_count` etc.
- Most large results redirect to a stdout file via `>` inside the command — the HTTP body just shows a status line. `_common.is_status_only_response` detects this.

## User profile (durable)

- Spanish-speaking algo trader on Fedora Linux 43. **Always respond in English** unless explicitly told otherwise.
- Primary asset: **BTCUSDT** (crypto perps/spot), working window 2023-01-01 onward (~3 years).
- SQ X at `~/Apps/StrategyQuantX/`, MT5 via Wine in `~/.mt5/`.
- Trial license is finite — confirm before kicking off long Builder/Optimizer runs.
- Confirm before any destructive or shared-state action (deleting projects/databanks, force-pushing, killing in-flight builds).

## The 60-second mental model for adding a tool

1. Decide: is this **pure math** (no engine), **engine-call** (sqcli HTTP), or **file-parser** (read-only on disk)?
2. Pick the matching pattern from any existing module of that type (`ratios.py` for pure math, `databanks.py` for engine, `parsers/sqx.py` + `strategy_inspect.py` for file).
3. Write `FooArgs` (Pydantic, bounded), `_foo()` (pure helper), `register()` (thin async wrapper).
4. Add to `server.py` import block + `register()` call (alphabetical).
5. Write `tests/test_foo_helpers.py` targeting `_foo` directly with at least 3 cases: happy path, edge (empty/extreme), and adversarial (NaN/Inf/negative where positive expected).
6. Run pytest + ruff. Regenerate TOOLS.md.

## When in doubt

- Architecture decisions are documented in `ARCHITECTURE.md`.
- Agent usage patterns (how to chain tools for typical workflows) are in `AGENT_GUIDE.md`.
- End-to-end recipes for a user starting a session are in `EXAMPLES.md`.
- The slash commands in `commands/` are the canonical "high-level UX" surface — when you build a new orchestration tool, consider whether it deserves a `/sq:something` slash command too.
- For any priority/Ultimate-tier SQ X capability the user asks for, check the analogue table in CHANGELOG 0.4.0 — most of it is already implemented natively and just needs to be composed.
