# Agent guide — how to drive sq-mcp effectively

For AI agents (Claude Code, etc.) working with this plugin. Reading this once at session-start should be enough to operate the workspace autonomously.

## Mental model

The plugin exposes ~200 tools over MCP. They split into three layers:

```
┌─────────────────────────────────────────────────────────┐
│ HIGH-LEVEL WORKFLOWS (pipeline_*, preset_*, slash cmds) │
├─────────────────────────────────────────────────────────┤
│ SEMANTIC TOOLS (portfolio_*, strategy_*, cfx_set_*)     │
├─────────────────────────────────────────────────────────┤
│ ENGINE PASSTHROUGH (engine_call, raw sqcli)             │
└─────────────────────────────────────────────────────────┘
```

Prefer the highest layer that fits. `engine_call` is an escape hatch — use it only when no typed tool exists.

## First call every session

Run **`session_bootstrap`** (also available as `/sq:bootstrap`). It returns:

- Environment health (SQ X install + sqcli + engine + history dir)
- Workspace summary (project count, total .sqx)
- State-store summary (what we remembered from past sessions)
- Tool-category counts
- A first-call suggestion

If health flags blockers, fix those before doing anything else.

## Discovery — "I want to do X, which tool?"

- **`tool_recommend query="..."`** — natural-language keyword search across tool descriptions.
- **`tools_catalog name_contains="..."` or `description_contains="..."`** — substring filter.
- **`common_workflows`** — curated multi-tool recipes.
- **`explain_finding code=...`** / **`explain_metric metric=...`** — knowledge-base lookups.

When in doubt, ask `tool_recommend` first.

## Safety patterns

### Before any cfx_set_* write

1. `cfx_inspect project=...` — see current settings.
2. `cfx_lint project=...` — get heuristic warnings + suggestions.
3. (Optional) `cfx_compare_against_recommendation` — drift vs advisor recommendation.
4. Apply patches with `cfx_set_*` tools — they snapshot the .cfx first.
5. `workspace_list_snapshots` if you need to roll back.

### Before any project_start

1. `project_precheck project=...` — verifies engine, license, .cfx, data.
2. If blockers appear, fix them BEFORE starting.
3. License is finite — confirm with the user before long runs.

### Before any MT5 deploy

1. `mt5_locate` — verify install detected.
2. `pipeline_export_to_mt5 deploy=False` — preview plan.
3. Confirm with user.
4. Re-run with `deploy=True`.
5. `mt5_verify_deployment` — check files landed.

## Common workflows

### "Audit this project's databank"

```
portfolio_summary → portfolio_audit → portfolio_concentration
  → portfolio_diversity_score → databank_audit_summary
```

If any high/critical finding from portfolio_audit, use `explain_finding code=...` to get remediation steps.

### "Pick the best strategies and ship to MT5"

```
portfolio_select_diverse n=10 → strategy_export_pipeline
  → pipeline_export_to_mt5 deploy=False (plan)
  → confirm with user → deploy=True → mt5_verify_deployment
```

### "Compare two Builder iterations"

```
databank_snapshot_metrics label="before"
[run iteration]
databank_snapshot_metrics label="after"
databank_regression_check baseline=... current=...
```

### "Bootstrap a fresh BTCUSDT Builder"

```
project_clone target=BTC_v1
cfx_set_instrument symbol=BTCUSDT timeframe=H1
preset_crypto_24_7 project=BTC_v1
preset_recommended_genetic project=BTC_v1
cfx_set_data_range date_from=2023.01.01 date_to=...
cfx_lint project=BTC_v1
project_precheck project=BTC_v1
project_start project=BTC_v1 nowait=true
```

## Memory across sessions

The plugin has a JSON state store at `<projects_dir>/.sq_mcp_state.json`:

- **`state_set namespace=... key=... value=...`** — save anything JSON-serializable.
- **`state_get namespace=... key=...`** — recall.
- **`state_list`** — see what's been saved.
- **`state_export`** / **`state_import`** — portable backup.

Recommended namespaces:

- `magic_numbers` — per-account, per-EA magic number registry.
- `session_notes` — free-form notes the agent should remember.
- `preferences` — user preferences ("always block YELLOW too", etc.).
- `last_deploy` — record of the most recent pipeline_export_to_mt5 deploy.

## Read-only vs write tools

**Always read-only:** portfolio_*, strategy_*, audit_*, databank_top_*, data_*, broker_data_*, history_*, mt5_locate / list_experts / log_tail / parse_backtest_report, integrity_*, comparison_*, regression_check, explain_*, tool_*, workspace_overview / fingerprint / find_strategies / search_*, advisor_*, cfx_inspect / lint / archetype / compare_*

**Write tools (use with care, all auto-snapshot the .cfx):** project_start / stop / pause / resume / remove / clone / load_and_start, cfx_set_*, cfx_apply_patch, cfx_template_apply, cfx_toggle_building_blocks, preset_*, databank_save / load / clear / promote / merge / force_sync, data_import / update, instrument_add / delete, mt5_deploy_ea / patch_magic_in_source / strategy_pack, pipeline_export_to_mt5 (with deploy=True), sqx_rename / safe_delete, workspace_export_projects / cleanup_snapshots, state_set / delete / import

When the user gives a vague request like "fix this", ALWAYS confirm before any write tool unless the fix is clearly local and reversible.

## Performance hints

- Most read tools scan filesystem; sub-100ms for small workspaces, may be seconds for many projects.
- Engine HTTP calls (any `project_status`, `databank_list`, etc.) need sqcli running on :5050.
- `_scan_databank` parses every .sqx in a databank → can be slow on huge databanks. Prefer `databank_top_n` for fast-path queries.

## Build 143 known quirks

Hardcoded in CLAUDE.md and worked around in the plugin:

- `-project action=list` → "Not implemented" (filesystem fallback used).
- `-databank action=count` → "Not implemented" (list + client-side count).
- `-instrument action=list type=*` → parser bug (project-cfx mining fallback).
- sqcli doesn't URL-decode `=` or `-`; backslashes need to be forward-slashed.
- Many commands write output to a stdout file, not the HTTP body.

If a tool seems broken in a way that smells like Build 143 quirks, fall back to filesystem scans (the plugin already does this in most places).

## Trial-license discipline

Trial license consumes time on every Builder/Optimizer run. Before kicking off anything that might take > 10 minutes:

1. `license_info` — how much time is left?
2. `health_check` — engine state?
3. Confirm with the user: cost-benefit clear?

For exploratory runs, prefer **`preset_quick_smoke`** (Population=20, Generations=10) over a full run.

## When stuck

1. `engine_watchdog` — is the engine alive?
2. `engine_recent_errors` — what's it complained about?
3. `health_check` — is everything wired up?
4. `workspace_doctor` — exhaustive audit.
5. `engine_call command="-h"` — last resort.
