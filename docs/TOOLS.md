# Tool reference

Every tool returns a dict with `ok: bool` plus tool-specific keys. Errors include `error: str`. Engine-backed tools also include the raw sqcli text in `raw` for debugging.

## Projects

### `project_list()`
List all SQ X projects in the user workspace.

### `project_start(name, only_task?, from_task?, wait?)`
Start a project. By default returns immediately (use `monitor_start` to follow progress). `only_task` runs a single task by 1-indexed number; `from_task` runs from task N onwards.

### `project_stop(name)` / `project_pause(name)` / `project_resume(name)`
Lifecycle controls.

### `project_status(name)`
Current status (running / idle / progress %).

### `project_inspect_cfx(name)`
Read the project's `.cfx` config (tasks, databanks, version) without modifying it.

### `project_config(name, file, action)`
`action="load"` replaces project config from a `.cfx` file; `action="save"` dumps current config.

### `project_remove(name)`
Delete a project. Irreversible.

## Databanks

### `databank_list(project, name?)`
Strategies inside a project's databank (default `Results`).

### `databank_count(project, name?)`
Cheap polling probe — returns `{count: int}`.

### `databank_save(project, folder, name?, strategies?)` / `databank_load(...)`
Move strategies between disk folders and the in-memory databank.

### `databank_clear(project, name?)`
Wipe a databank.

### `databank_export(project, file, name?, view?)`
Dump to CSV or XLSX (extension determines format).

### `sqx_inspect(path)`
Parse a `.sqx` file directly — no engine call. Returns version, fitness IS/OOS, presence of orders / equity blobs.

## Data

### `data_import(symbol, filepath, ...)`
Import historical data from a CSV / MT4 / Amibroker / etc. file.

### `data_update(symbols?)`
Refresh existing symbols from broker source.

### `data_export(symbols, timeframe, output_dir, format?, date_from?, date_to?)`
Export to disk in any of SQ's supported formats.

### `data_list_local()`
Inspect what's on disk under `user/data/History/` — symbols + timeframes available.

### `data_timezones()`
List the timezone identifiers SQ accepts.

## Symbols / instruments

### `symbol_list()`
All data symbols (e.g. `EURUSD_M1_dukas`).

### `instrument_list()`
All configured instruments (with their tick size, point value, etc.).

### `instrument_add(instrument, ...)` / `instrument_delete(instrument)`
Manage instruments. For crypto: pass `data_type=crypto`, real `tick_size` and `commissions`.

## Analysis (no engine needed)

### `analyze_mq5_file(path)`
Static audit of an SQ-generated MQL5 EA. Returns header info, indicators, entry/exit rules, MM, and a list of `RiskFinding`s with severity (`critical` / `high` / `medium` / `low` / `info`) and suggestions.

Built-in rules:
- `NO_STOP_LOSS` — `sl=0` everywhere. Critical for crypto.
- `NO_PROFIT_TARGET` — `pt=0` everywhere.
- `VERY_SHORT_HOLD` — `ExitAfterBars ≤ 1`.
- `DEFAULT_MAGIC_NUMBER` — uses SQ default `11111`.
- `HIGH_MAX_LOTS` — `mmMaxLots ≥ 10`.
- `MM_FIXED_AMOUNT_NO_SL` — fixed-amount MM degenerates without SL.
- `SHORT_BACKTEST` — backtest window may be too narrow.

### `compare_mq5_files(paths)`
Side-by-side comparison. Detects magic-number collisions (running multiple EAs with the same MagicNumber will cross-close trades) and identical backtest windows (curve-fitting risk).

### `inspect_cfx(path)`
Parse a `.cfx` project config file — tasks, databanks, version.

## Active monitoring

### `monitor_start(project, databank?, interval_seconds?, auto_stop_on_critical?)`
Start a background watcher on a running project. Polls databank count, reads new `.sqx` files for fitness stats, evaluates rules.

### `monitor_stop(project)` / `monitor_list()`
Manage sessions.

### `monitor_status(project)`
Last 5 snapshots + last 10 alerts.

### `monitor_alerts(project)`
Full alert history.

### `monitor_default_rules()`
List the built-in rules (`STALLED_GROWTH`, `LOW_MEDIAN_FITNESS`, `OOS_DEGRADATION`, `PROJECT_NOT_RUNNING`) and their thresholds.

When `auto_stop_on_critical=True`, alerts that fire under `STALLED_GROWTH`, `LOW_MEDIAN_FITNESS`, or `OOS_DEGRADATION` will automatically call `project_stop` to abort wasteful runs.
