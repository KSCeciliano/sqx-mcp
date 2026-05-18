# sq-mcp architecture

> One-pager covering how the modules talk to each other. For tool inventory, see [TOOLS.md](TOOLS.md). For user-facing usage, see [README.md](README.md).

## High-level flow

```
MCP client (Claude Code, etc.)
     │  JSON-RPC over stdio
     ▼
src/sq_mcp/server.py        ← FastMCP bootstrap; per-module register()
     │
     ├── engine.py           ← spawns sqcli (singleton); HTTP client to :5050
     │                         + noise-filtered stdout consumer
     │
     ├── parsers/            ← pure-Python parsers for .mq5 / .sqx / .cfx
     │
     └── tools/<group>.py    ← every tool group has a register(mcp) entry-point
                               that adds @mcp.tool() decorated coroutines.
```

`server.py` calls each module's `register(mcp)` once at startup. Tools share state only through:

- the global `EngineClient` (lifespan-managed),
- the filesystem (`<projects_dir>`, `<data_dir>`),
- the JSON state store at `<projects_dir>/.sq_mcp_state.json` (see `state.py`).

There is **no** in-process global mutable state besides the engine handle.

## Module map

### Engine + lifecycle

| Module | Owns |
|---|---|
| `engine.py` | sqcli subprocess + HTTP client + log tail |
| `config.py` | SQ X install detection (SQX_HOME, $PATH fallback) |
| `tools/projects.py` | project list / start / stop / status / clone / patch (~30 tools) |
| `tools/diagnostics.py` | `health_check` for engine-side state |
| `tools/meta.py` | self-describing catalog, full-environment probe, session_bootstrap, common_workflows |
| `tools/batch.py` | multi-project orchestration (`projects_batch_*`, `workspace_overview`) |

### Static analysis (no engine)

| Module | Owns |
|---|---|
| `parsers/mq5.py` | MQL5 EA risk-rule scanner |
| `parsers/sqx.py` | .sqx fingerprint + MEC sparkline + derived metrics |
| `parsers/cfx.py` | .cfx structure |
| `tools/analysis.py` | `analyze_mq5_file`, `compare_mq5_files`, `inspect_cfx` |
| `tools/audit.py` | strategy-level finding scanner + project structure audit |
| `tools/portfolio_audit.py` | portfolio-level findings (concentration, overfit rate, etc.) |

### Configuration writing (.cfx patchers)

All write tools snapshot first via `projects._make_snapshot`. Atomic via tmp + `os.replace`.

| Module | Owns |
|---|---|
| `tools/cfx_config.py` | fitness, money management, data range, inspect |
| `tools/cfx_advanced.py` | trade caps, SL/PT ranges, genetic options, MaxStrategies, building blocks |
| `tools/cfx_lint.py` | heuristic recommendations (no OOS, undersized population, etc.) |
| `tools/cfx_diff.py` | structural diff between two .cfx files + archetype detection |
| `tools/cfx_templates.py` | capture/list/apply reusable task XMLs |
| `tools/robustness.py` | Walk-Forward / Robustness section patchers |
| `tools/presets.py` | bundled patches: `preset_crypto_24_7`, `preset_recommended_genetic`, `preset_quick_smoke` |
| `tools/advisor.py` | heuristic recommendation tables by goal × asset class |

### Portfolio + analytics

| Module | Owns |
|---|---|
| `tools/portfolio.py` | dedupe, rank, summary, diverse selection |
| `tools/portfolio_risk.py` | distributional stats, Herfindahl concentration, inverse-DD allocation |
| `tools/comparison.py` | A/B test two databanks + Pearson correlation matrix |
| `tools/analytics.py` | combined-equity simulation, monthly returns, Sharpe/Sortino/Calmar |
| `tools/strategy_inspect.py` | per-strategy compare, summarize, equity geometry |
| `tools/databank_partition.py` | read-only metric threshold partitioning |
| `tools/databanks.py` | databank CRUD + filter + promote + merge |
| `tools/regression.py` | snapshot + iteration-over-iteration comparison |

### Reports, visualization, MT5

| Module | Owns |
|---|---|
| `tools/reports.py` | CSV export, Markdown deck (`portfolio_deck_markdown`) |
| `tools/visualize.py` | ASCII Unicode-block sparkline + histogram |
| `tools/mt5.py` | install detect, EA deploy, log tail |
| `tools/mt5_extra.py` | magic-number assignment + source patcher + verify + pack |
| `tools/mt5_reports.py` | MT5 Strategy Tester HTML report parser + diff vs .sqx |
| `tools/pipeline.py` | strategy_export_pipeline + strategy_ready_for_deploy |
| `tools/pipeline_mt5.py` | databank → MT5 pack mega-pipeline |

### Data + integrity

| Module | Owns |
|---|---|
| `tools/data.py` | import/update/export, coverage check |
| `tools/data_quality.py` | bar-density coverage, age check, .dat header diff, workspace audit |
| `tools/symbols.py` | symbol/instrument list, SQLite data.db registry queries |
| `tools/integrity.py` | cross-check registry vs on-disk .dat files |
| `tools/trade_analysis.py` | parse SQ-exported trade CSV → distribution + streak stats |

### Persistence

| Module | Owns |
|---|---|
| `tools/state.py` | flat JSON key/value store at `.sq_mcp_state.json` for cross-session memory |
| `tools/backup.py` | workspace tarball export, manifest, snapshot listing, cleanup |
| `tools/monitor.py` | live build monitoring sessions |

## Patterns

### Patch-then-swap (atomic .cfx writes)

Every `.cfx`-write tool follows this pattern:

```python
snap = _make_snapshot(cfx_path, label="...")
def _patcher(xml_bytes: bytes) -> tuple[bytes, Counter, bool]:
    ...  # idempotent; returns (new_bytes, changes_counter, was_present)
summary, per_file = _apply_to_cfx_buildlike(cfx_path, target_first_only=..., patcher=_patcher)
```

`_apply_to_cfx_buildlike` (in `cfx_config.py`) walks zip members, runs the patcher on every Build/Optimize task XML, writes the new zip to a tmp file, then `os.replace`s the original. A crash mid-write leaves either the original or the new file — never a half-written one.

### Read-only scan helpers

Three core scanners are reused across modules:

- `portfolio._scan_databank(db_dir)` — returns `(metrics_rows, unparseable_rows)`.
- `integrity._index_disk_files(history_dir)` / `_index_registry_rows(rows)` — symbol/TF indices for cross-checks.
- `projects._scan_projects_fs(projects_dir)` — workspace-wide project enumeration.

### Defensive composite ranking

`portfolio._composite_score` is the shared "what's the actual quality of this strategy?" function. It penalizes:

- Overfit (low OOS/IS ratio, capped at 0.5×fitness)
- Deep drawdown (>25% of capital scales fitness down by 25/dd_pct)
- Small samples (<100 trades scales down by trades/100, floor 0.1)

Used by `portfolio_rank` (when mode='defensive'), `pipeline_export_to_mt5`, `analytics.portfolio_combined_equity`.

### Audit/Finding format

`audit.Finding` is the shared dataclass: `code`, `severity` ('critical'/'high'/'medium'/'low'/'info'), `title`, `message`, `suggestion`. The `pipeline._verdict_from_findings` helper rolls findings into a traffic-light verdict ('green'/'yellow'/'red'). Re-used by `strategy_audit`, `portfolio_audit`, `cfx_lint`, `strategy_export_pipeline`.

## Build 143 quirks worked around

Documented in code comments and `CLAUDE.md`:

- `-project action=list` returns "Not implemented" → all listing tools fall back to filesystem scan.
- `-databank action=count` returns "Not implemented" → `databank_count` uses `action=list` + client-side count.
- `-instrument action=list type=*` → parser bug; `instrument_list` falls back to mining `<InstrumentInfo>` from every project's .cfx.
- sqcli does NOT URL-decode `=` or `-`; backslashes break Java path parsing → `engine._encode_cmd` hand-rolls the encoding (spaces=`%20`, backslash→forward slash on Windows).
- Most large outputs go to a stdout file via `>` redirect inside the command, NOT in the HTTP response body.

## Testing

- 558 unit + parser + integration tests, 6 skipped (require live env vars).
- Each new module ships with a `tests/test_<module>_helpers.py` covering its pure helpers (parsers, classifiers, math).
- `scripts/integration_smoke.py` drives every tool category end-to-end against a live SQ X (license-time consuming; run sparingly).

## License

MIT. See [LICENSE](LICENSE).
