# sq-mcp tool catalog

482 MCP tools, grouped by heuristic category. Generated from the live server.

All tool names are namespaced `mcp__strategyquant__*` when called from an MCP client.

## audit (7)

- **analyze_mq5_file** — Analyze a SQ-generated .mq5 Expert Advisor: extracts symbol/TF, indicators, entry/exit rules, MM, and runs a risk audit (no SL, default magic number, ExitAfterBars=1 on low TF, etc.). Use this BEFORE importing or running.
- **batch_audit_databank** — Audit every .sqx in a databank against the promotion gates for the given risk_profile. Returns ranked buckets: ready / marginal / blocked, each with the metrics + reasons. Read-only — does not promote anything.
- **batch_audit_summary_markdown** — Format a batch_audit_databank result as a one-page Markdown report with sections for ready / marginal / blocked strategies and counts.
- **compare_mq5_files** — Compare two or more .mq5 strategies side-by-side: indicators, magic numbers, time ranges, MM. Flags conflicts (same magic, identical backtest window).
- **notify_format_audit_summary** — Format an audit-findings summary as Slack/Discord markdown. Counts by severity, lists the top 10 worst findings, returns a single block of text. Pure formatting.
- **pre_live_checklist** — Pre-live deployment checklist for a .sqx strategy. Verifies: OOS sample exists and is non-zero (configurable), trade count >= require_min_trades, drawdown_pct <= max_drawdown_pct, profit_to_dd_ratio >= min_profit_to_dd, HistoryTo is not old...
- **workspace_audit** — Workspace-wide audit. Runs project_anomaly_check on every project AND strategy_anomaly_check on the N most recent .sqx files in each Results databank. Returns a ranked roll-up plus per-project / per-strategy details. Use this as a 'morning ...

## cfx_config (33)

- **cfx_active_blocks** — Quick summary of which Builder blocks are currently ENABLED in a project. Returns the count by category and a flat list of enabled block keys. Use as a faster discovery step than cfx_list_building_blocks (which returns every block, enabled ...
- **cfx_apply_patch** — Patch an existing project's .cfx in-place — rewrite symbol / timeframe / dateFrom / dateTo across all task XMLs without cloning. Takes an auto-snapshot first by default (rollback via the .cfx.bak.* file). Use this to retarget an active proj...
- **cfx_archetype** — Classify a .cfx by what kind of task XMLs it contains: builder, optimizer, retester, walkforward, montecarlo, or a combo. Reports the primary archetype + per-kind task counts. Use this when picking which downstream tool applies. Read-only.
- **cfx_compare_against_recommendation** — Compare an existing project's current cfx settings against advisor recommendations for the given goal × asset_class. Returns per-field drift: which recommended settings differ from current. Read-only.
- **cfx_compare_paths** — Same as cfx_compare_projects but takes raw file paths so you can compare arbitrary .cfx files (e.g. a current project against a snapshot). Read-only.
- **cfx_compare_projects** — Compare two project.cfx files structurally: zip-member diff (files added/removed/changed-size), curated field diff on the first Build/Optimize task XML (fitness, MM, data range, max strategies, capital), and a coarse XML-path diff. Read-onl...
- **cfx_configure_robustness** — Enable/disable Monte Carlo and other robustness CrossChecks on a project's Retest tasks. Pass an `enable` map e.g. {'MonteCarloRetest': true, 'MonteCarloManipulation': true, 'WalkForwardOptimization': true, 'WhatIf': false}. Recognized: Ret...
- **cfx_configure_walkforward** — Configure Walk-Forward analysis on a project's Retest tasks. Toggles the <WalkForwardOptimization> CrossCheck on/off and tunes its mode (anchored / rolling), IS period (`optimization`), and OOS period (`period`). Operates on the Retest-Task...
- **cfx_inspect** — Read-only inspection of project.cfx config: fitness criterion, money management (active method + params + initial capital), data setup date ranges, OOS settings, and the embedded Symbols list. Use this before calling cfx_set_* to know the c...
- **cfx_inspect_robustness** — Read-only report of the current Walk-Forward + CrossCheck configuration of every Retest task XML in a project. Returns per-task: WalkForwardOptimization use/type/period/optimization, plus the use= flag for every other known CrossCheck block...
- **cfx_lint** — Heuristic lint of a .cfx Builder/Optimizer config — flags missing OOS holdout, undersized populations, inverted date ranges, fixed-size MM that hides risk-of-ruin, etc. Each finding includes a suggested next tool call. Read-only.
- **cfx_list_building_blocks** — Read-only listing of every <Block> in the Builder XML, with its key, category, weight, and current use=true|false. Use this to discover what's available before calling cfx_toggle_building_blocks.
- **cfx_recommend_for_symbol** — Infer asset_class from a symbol name (BTCUSDT → crypto, EURUSD → forex, ES → futures) and return cfx_recommend_settings for that class plus the given goal. Read-only.
- **cfx_recommend_settings** — Return a recommended bundle of cfx settings for a given goal (yield / risk_min / balanced / smoke) and asset_class (crypto / forex / futures / equities / unknown). Pure heuristic — the actual application is up to the agent via cfx_set_* too...
- **cfx_set_data_range** — Update the Data/Setup dateFrom / dateTo on a project's Build/Optimize task XMLs. Dates must be yyyy.MM.dd. Either one can be omitted to leave it unchanged. NOTE: this updates the Setup-level date range; the <Symbol> dateFrom/dateTo (epoch m...
- **cfx_set_fitness_criterion** — Set the fitness ranking criterion on a project's Build/Optimize tasks inside project.cfx. Common ranking types: ReturnDDRatio, NetProfit, ProfitFactor, SQNScore, Stability, Sharpe, ReturnDownsideRatio. Optionally override the FitnessCriteri...
- **cfx_set_genetic_options** — Patch the genetic-evolution options in <BuildMode>: PopulationSize, MaxGenerations, IS/OOS ratio (EvoInSamplePeriod@ratio), crossover/mutation probabilities. Snapshots the .cfx first.
- **cfx_set_instrument** — Retarget a project to a different instrument by rewriting every `<Symbol>` and `<InstrumentInfo>` block from the SQLite data.db registry. Use this to convert a forex-Dukascopy project into a crypto/Binance project (or any cross-asset migrat...
- **cfx_set_max_strategies** — Patch <Rankings>/<MaxStrategies> (and StopCondition passedStrategies by default) so the Builder caps the databank at the requested count. Snapshots the .cfx first.
- **cfx_set_money_management** — Activate a Money Management method on a project's Build/Optimize tasks, and optionally update its parameters and the initial capital. Sets use='true' on the chosen <Method> and use='false' on all others. Common method types: FixedSize (para...
- **cfx_set_setup_attrs** — Patch Setup-level attributes on <Data>/<Setups>/<Setup ...>: session, slippage, min_dist, engine, plus the inner <Chart> element's spread, and the Setup's testPrecision. Snapshots .cfx first.
- **cfx_set_sl_pt_range** — Patch <SLPTOptions> values: min/max stop-loss and profit-target in pips or percent, plus the SLRequired / PTRequired flags. Snapshots the .cfx first; only touches values that already exist.
- **cfx_set_trade_caps** — Patch <BuildTradingOptions> params: max-trades-per-day, exit-at-end-of-day, exit-on-friday, and the LimitTimeRange filter. Snapshots the .cfx first; only touches params that already exist.
- **cfx_template_apply** — Apply a saved CFX template to an existing project: replace each matching task XML inside the .cfx with the template's version. Snapshots .cfx first. Template files that don't have a matching slot in the target .cfx are listed as 'unused' (n...
- **cfx_template_capture** — Capture an existing project's task XMLs (Build-/Optimize-/Retest-/etc.) into a named template folder under <projects_dir>/_cfx_templates/. Useful when you've tuned a project's settings and want to reuse them for a new symbol. Templates pers...
- **cfx_template_delete** — Delete a saved CFX template. Irreversible — removes the folder under <projects_dir>/_cfx_templates/<name>/.
- **cfx_template_list** — List every saved CFX template under <projects_dir>/_cfx_templates/. Returns name, task-XML count, captured-at timestamp.
- **cfx_toggle_building_blocks** — Enable or disable <Block> entries in the Builder XML by key prefix and/or category (e.g. category='signals' to flip every indicator block). Snapshots the .cfx first. Returns how many blocks matched and were flipped.
- **cfx_validate** — Structural validation of a .cfx archive. Reports the project name and version, every task with an exists flag, missing files referenced by config.xml, orphan task XMLs present in the zip but not referenced, and malformed XML. Pass either `p...
- **inspect_cfx** — Inspect a .cfx project config file (tasks, databanks, version) without loading SQ X.
- **preset_crypto_24_7** — Apply a crypto-24/7 preset to a project's first Build/Optimize task: disable ExitOnFriday + LimitTimeRange, switch SLPT to percent-based (MinSLInPercent=1.0 / MaxSLInPercent=10.0). Snapshots the .cfx first. Pair with cfx_set_data_range to s...
- **preset_quick_smoke** — Apply minimal smoke-test defaults: PopulationSize=20, MaxGenerations=10, MaxStrategies=20. Use to verify a project actually runs before committing trial-license time to a full search. Snapshots .cfx.
- **preset_recommended_genetic** — Apply 'real GA run' defaults to a project: PopulationSize=100, MaxGenerations=100, EvoInSamplePeriod ratio=50 (50% OOS holdout), MaxStrategies=1000 with matching StopCondition. Snapshots .cfx.

## data (20)

- **broker_data_integrity** — Cross-check the SQ X SQLite data registry (data.db DATA table) against the on-disk History/<symbol>/*.dat files. Reports: pairs (symbol/TF) present in both, on disk only (registry forgot to record it), and in registry only (SQ thinks data e...
- **data_age_check** — Flag .dat history files whose mtime is older than max_age_days. Stale files mean the data hasn't been refreshed (and a recent backtest may be testing against year-old data). Read-only.
- **data_bar_density** — Estimate bar density for a (symbol, timeframe) pair: expected bars for the registry date window vs the actual bar count derived from .dat file size. Coverage ratios well under 1.0 signal a partial fetch; ratios over ~1.05 signal an over-siz...
- **data_coverage_check** — Verify that local data actually covers a requested backtest window. Three-way cross-check: .dat file presence under history/, data.db DATA table date range, and the requested window. Two modes: pass `project=` to mine referenced symbols/dat...
- **data_dat_header_diff** — Compare two .dat history files: header strings, file size, header byte offset, and estimated bar count. Useful for verifying that an incremental data update actually appended bars. Read-only.
- **data_export** — Export data to CSV / MT format / etc.
- **data_health_bar_count_check** — Compare actual bar count to expected over a date window. Accounts for market hours (e.g. forex ~24/5 → market_hours_pct=0.714). Returns coverage_pct + verdict (complete/good/patchy/sparse).
- **data_health_freshness_gate** — Freshness gate: pass/fail based on whether the most recent bar is newer than max_age_minutes. Use as a pre-trade check or before running a fresh-data backtest.
- **data_health_gap_report** — Detect gaps in a timestamp series relative to an expected bar interval. Reports gap count, missing bar count, and the 10 largest gaps. Caller supplies timestamps as epoch seconds and the expected interval in seconds (e.g. 60 for M1, 3600 fo...
- **data_health_monotonicity** — Verify timestamps are strictly increasing — flags duplicates and out-of-order entries. Returns the index of the first 10 anomalies.
- **data_import** — Import historical data into SQ X from a file.
- **data_list_local** — List symbols + timeframes already present in the local SQ X data folder.
- **data_registry_lookup** — Look up authoritative metadata for a symbol/timeframe combo from SQ X's SQLite registry (`data.db`). Returns DATEFROM/DATETO (as both epoch ms and ISO), ROW count, BROKER_ID, SOURCE, USYMBOL, etc. — the ground-truth metadata for what data i...
- **data_timezones** — List available timezones for data import.
- **data_update** — Update existing data symbols (refresh from broker / data source).
- **data_workspace_quality_report** — One-call workspace data quality audit: iterates every (symbol, TF) in the data.db registry, computes bar-density coverage, flags stale (>=30 days) and orphaned files. Aggregates issues by code. Slow on huge workspaces — runs O(symbols × TFs...
- **history_data_summary** — Summarize the local .dat history files for one or every symbol under <data_dir>/History/. Reports file size, mtime, header magic strings (Java DataOutputStream-style writeUTF prefix), and an estimated bar count derived from file size. Pure ...
- **history_disk_inventory** — Filesystem inventory of every History/ symbol directory: counts, total bytes, timeframes present. Read-only. Useful right before disk-cleanup or before a Binance native pull, so the agent knows what's already on disk.
- **symbol_data_summary** — Single-symbol deep dive: all timeframes on disk with per-file size, header strings (best-effort UTF parse), and age. Read-only.
- **workflow_data_health_check** — End-to-end data health workflow: apply freshness + bar-count + gap-count gates to a symbol+timeframe's caller-provided data snapshot. Returns a single overall verdict (healthy / stale / patchy / missing). Read-only — does NOT trigger a data...

## databanks (20)

- **databank_audit_summary** — Aggregate strategy-level audit findings across every strategy in a databank: counts by severity, top-N most common finding codes, which strategies have the most findings. Use to triage at the databank level before drilling into individual s...
- **databank_clear** — Clear all strategies from a databank.
- **databank_correlation_matrix** — Pearson correlation matrix on the equity curves of the top-N strategies in a databank. Pre-ranks by rank_mode (defensive composite by default), parses MEC sparklines from each .sqx, converts to per-step returns, and flags pairs whose |corr|...
- **databank_count** — Count strategies in a databank — useful as a cheap polling probe during long builds.
- **databank_export** — Export a databank to CSV or XLSX (extension determines format).
- **databank_filter** — Filter strategies in a databank by performance/robustness criteria. Reads .sqx files directly (no engine call) and returns the subset that satisfies every supplied threshold, sorted by the chosen metric. Returns BOTH the survivors and a per...
- **databank_force_sync** — Force-sync a databank from JVM memory to disk. CRITICAL for monitoring: most SQ X project databanks default to `syncType=Auto-sync never` which means new strategies live in JVM memory and never hit disk on their own. Without this call, your...
- **databank_list** — List strategies inside a project's databank (default name: Results). When the engine reply is the status-only marker 'Databanks listed.' (Build 143 redirects the actual list to a file), falls back to a filesystem scan of the project's datab...
- **databank_load** — Load .sqx files from a folder into a databank.
- **databank_merge** — Merge .sqx files from multiple source databanks into a single destination databank. Dedupes by Fingerprint trades_hash (first source wins on conflict). Useful for combining results from parallel Builder runs or pooling survivors from severa...
- **databank_metric_correlation** — Compute the Pearson correlation between two metrics across every strategy in a databank. Use to answer 'does higher fitness usually mean higher drawdown?' (positive correlation) or 'is profit-to-DD independent of trade count?' (near zero). ...
- **databank_metric_histogram** — ASCII histogram of a metric across every strategy in a databank (default: fitness_oos). Returns the bin counts AND a Markdown-friendly text rendering with horizontal bars. Read-only.
- **databank_partition** — Read-only partition of a databank by metric ≥ threshold. Returns three buckets: above (qualifies), below (drops out), missing (metric not available). Use this to answer 'how many strategies in this databank have OOS/IS ratio >= 0.5?' withou...
- **databank_promote** — Promote top-N strategies from one databank to another by copying .sqx files. Optional pre-filter on trades / drawdown_pct / profit_to_dd / fitness_oos / oos_is_ratio. Default sort is profit_to_dd_ratio descending (a robust ranking that pena...
- **databank_regression_check** — Compare a databank's current state against a previous snapshot to detect regression. Matches strategies by trades_hash (preferred — stable across re-imports) or by relative file path. Reports per-strategy classification (improved / regresse...
- **databank_save** — Save databank contents to a folder under the project's databanks/.
- **databank_snapshot_metrics** — Snapshot a databank's per-strategy metrics to a JSON file. Captures trades, fitness IS/OOS, drawdown, profit/DD ratio, OOS-IS ratio, and the trades_hash + fingerprint identity. Use this AFTER a builder run finishes — it's the baseline for d...
- **databank_top_bottom** — Return the top-K and bottom-K strategies of a databank by metric in a single call. Use for 'show me the best and worst' triage. Read-only.
- **databank_top_n** — Rank strategies inside a project's databank folder by IS / OOS / full fitness. Reads .sqx files directly from disk (no engine call) and returns the top N. Useful for quickly inspecting a finished build without round-tripping through sqcli.
- **walkforward_databank_summary** — Walk-forward summary from a databank of WF folds. Each .sqx in the databank is treated as one fold. Returns per-fold IS/OOS values plus aggregates (mean, std, min, max) and walk-forward efficiency (mean OOS / mean IS). metric='fitness' look...

## engine (12)

- **engine_call** — Escape-hatch passthrough to sqcli's HTTP API. Pass any command (e.g. '-project action=status name=Builder') and get the raw engine response. Use only when no typed tool covers your need — typed tools validate args and decode SQ-specific odd...
- **engine_help** — Query sqcli's built-in help. Omit `command` for the top-level command list; pass `command='project'` (etc.) for action/parameter details. The engine truncates large help text at ~1KB and appends '(N more lines, M bytes total)' — we surface ...
- **engine_log_export** — Dump the engine's bounded log tail to a file path. Useful when you want to share or grep through more than what fits in a JSON response. Up to ~200 lines (the in-memory ring buffer size).
- **engine_log_stream** — Stream the engine log for a bounded duration. Polls the buffered log every poll_interval_seconds and emits an MCP `info` notification each time new lines appear (so attached clients see incremental updates). Also extracts Builder/Optimizer ...
- **engine_log_tail** — Return the last N lines of the sqcli engine's stdout log (already noise-filtered). Useful for debugging why a project_start failed or what the engine was doing at a given time.
- **engine_recent_errors** — Surface the most recent fatal/error/exception lines from the engine's log tail. Useful right after a failed Builder run or an HTTP timeout to see what the engine said about it.
- **engine_start** — Start the sqcli engine. First tries to attach to an already-running sqcli on the configured HTTP port (no spawn). Only spawns a new subprocess if nothing is listening. Reports which mode was used.
- **engine_status** — Report engine state: running, attached/spawned, http_url, pid (None for attached), log path being tailed (if any), recent log line count, fatal error.
- **engine_stop** — Stop the sqcli engine. If the plugin spawned the subprocess: sends `-exit`, then SIGTERM, then SIGKILL. If the engine was attached (externally spawned): leaves the external process alone, only detaches the plugin from it.
- **engine_watchdog** — Watchdog probe for the sqcli engine. Combines: process liveness, HTTP responsiveness, log file mtime. Returns one coarse state — 'responsive', 'quiet', 'unresponsive', or 'dead' — plus the raw signal data. Use during long Builder runs to de...
- **license_info** — Query the engine for license info: product/build, license type (Trial / Subscription / Permanent), expiry date, license code, computed days_remaining. Use this BEFORE kicking off a long Builder run so you know if the trial will expire mid-r...
- **task_progress** — Scan the buffered sqcli log tail for Builder/Optimizer progress markers (Built N strategies, Generation N/M, Backtest N/M, Best fitness, Progress %, task/project lifecycle events). Returns the list of detected events plus a `latest` dict ke...

## meta (29)

- **agent_init_prompt** — Return a concise system-prompt fragment teaching an AI agent how to use this plugin effectively (principles, slash commands, safety rules). Use at session start to bootstrap the agent's understanding.
- **alert_workspace_defaults** — Return a curated default rule set for a typical algo workspace: extreme drawdown, drift red/yellow, weak OOS, low trades, stalled build, very-brittle. Use as a starting point; customize the thresholds for your tolerance.
- **calendar_session_split** — Split trades into named sessions (asia/europe/us, or custom). Each session is (name, start_hour, end_hour). Sessions wrapping midnight are supported. Trades outside any session land in 'uncategorized'.
- **common_workflows** — Curated list of common multi-tool workflows: name, what-it-does, and the tool sequence. Returns workflows like 'Build a new portfolio from scratch', 'Audit + ship to MT5', 'Regression-check after a rebuild', 'Recover a corrupted .cfx'. Use ...
- **env_path_tools_check** — List which shell command-line tools are visible on $PATH that this MCP plugin might depend on. Returns presence + resolved path for common helpers (curl, unzip, sqlite3, etc.). Read-only.
- **environment_health_check** — End-to-end environment health probe. Checks SQ X install, sqcli binary, engine HTTP port reachability, data.db readability, history dir presence, projects dir, MT5 install detection. Returns a 'healthy' boolean and a per-check breakdown. Qu...
- **explain_finding** — Explain an audit finding code in plain English: what it means, why SQ flags it, and concrete tool calls to fix it. Returns 'unknown_code' if the code isn't in the knowledge base.
- **explain_metric** — Explain a derive_metrics field in plain English: what it measures, good vs bad ranges. Use to teach an agent what each metric means.
- **health_check** — One-call health rollup: engine running/attached/pid, license info + days_remaining, data registry presence, project count on disk, recent fatal-error tail. Useful as the very first step of an autonomous run so the agent knows what's availab...
- **promotion_explain_failure** — Explain why a promotion was blocked in human-readable form. Returns a multi-line markdown bullet list of failures.
- **session_bootstrap** — Single-call orientation for an agent starting a new session. Bundles: environment health probe, workspace overview, recent state-store entries, common-workflow recipes, and a tool count by category. Use this as the first call so the agent k...
- **tool_recommend** — Find tools matching a free-form natural-language query. Splits the query into keywords, scores each tool by how many keywords appear in its name + description, returns the top-N matches. Use when you know what you want to do but not which t...
- **tools_catalog** — Self-describing catalog of every tool this MCP server exposes. Returns name + description + heuristic category for each tool. Optionally filter by substring match on name or description. Free / no I/O — use to discover available capabilitie...
- **version_info** — Plugin version + Python version + key dependency versions. Useful for support / bug reports. Read-only.
- **workspace_archive_strategies** — Zip a list of strategy files into one archive. dry_run defaults to true. Atomic write — archive is written to a .tmp file and renamed on success.
- **workspace_batch_rename** — Batch-rename files matching a glob in a directory, using a regex pattern → replacement. dry_run defaults to true. Refuses if any rename would collide with an existing file.
- **workspace_cleanup** — Bulk-remove projects matching a glob pattern (e.g. 'TEST_*', '*_AUTO_*'). Defaults to dry_run=True for safety. Returns a token in dry-run mode that must be passed back as confirm_token to actually delete. Uses force-remove ordering: -projec...
- **workspace_clone_strategy** — Clone a .sqx file to a new name in the same directory. dry_run (default true) reports what would happen without writing. overwrite=true allows replacing an existing file.
- **workspace_diff_manifests** — Compare two workspace manifest JSON files (produced by workspace_export_manifest) and report what changed: added / removed projects, mtime changes per project, databank count deltas. Read-only.
- **workspace_disk_usage** — Disk-usage report for the SQ X user workspace. Per-project: project.cfx size, databanks/ total bytes, .sqx file count, snapshot count, last-modified time. Plus history/ size summary. Use to find disk-hogs before cleanup.
- **workspace_doctor** — One-call deep audit of the whole workspace: environment health, broker data integrity, stale data files, per-project cfx_lint, per-project portfolio_audit, and a workspace fingerprint. Returns blockers list + raw per-check results.
- **workspace_export_manifest** — Emit a workspace manifest as JSON: every project, cfx mtime + size + SHA-256, and databank sqx counts. Lightweight 'state fingerprint' without archiving anything. If output_path is set, also writes the JSON to disk. Read-only.
- **workspace_export_projects** — Make a tar.gz backup of project configs (and optionally databanks). Writes to <projects_dir>/_backups/ (or a custom output_dir) and emits a manifest.json inside the archive. Excludes data/History by default — too big to bundle. Returns the ...
- **workspace_find_strategies** — Scan every project's databanks and return strategies matching the given filters (symbol/timeframe/min_trades). Use this when looking for 'all my BTCUSDT survivors' or 'every strategy with >100 trades' across the workspace. Read-only.
- **workspace_fingerprint** — Compute a single 16-character fingerprint of the workspace state (every project's cfx SHA-256 + databank sqx counts). Use this at the start of a session to detect 'did anything change since last time?'. Read-only.
- **workspace_insights** — Workspace insights: top/worst strategies by profit_to_dd_ratio across every project, symbol/timeframe distribution, total strategy count, license outlook (if engine is up), disk usage roll-up. One call to get the 'state of my trading worksh...
- **workspace_overview** — Workspace overview: counts of projects, databanks, and .sqx files. Includes a 'most_recently_modified' list (top 10 by cfx mtime). Cheap one-call dashboard. Read-only.
- **workspace_restore_archive** — Restore strategies from a workspace archive (.zip). Refuses to overwrite existing files unless overwrite_existing=true. Strips path traversal attempts from archive members.
- **workspace_summary_markdown** — Generate a Markdown summary of the entire workspace: every project, its task XML count, databanks, total .sqx files. Use to publish or share the current workspace state. Optional output_path writes to disk.

## misc (222)

- **alert_format_summary** — Format a list of triggered alerts as a Slack/Discord-friendly Markdown block, sorted critical → info.
- **alert_rule_evaluate** — Evaluate a single alert rule against a metric payload. Rule is (metric_key, op, threshold, severity, message). metric_key supports dotted paths into nested dicts. Returns triggered True/False + details.
- **alert_rules_batch** — Evaluate a list of alert rules against one payload. Returns the triggered alerts sorted, plus per-severity counts.
- **annual_report_compose** — Compose a full markdown annual/period report from caller-supplied sections: portfolio summary, top strategies, drift outcomes, recent activity, free-form notes. Returns the markdown text. The caller writes it to a file.
- **annual_report_section_drift_summary** — Format a 'drift summary' section: counts by verdict, highlighted red/yellow strategy lists. Returns markdown only.
- **annual_report_section_recent_activity** — Format a 'recent activity' table (when / what / details). Sorted newest-first. Returns markdown table only.
- **annual_report_section_top_strategies** — Format a 'top strategies' table from a list of strategy dicts (rank, name, fitness, drawdown_pct, trades, profit_factor, tags). Returns markdown table only.
- **attribution_by_month_from_curves** — Bucket each strategy's equity curve into n_buckets equal-length segments (typically 'months') and report per-bucket returns. Also computes weighted portfolio bucket returns and identifies best/worst buckets. Pure math.
- **attribution_by_strategy** — Attribute total portfolio return to each strategy: contribution = weight × return. Returns per-strategy contributions, share of total, and the portfolio total. Use as a starting point for any breakdown report. Pure math.
- **attribution_concentration_index** — Herfindahl-Hirschman index on |contribution|. 100 = single strategy did all the work; 0 = perfectly distributed across sources. Returns raw, normalized, percentage, and a plain-English verdict.
- **attribution_regime_split** — Split buckets into calm vs volatile (|portfolio return| > volatility_threshold_std). Report each strategy's mean and total return in each regime. Use to spot strategies that only work in one regime.
- **bar_align_to_session** — Align intraday bars to daily session boundaries starting at session_start_hour_utc (default 0). Returns one bar per session with full OHLCV. Drops the first partial bar if it doesn't land on a session boundary.
- **bar_construct_dollar** — Dollar bars: emit a bar every time cumulative price·volume exceeds dollar_per_bar. Most stable across price regimes — a $1M bar is the same idea whether BTC is at $20k or $80k. Recommended for crypto.
- **bar_construct_imbalance** — Imbalance bars: emit a bar whenever cumulative signed-volume imbalance (buy − sell) exceeds threshold. Bar reports the imbalance direction and magnitude. AFML Ch.2 'information bars'.
- **bar_construct_tick** — Tick bars: group consecutive ticks into bars of N ticks each. Returns OHLC+volume+timestamp per bar. Useful when order-arrival rate matters more than wall-clock time.
- **bar_construct_volume** — Volume bars: emit a bar every time cumulative traded volume exceeds volume_per_bar. Captures liquidity-driven sampling — busier periods produce more bars.
- **bar_convert_timeframe** — Convert between conventional timeframe strings (M1 / M5 / M15 / M30 / H1 / H4 / D1 / W1). Returns the bars_per_target_bar compression ratio. Fails if the target is not an integer multiple of the source.
- **bar_resample_to_interval** — Resample OHLCV bars to a new interval (in seconds). Standard aggregation: open=first, high=max, low=min, close=last, volume=sum. Buckets are aligned to interval boundaries (epoch %% interval).
- **benchmark_alpha_beta** — Alpha (intercept) and beta (slope) of strategy returns regressed on benchmark returns. Plus correlation. Beta>1 = strategy moves more than the benchmark; alpha>0 = excess return after controlling for beta.
- **benchmark_compare_curves** — Side-by-side curve comparison: strategy vs benchmark total return, max DD, Sharpe; reports excess return and a verdict (outperforms / matches / underperforms). Curves are truncated to common length.
- **benchmark_excess_return_series** — Return strategy minus benchmark per period as a list (capped to first 200), plus mean/std/min/max of the excess series.
- **benchmark_outperformance_periods** — Per-period wins vs benchmark + longest outperformance / underperformance streaks. Use to detect strategies that win rarely but big vs those that win often by a little.
- **bet_size_average_active** — Average overlapping bet sizes to produce a smoothed target position per bar. Each bet at index i is active for active_windows[i] bars; output[t] = mean(bet_sizes[i] for i ≤ t < i+window_i). Useful to dampen whipsaw entries.
- **bet_size_from_probability** — Bet sizing from a classifier's predicted probability: size = sign × (2·Φ(z) − 1) where z = (p − 1/n) / sqrt(p·(1−p)). Optional step_size discretizes the output to a grid. Returns size in [-1, 1].
- **brittle_consecutive_loss_streak** — Compute longest consecutive losing streak (and win streak) from a trade array. Reports the streak as a fraction of total trades. Brittle strategies often hide their fragility in long flat or losing periods.
- **brittle_curve_concentration** — Measure concentration of equity-curve gains across n_buckets segments. Verdict based on what fraction came from the top 20% of buckets + Gini coefficient. Use when you have the curve but not the per-trade list. Read-only.
- **brittle_score_from_trades** — Detect a brittle strategy from its trade-PnL array. Returns a verdict (very_brittle / brittle / skewed / robust) based on the top 5% / top 10% trade contribution and Gini coefficient. Use to decide whether a high-fitness backtest is worth d...
- **brittle_top_trades_share** — Explicit 'top N trades share of total profit' breakdown. Useful for stakeholder reports. Returns top_n_sum, total positive PnL, and share-of-total %.
- **calendar_by_dayofweek** — Aggregate trade PnLs by day of week (Mon-Sun). Useful to detect calendar bias (Monday momentum, Friday squaring up).
- **calendar_by_hour** — Aggregate trade PnLs by hour of day (0-23). Returns trades / wins / losses / sum / mean / best / worst per hour, plus best+worst overall. Useful to find session-specific edge.
- **calendar_by_month** — Aggregate trade PnLs by calendar month (1-12). Useful for seasonality / 'sell in May' style effects.
- **calendar_by_quarter** — Aggregate trade PnLs by quarter (Q1-Q4). Useful for quarterly rebalancing analysis.
- **cointegration_engle_granger** — Engle-Granger two-step cointegration test: regress y on x, compute residuals (the spread), test residuals for stationarity. Returns hedge_ratio + ADF-on-residuals + verdict (cointegrated_1pct / 5pct / 10pct / not_cointegrated). Use as the f...
- **cointegration_pair_scan** — Scan every pairwise combination of a set of price series for cointegration. Returns the top_n best pairs sorted by ADF statistic on the residuals. O(N²) — cap series count to ~30-50 for fast response.
- **cointegration_spread_series** — Compute the spread series (y - hedge_ratio · x) given the hedge ratio. Returns the spread + its mean + std + Ornstein-Uhlenbeck half-life (how fast deviations decay).
- **cointegration_zscore** — Z-score of the latest spread value against its rolling window mean/std. Returns signal: long_y_short_x (z<-2), short_y_long_x (z>2), weak_signal (|z|>1), or no_signal. Canonical pairs-trading entry rule.
- **cost_apply_to_trades** — Subtract a per-trade cost (commission + slippage_points × point_value) from each PnL. Returns the cost-adjusted trade list. Useful to feed into other analytics with realistic costs.
- **cost_break_even_per_trade** — Compute the per-trade cost at which a strategy's average trade hits zero. The 'cost ceiling' for the strategy to remain viable. Pure math.
- **cost_recompute_metrics** — Side-by-side original vs cost-degraded metrics: net profit, profit factor, win rate, average trade, max drawdown. Reports whether the strategy survives the modeled costs.
- **cost_sensitivity_grid** — Sweep over a grid of (commission, slippage_points) values; report degraded net profit and survival at each combination. Sorted best→worst. Use to find the cost envelope a strategy can tolerate.
- **cpcv_generate_splits** — Generate the full schedule of (train, test) index splits for Combinatorial Purged Cross-Validation. Embargo (default 1% of samples) is applied after each test group to prevent leakage. Suitable as input to any backtester that accepts index ...
- **cpcv_path_count** — Compute how many distinct out-of-sample paths Combinatorial Purged Cross-Validation generates for given (n_groups, test_groups). Pure combinatorics — no data needed.
- **cpcv_summarize_paths** — Summarize a distribution of OOS Sharpe ratios (one per CPCV path) into percentiles, mean ± std, fraction beating a benchmark, and a verdict (robust / promising / borderline / fragile). Use after running cpcv_generate_splits + a backtester.
- **dashboard_overview** — One-shot workspace dashboard: project count, total strategies in Results databanks, recent project mtimes, tag inventory, lineage tree size, optionally stale .dat files. Returns a structured payload — pair with dashboard_render_markdown for...
- **dashboard_render_markdown** — Render a dashboard payload as Markdown. Pass the `payload` returned by `dashboard_overview`. Pure formatting.
- **detect_prompt_injection** — Heuristic prompt-injection detector — pattern-matches against known indicators (instruction overrides, destructive commands, role-tag impersonation, jailbreak phrases). Returns severity + verdict. Read-only — does not modify text.
- **drawdown_pain_decomposition** — Decompose total pain (area under the underwater curve) by depth buckets. Tells you which DD severity contributed most to the cumulative pain — a strategy that spent 10% of time at −5% accumulates more pain than one that spent 0.1% at −50%.
- **drawdown_periods** — Segment an equity curve into drawdown episodes. Each episode reports start / peak / trough / recovery indices, depth %, duration, drop bars, recovery bars. Also reports the deepest and longest drawdown across the curve.
- **drawdown_recovery_curve** — Recovery-time distribution across drawdown episodes. Reports mean/max duration and recovery bars + count of ongoing (unrecovered) drawdowns.
- **drawdown_time_underwater** — Time underwater: fraction of bars below the running peak (regardless of depth). Verdict: rarely / moderately / often / almost_always_underwater.
- **drift_compare_average_trade** — Z-score of the live mean trade PnL vs the backtest mean (assuming backtest std). |z|>2 → drift. Requires >=20 live trades. Read-only.
- **drift_compare_distributions** — Two-sample Kolmogorov-Smirnov distance between backtest and live trade-PnL distributions, compared against the 5% critical value. Pure Python (no scipy). Read-only.
- **drift_compare_drawdown** — Flag drift if live max drawdown exceeds backtest by more than threshold_excess_pct. Use as an early stop on a strategy that's underperforming.
- **drift_compare_win_rate** — Wilson confidence interval on live win-rate; flag drift if the backtest win-rate falls outside the band. Requires >=20 live trades. Read-only.
- **drift_score_overall** — Combine win-rate, mean-trade, and drawdown drift checks into one traffic-light verdict (green / yellow / red) and a recommendation. Requires >=20 live trades. Read-only.
- **exposure_concentration_hhi** — Herfindahl-Hirschman concentration index on per-symbol notional weights. Returns raw + normalized HHI ∈ [0, 1] + verdict (very_concentrated / concentrated / moderately_diversified / diversified).
- **exposure_correlation_risk** — Correlation-weighted effective exposure: how much of the gross exposure is correlated risk vs diversified. Verdict: strong_diversification (eff<0.6) / moderate (0.6-0.85) / weak (0.85-0.98) / highly_correlated (>0.98).
- **exposure_leverage_check** — Check gross and net leverage vs a max-leverage policy. Reports headroom % and a verdict (safe / near_limit / breach / critical_breach).
- **exposure_summary** — Summarize live exposure across a set of open positions. Reports net (long − short), gross (long + short), per-symbol breakdown, and directional bias verdict (strongly_long / moderately_long / balanced / moderately_short / strongly_short).
- **exposure_var_decomposition** — Decompose portfolio VaR by symbol contribution given per-symbol volatilities and a correlation matrix. Returns total VaR + each symbol's variance contribution and share %.
- **fingerprint_find_duplicates** — Find near-duplicates in a list of fingerprints by pairwise similarity ≥ threshold (default 0.85). Returns the duplicate groups + each pair's score. Use to clean up redundant strategies.
- **fingerprint_from_metrics** — Compute a fingerprint from a strategy metrics dict. Returns tags (coarse structural traits), param_hash (SHA-256 of canonicalized params), num_hash (over bucketed numeric metrics), and curve_digest (16-sample equity-curve summary). Use to c...
- **fingerprint_from_sqx** — Compute a fingerprint directly from a .sqx file. Parses settings + metrics + embedded equity curve and produces the same fingerprint shape as fingerprint_from_metrics.
- **fingerprint_similarity** — Pairwise similarity between two fingerprints. Returns a similarity score in [0,1], its components (tag Jaccard + curve correlation), and a verdict (identical / near_identical / very_similar / similar / distinct).
- **fracdiff_apply** — Apply fractional differentiation order d to a price series. Returns the differentiated series (shorter than input by window-1 samples) plus the effective window length. Use to produce stationary input for ML without destroying long-memory s...
- **fracdiff_find_min_d** — Sweep d from d_min to d_max and return the minimum d that makes the series stationary by a heuristic Dickey-Fuller statistic (threshold default -3.0, roughly 1% large-sample critical value). Use to pick the smallest differencing order that ...
- **fracdiff_weights** — Compute the fixed-width-window weights for fractional differentiation order d. Weights truncate when |w_k| < tolerance (default 1e-5). Returns the weight vector and its length. Read-only.
- **hypothesis_paired_t_test** — Paired t-test on per-trade differences between two strategies. Use when the strategies trade the same instrument over the same period so each trade is naturally paired. Normal approximation; tight for n > 30.
- **hypothesis_sign_test** — Non-parametric sign test on paired differences. Counts +, -, and tie pairs and runs a binomial test. Robust to outliers but less powerful than the t-test.
- **hypothesis_unpaired_t_test** — Two-sample Welch's t-test (unequal variances) between two independent samples. Use when trades aren't naturally paired.
- **hypothesis_wilcoxon_signed_rank** — Wilcoxon signed-rank test on paired differences. Non-parametric alternative to the paired t-test that uses magnitudes (ranked). Use when differences aren't normally distributed.
- **indicator_atr** — Wilder's Average True Range (ATR) with the canonical smoothing recursion: ATR_t = ((window-1)·ATR_{t-1} + TR_t) / window. Foundation for ATR-based stops and Keltner bands.
- **indicator_atr_stop** — ATR-based stop level for a single trade: stop = entry ± n_atr · ATR (minus for long, plus for short). Returns the stop price + distance in absolute and percent terms.
- **indicator_bollinger** — Bollinger bands: SMA ± N · stddev over a rolling window. Returns middle/upper/lower series + a current snapshot with position_in_band ∈ [0, 1] (0 = at lower band, 1 = at upper band). Classic mean-reversion envelope.
- **indicator_chandelier_stop** — Chandelier exit: trailing-stop variant. Long stop = max_high_N − k·ATR; short stop = min_low_N + k·ATR. Standard k = 3, window = 22. Less whipsaw than fixed-distance ATR stop.
- **indicator_donchian** — Donchian channel: rolling N-bar highest high and lowest low. Used in classic Turtle trend systems — breakouts of the upper channel signal long entry.
- **indicator_keltner** — Keltner channel: EMA ± N · ATR. Trend-following envelope that uses true range instead of close-stddev. Reacts faster than Bollinger when volatility expands.
- **info_diversity_index** — Mean pairwise Jensen-Shannon-like distance across a portfolio of strategies. Verdict: highly_diversified (>1.0), moderately_diversified (0.3-1.0), concentrated (0.1-0.3), near_duplicates (<0.1). Use to audit a portfolio for redundancy at th...
- **info_kl_divergence** — Kullback-Leibler divergence between two empirical distributions. D_KL(P‖Q) = 0 iff P ≡ Q. Use to compare strategy return distributions or live-vs-backtest distributions. Laplace-smoothed to avoid log(0).
- **info_mutual_information** — Mutual information between two series. Captures non-linear dependence (which Pearson correlation misses). 0 = independent; >0 = dependent. Useful for spotting hidden relationships between strategies.
- **info_shannon_entropy** — Shannon entropy of a discretized series in bits (or other base). High entropy = uniform / unpredictable; low = concentrated mass. Returns normalized entropy ∈ [0, 1] for direct interpretation.
- **info_transfer_entropy** — Schreiber's transfer entropy from source to target at given lag. Detects directional information flow that correlation cannot. Use to test 'does X predict Y k bars later?'
- **kelly_criterion** — Kelly criterion + fractional-Kelly variants. Given win rate, avg win and avg loss, returns the full Kelly fraction plus half / quarter Kelly. Most pros use 0.25 × Kelly because full Kelly is extremely sensitive to estimation error. Read-onl...
- **label_meta_label_outcomes** — Given primary-signal labels (-1/0/+1) and realized per-bar PnL, produce meta-labels (1 = primary was right, 0 = primary was wrong or didn't fire). Use as the target variable for a secondary classifier trained on top of an existing strategy.
- **label_triple_barrier** — Apply the triple-barrier method (Lopez de Prado, AFML Ch.3) to label entries by whichever exits first: profit_target (label +1), stop_loss (-1), or time_horizon expiry (0). Caller supplies the price series, entry bar indices, PT/SL percenta...
- **lineage_ancestors** — List every ancestor (parent, grandparent, …) of a node. Returns an ordered list root-direction. Empty list if the node has no parent.
- **lineage_descendants** — List every descendant (children, grandchildren, …) of a node. Returns a flat list in depth-first order.
- **lineage_get** — Return a single node's metadata. None if the node is not registered.
- **lineage_link** — Set or clear the parent of an existing node. Detects cycles and rejects them. Pass parent_id=null to detach.
- **lineage_list** — List every lineage node. Returns the full nodes dict.
- **lineage_register** — Register a strategy in the lineage tree. node_id is any stable identifier (typically .sqx relpath or trades_hash). Optional parent_id, label, notes, metadata. Fails if node_id already exists or parent_id is missing.
- **lineage_remove** — Remove a node. Does NOT cascade: children of the removed node have their parent set to None. Returns the list of detached children.
- **lineage_tree** — Produce a nested tree rooted at node_id. Each node has its metadata + a children array. Useful for rendering or recursive audits.
- **mean_reversion_adf_heuristic** — Heuristic Augmented Dickey-Fuller stationarity test (re-exposed from frac_diff for general use). Returns the t-statistic; more negative = more stationary. Critical values approximately −1.95 (10%), −2.86 (5%), −3.43 (1%).
- **mean_reversion_cusum_detector** — Two-sided CUSUM change-point detector for live PnL monitoring. Signals when cumulative deviation from a reference (default = series mean) crosses ±threshold. Returns detection indices + direction (upward/downward) + verdict (stable / occasi...
- **mean_reversion_hurst** — Hurst exponent via rescaled-range (R/S) analysis. <0.5 = mean-reverting, ~0.5 = random walk, >0.5 = trending. Verdict buckets: strongly_mean_reverting / mean_reverting / random_walk / trending / strongly_trending. Use to decide between mean...
- **mean_reversion_ou_half_life** — Estimate Ornstein-Uhlenbeck mean-reversion half-life — how many bars a deviation from the long-run mean takes to decay by 50%. Verdict: fast (<10), moderate (10-50), slow (50-500), very_slow_or_unstable. Returns None if no mean-reverting te...
- **montecarlo_drawdown_distribution** — Monte Carlo bootstrap over a strategy's equity curve, but reporting only max-drawdown percentiles (lighter return than montecarlo_equity_paths). Read-only.
- **montecarlo_equity_paths** — Monte Carlo bootstrap over a strategy's equity curve. Parses the .sqx, extracts the embedded equity sparkline, converts to per-sample returns, and resamples with replacement to produce N synthetic paths. Reports percentile distribution of t...
- **montecarlo_probability_of_drawdown** — Probability of hitting drawdown_threshold_pct across N Monte Carlo paths. Caller supplies an explicit returns array (use montecarlo_equity_paths if you'd rather start from an .sqx). Read-only.
- **montecarlo_synthetic_from_returns** — Monte Carlo bootstrap from an explicit per-trade returns array (any source, not just SQ X). Useful when the caller has an MT5 backtest report or another strategy backtester's results. Pure math — no engine call.
- **notify_format_discord_build_status** — Format a Discord embed dict from a project status. Caller wraps it in `{embeds: [...]}` and POSTs to the webhook. Returns the embed object only.
- **notify_format_email_html** — Build an HTML email body from a title + section list. Each section is {heading, body}; body may be a string or a list (rendered as <ul>). Returns ready-to-send HTML. Pure formatting.
- **notify_format_slack_build_status** — Format a Slack-flavored markdown message from a project status dict (as returned by project_status). Includes a status emoji and the common fields (status, fitness, count, elapsed). Returns text only — the caller posts it. Pure formatting —...
- **notify_format_status_line** — One-line plain-text status (cron-friendly). [project] status fitness=X count=Y. Useful for shell scripts or status bars.
- **oscillator_cci** — Commodity Channel Index: (typical_price − SMA(typical, N)) / (0.015 × mean absolute deviation). Unbounded; >100 = overbought, <−100 = oversold.
- **oscillator_macd** — MACD = EMA(fast) − EMA(slow), with EMA(signal_period) signal line and (MACD − signal) histogram. Default 12/26/9 configuration. Returns the three series + current values.
- **oscillator_rsi** — Wilder's Relative Strength Index. Bounded [0, 100]; >70 = overbought, <30 = oversold. Uses smoothed average gain / loss recursion.
- **oscillator_stochastic** — Stochastic oscillator: %K = (close − low_N) / (high_N − low_N) × 100, %D = SMA(%K, d_period). >80 = overbought, <20 = oversold. Returns both series + current values.
- **oscillator_williams_r** — Williams %R: −100 · (high_N − close) / (high_N − low_N). Bounded [−100, 0]; >−20 = overbought, <−80 = oversold. Inverted Stochastic %K essentially.
- **overfit_deflated_sharpe** — Deflated Sharpe Ratio probability (Bailey/Lopez de Prado). Estimates the probability that the observed Sharpe is genuinely above a benchmark given N trials, sample size, and return skew/kurtosis. >0.95 = very robust; <0.60 = likely overfit....
- **overfit_haircut_sharpe** — Simpler heuristic Sharpe haircut: subtract sqrt(log(N_trials) / N_observations) from the observed Sharpe. Use when you don't have skew/kurt — DSR is the rigorous version. Read-only.
- **overfit_min_track_record_length** — Minimum number of observations needed to claim observed Sharpe is statistically above benchmark at the given confidence. Returns None if observed Sharpe is below benchmark. Read-only.
- **overfit_pbo_from_oos_pairs** — Probability of Backtest Overfitting from a list of (in-sample, out-of-sample) Sharpe pairs. The in-sample best is overfit if its OOS performance falls below the OOS median. Read-only.
- **perp_funding_adjusted_pnl** — Adjust trade PnLs for funding-rate payments on perpetual futures. Caller supplies per-trade holding hours, average funding rate per 8h, side (long/short), and notional. Longs pay positive funding; shorts receive it. Reports the adjusted per...
- **perp_funding_regime_classifier** — Bucket per-8h funding-rate observations into contango / neutral / backwardation regimes by threshold. Returns per-bucket counts, share percentages, and an annualized-funding-cost estimate.
- **perp_liquidation_distance_pct** — Same as perp_liquidation_price but returns just the percentage distance from entry. Convenience for risk dashboards.
- **perp_liquidation_price** — Compute the liquidation price for an isolated-margin perpetual-futures position given entry price, leverage, side, and maintenance-margin percentage (default 0.5%). Returns the liquidation price and the distance from entry as a percent.
- **perp_liquidation_stress_test** — Replay trades with intra-bar high/low excursion to detect which would have been liquidated at the given leverage. Reports per-trade liquidation hits (up to 50) and a verdict (safe / marginal / dangerous / unfeasible).
- **pinescript_generate_alert_template** — Emit Pine Script alertcondition() scaffold compatible with TradingView webhooks. Caller supplies the webhook message template (JSON with TradingView placeholders like {{ticker}}).
- **pinescript_generate_indicator** — Emit Pine Script v5 indicator source. Supports sma_crossover, ema_crossover, rsi, macd. Returns the source as a string the user can paste into TradingView's Pine editor. Read-only.
- **pinescript_generate_strategy** — Emit a Pine Script v5 strategy with entries, TP/SL exits, risk-percent sizing, and date filter. Read-only.
- **pivots_camarilla** — Camarilla pivot points: eight levels (4R + 4S) clustered near the previous close, with multipliers 1.1/12, 1.1/6, 1.1/4, 1.1/2. Designed for intraday range-bound trading.
- **pivots_classic** — Classic pivot points: PP = (H+L+C)/3, R1/S1 from PP±(PP-L)/(H-PP), R2/S2 from PP±range. Five levels total. Most widely used variant.
- **pivots_compare_all** — Run all five pivot variants on the same bar (requires OHLC). Returns Classic / Fibonacci / Camarilla / Woodie / DeMark side-by-side for comparison.
- **pivots_demark** — DeMark pivot points: recipe depends on close vs open direction (bullish / bearish / inside). Returns only PP, R1, S1 — fewer levels but more directional context. Requires OHLC.
- **pivots_fibonacci** — Fibonacci pivot points: levels at 38.2% / 61.8% / 100% of the previous range from PP. Seven levels (PP + 3R + 3S).
- **pivots_woodie** — Woodie pivot points: PP = (H+L+2C)/4 — weights close at 2×. R1/S1/R2/S2 as in classic. Use when the closing price matters more than the open.
- **position_sizing_calc** — Compute the lot/contract size that risks exactly risk_pct of the account given a stop-loss distance. Handles crypto, forex (pip-based) and generic contract-size instruments. Rounds to lot_step. Returns raw + final lots, risk in dollars, not...
- **position_sizing_pyramid** — Given an initial position and a pyramiding schedule (a list of (add_at_price, add_size) tiers), report cumulative size, weighted average entry, R-multiple of each tier vs the initial stop, and the total absolute risk to the original stop. U...
- **position_sizing_risk_of_ruin** — Analytic approximation of long-run risk of ruin given win rate, payoff ratio (avg_win / avg_loss) and risk_per_trade_pct. Quick first-cut estimate; pair with the Monte Carlo simulator for distribution-aware results.
- **promotion_evaluate** — Run the full promotion gate checklist on a strategy. Each gate is evaluated against caller-supplied inputs (trades, oos_is_ratio, drawdown_pct, brittle_verdict, stress_verdict, drift_verdict, etc). Required gates block; soft gates only warn...
- **promotion_required_gates** — List the default gates and which inputs they expect. Use to know what to provide for a promotion_evaluate call.
- **ratios_calmar** — Calmar ratio: CAGR / |max drawdown|. CAGR computed from total_return_pct + years_observed. Useful when you don't have a full return series.
- **ratios_information** — Information ratio — Sharpe-like ratio of active returns (strategy - benchmark). Use to evaluate a strategy vs buy-and-hold.
- **ratios_mar** — MAR ratio = CAGR / max drawdown (functionally equivalent to Calmar). Standard Managed Account Reports metric.
- **ratios_omega** — Omega ratio at a threshold = sum(gains above) / sum(losses below). More forgiving than Sharpe — captures skewness. Threshold often 0 (profit/loss) or risk_free_rate.
- **ratios_pain_index** — Pain Index — average per-sample drawdown across an equity curve. Captures the 'pain' of being underwater on average, not just at the worst point.
- **ratios_sharpe** — Annualized Sharpe ratio from a return series. Subtracts the per-period risk-free rate, computes (mean / std) × sqrt(periods/yr). Read-only.
- **ratios_sortino** — Annualized Sortino ratio — like Sharpe but uses downside deviation (only returns below the risk-free rate count). Rewards strategies with limited downside even if upside is volatile.
- **ratios_ulcer_index** — Ulcer Index — RMS of per-sample drawdowns. Penalizes deep AND prolonged drawdowns more than max-DD. Lower = smoother ride.
- **redact_pii** — Naive PII redaction: replaces emails, IPv4 addresses, long hex strings (32+ chars, potential API keys), and long digit runs (8+, potential account numbers) with placeholder tokens. Useful before forwarding text to an LLM provider or to a no...
- **regime_classify_trend** — Classify each sample of a price series into a trend regime (bear/neutral/bull) using rolling linear-regression slope. Read-only.
- **regime_classify_volatility** — Classify each sample of a price series into a volatility regime (low/normal/high) using a rolling stddev tertile. Window controls the smoothing — 20 bars is typical for daily, 50 for hourly. Read-only.
- **regime_pnl_split** — Split trade PnLs across regime labels. Use to spot strategies that only work in one regime (e.g. only profitable in high-vol). Caller supplies trade_pnls and a parallel array of regime labels (e.g. from regime_classify_volatility, sampled a...
- **regime_recommend_filter** — Given a regime PnL split, recommend which regimes to trade and which to skip. Sorts regimes by metric (mean_pnl, win_rate, total_pnl) and reports the best plus a keep/skip recommendation.
- **risk_profile_apply** — Apply a risk profile's money-management settings to a project's Build/Optimize tasks. Snapshots the .cfx first. Activates the profile's MM method, sets Risk and MaxLots params, optionally updates initial_capital. WRITE — confirm with the us...
- **risk_profile_compare_to_strategy** — Check whether a strategy's drawdown / OOS-IS ratio / trade count fit within a given risk profile's caps. Returns 'fits' or 'violates' plus the specific cap violations. Use before promoting a strategy to live trading under a given profile. P...
- **risk_profile_list** — List every known risk profile with its summary fields. Useful when asking the user to pick one. Read-only.
- **risk_profile_recommend** — Look up a risk profile by name (ultra_conservative, conservative, moderate, aggressive). Returns the full bundle: risk per trade %, max DD threshold, max concurrent positions, compounding flag, recommended MM method + params, recommended Ke...
- **rolling_beta_alpha** — Rolling beta and alpha from a regression of strategy returns on benchmark returns. Returns parallel series of beta_values and alpha_values + current_beta / current_alpha.
- **rolling_correlation** — Pearson correlation between two series over a rolling window. Use to detect when a previously diversifying strategy starts tracking the benchmark (correlation drift).
- **rolling_drawdown** — Max drawdown computed over each rolling window of the equity curve. Returns the windowed series + worst-window DD across the curve. Detects 'we got dragged into a deeper drawdown' early.
- **rolling_sharpe** — Annualized Sharpe ratio computed over a rolling trailing window of N periods. Returns a parallel series + a summary (mean / min / max / current). Useful for detecting Sharpe decay live.
- **rolling_volatility** — Annualized volatility over a rolling window. Useful for live volatility regime detection — compare current vs mean to detect surprises.
- **rolling_win_rate** — Win rate (fraction of positive trades) computed over a rolling window. Detect early when a strategy's edge starts eroding without waiting for a full backtest update.
- **sanitize_struct_for_llm** — Recursively sanitize every string in a nested dict/list/tuple payload. Use on a sqcli response payload before forwarding it to the LLM.
- **sanitize_text_for_llm** — Escape Markdown code fences and fake role-tags (<system>, <assistant>, <user>, <tool>, <instructions>) in untrusted text so the LLM treats them as literal. Optionally redact PII (emails, IPs, long hex strings, long digit runs). Use on any s...
- **schedule_for_strategy** — Recommend what to do with a single strategy: no_action / retest / rebuild. Inputs: when the strategy was built, when underlying data was last updated, optionally days-live and a drift verdict. Returns the recommended action + reasons. Pure ...
- **schedule_for_workspace** — Apply schedule_for_strategy across a list of strategy items the caller supplies (each item must have strategy_built_at, data_updated_at, etc.). Returns a ranked queue (rebuild first) and a counts summary.
- **schedule_quarterly_calendar** — Yearly cadence calendar for a portfolio: Q1/Q2/Q3/Q4 actions tuned for asset_class (crypto needs frequent retests, equities less so). Returns markdown-paste-ready list of actions per quarter.
- **sensitivity_bullseye** — Find best parameter value (highest metric) in a sweep, plus the range of parameter values within tolerance_pct of that best. Identifies a 'safety plateau' you can pick from.
- **sensitivity_compare_two_params** — Compare sensitivity of two parameters. Returns plateau scores and which parameter the strategy is more sensitive to.
- **sensitivity_local_slope** — Estimate local slope of metric vs parameter around a baseline value. Uses the nearest neighbors on each side. Large |slope| indicates high sensitivity. Pure math.
- **sensitivity_plateau_score** — Plateau (robustness) score from a parameter sweep. 0-100, higher = flatter metric curve (less sensitive to parameter changes). Uses coefficient of variation.
- **sensitivity_range_decay** — Find the parameter values on each side of the baseline at which the metric falls below baseline × (1 - tolerance_pct). Tells you how far you can drift from baseline before performance degrades.
- **signal_hit_rate_by_quantile** — Quantile sort: rank signals into Q buckets, report mean forward return + hit rate per bucket. Returns top-bottom spread and a monotonicity check. Clean signal = monotonic + positive spread.
- **signal_ic_decay** — IC decay across multiple forward horizons (default 1, 2, 5, 10, 20 bars). Identifies the peak horizon (natural holding period) and where the signal decays to noise (|IC| < 0.03).
- **signal_information_coefficient** — Information Coefficient: Spearman rank correlation between signal[t] and forward_return[t]. Returns IC + t-statistic + verdict (very_strong / strong / weak / noise). |IC| > 0.05 is useful at scale; > 0.10 is exceptional in equity research.
- **signal_to_noise_ratio** — Signal-to-noise ratio = var(signal) / var(noise). Returns SNR in dB. Verdict: excellent (>10dB), good (3-10), marginal (0-3), noise_dominated (<0).
- **signal_turnover** — Turnover: mean absolute per-bar change in the signal, optionally normalized by the mean absolute signal level. Verdict: low / moderate / high / extreme. High turnover means high transaction costs.
- **spa_test_consistency** — Bootstrap consistency report: for each strategy, what fraction of bootstrap samples is it the best performer? Use as a sanity check alongside spa_test_reality_check — a 'best' strategy with low win share is suspicious.
- **spa_test_hansen** — Hansen's SPA test (2005): improved Reality Check that studentizes each strategy's excess return. Less sensitive than the basic Reality Check to dispersion across irrelevant strategies.
- **spa_test_reality_check** — White's Reality Check: bootstrap-corrected p-value for the null 'no strategy beats the benchmark.' Uses the stationary bootstrap to handle autocorrelated returns. Correct for data-snooping when picking the best of N strategies.
- **spp_compare_baseline** — Compare a baseline metric against the SPP distribution: report the percentile rank + whether it sits in the top quartile/decile + a verdict. Use to detect in-sample overfit (baseline at the 95th+ percentile of perturbations is suspicious).
- **spp_permutation_grid** — Generate the parameter-permutation grid for System Parameter Permutation testing. Each parameter is perturbed ±perturbation_pct in n_steps_per_param values. Returns the full grid, or a random sample if n_random_samples is set.
- **spp_recommend_params** — Given a list of (params, metric) entries from an SPP run, recommend the parameters at the median metric — the typical out-of-sample performance instead of the in-sample best. Reports best/worst params alongside for comparison.
- **spp_summarize_distribution** — Summarize a distribution of per-permutation metrics from an SPP run. Reports percentiles + profitable_share_pct + coefficient_of_variation + verdict (very_robust / robust / moderately_fragile / fragile). Optionally compares against a baseli...
- **stationary_bootstrap_confidence** — Stationary-bootstrap confidence interval for a chosen statistic (sum / mean / sharpe / median) at the given confidence level (default 0.95). Returns CI bounds + width.
- **stationary_bootstrap_paths** — Draw n_paths stationary-bootstrap paths and report the distribution of a chosen aggregator (sum / mean / sharpe). Returns percentiles + the fraction of paths whose statistic meets or exceeds the observed value. Read-only.
- **stationary_bootstrap_resample** — Politis-Romano stationary bootstrap: draw a single resampled series by chaining geometric-length blocks (block_length is the expected block size; each step continues w.p. 1-1/L or restarts w.p. 1/L). Preserves short-range autocorrelation. R...
- **stats_autocorrelation** — Sample autocorrelation at the given lag, plus a single-lag Ljung-Box statistic. Use to detect serial dependence in returns — non-zero autocorrelation often indicates trend/momentum effects.
- **stats_irr** — Internal Rate of Return from a cashflow series via Newton-Raphson. Cashflows must include at least one positive and one negative. Returns the rate that makes NPV = 0. Read-only.
- **stats_jarque_bera** — Jarque-Bera normality test on a return series. Combines skewness and kurtosis into a single χ² statistic. p > 0.10 = consistent with Normal; p < 0.05 = reject normality.
- **stats_kurtosis** — Sample excess kurtosis (bias-corrected). Normal = 0. Positive = fat tails (more extreme observations than Normal predicts); negative = thin tails.
- **stats_runs_test** — Wald-Wolfowitz runs test on the sign of returns. p > 0.10 = consistent with independence; p < 0.05 = significant streaks or alternation in the sign pattern.
- **stats_skewness** — Sample skewness (Fisher-Pearson, bias-corrected). Positive = right tail heavier; negative = left tail heavier. Useful for spotting asymmetric P&L distributions. Read-only.
- **stats_summary** — One-shot descriptive statistics: mean, median, std, skew, kurtosis, min, p25, p75, max. Useful starting point before any deeper analysis. Read-only.
- **stats_time_weighted_return** — Time-Weighted Return: chain per-period returns (1+r1)(1+r2)…(1+rN) − 1. Use when the strategy has external cashflows you want to exclude from the performance calc.
- **stress_apply_random_skip** — Drop a random fraction of trades (simulates connectivity loss). Seed is exposed for reproducibility.
- **stress_apply_skip_best** — Remove the top-N best trades and re-compute metrics. Tests whether the strategy survives without the lucky outliers. If the strategy dies after removing 1-2 trades, it's brittle.
- **stress_apply_slippage** — Apply a slippage haircut: positive trades lose slip_pct, losses get larger by slip_pct. Report degraded vs original metrics. Realistic crypto/forex slippage is 1-5%.
- **stress_combined_scenarios** — Run all stress scenarios (slippage, skip-best, random-skip, worst-streak) and report a combined verdict: robust / moderate / fragile based on how many scenarios kill the strategy. Read-only.
- **stress_worst_case_dd** — Synthetic worst-case streak: take the streak_length worst trades and inject them consecutively at the start. Reports the resulting drawdown — an upper bound on what the strategy might have seen.
- **tail_risk_cvar** — Conditional Value at Risk (Expected Shortfall) — the average loss in the tail beyond VaR. More informative than VaR alone for fat-tailed distributions. Read-only.
- **tail_risk_gain_to_pain** — Gain-to-pain ratio = sum(positive returns) / |sum(negative returns)|. >2.0 is robust, <1.0 is weak. Simpler alternative to Sharpe when you care about edge over losses, not volatility.
- **tail_risk_historical_var** — Historical Value at Risk at the given confidence level (default 0.95). Returns the worst 5% (1−c) loss observed in the return series. Non-parametric — no distributional assumption. Read-only.
- **tail_risk_parametric_var** — Parametric VaR assuming Normal returns. Compare against historical VaR — if historical >> parametric, returns have fat tails and Normal assumption underestimates risk.
- **tail_risk_tail_ratio** — Tail ratio = |p95| / |p5|. >1 means fatter upside tail; <1 means fatter downside tail; ~1 means symmetric. Useful for evaluating skewness of P&L distribution.
- **trade_replay_apply_costs** — Replay a trade PnL list with overridden commission and slippage (per-trade cost). Reports original and replayed metrics for side-by-side comparison.
- **trade_replay_compare_to_original** — Compare original vs replayed PnLs side by side. Reports each metric's delta. Use to quantify the impact of a replay tweak.
- **trade_replay_filter_by_regime** — Replay trades but skip those in the listed regimes (caller supplies a parallel regime-label array). Use to validate a 'trade only in low-vol' filter.
- **trade_replay_position_sizing** — Replay trades with a different position-sizing policy: fixed_lot, fixed_fractional (% of equity per trade), or percent_risk (% of equity at risk per trade, given trade_avg_risk). Caller supplies per-unit PnLs.
- **trade_replay_risk_caps** — Apply per-trade, per-day, and per-week loss caps. Trades that would exceed a cap are either capped (per-trade) or skipped (daily/weekly). Reports replayed metrics.
- **twap_compute** — Time-Weighted Average Price: weighted mean of prices with caller-supplied weights (typically time-spans between samples). Use when volume is unavailable or not relevant.
- **vol_close_to_close** — Close-to-close log-return volatility (annualized). Baseline estimator — uses only one data point per bar. Read-only.
- **vol_estimator_comparison** — Run all five volatility estimators (close-to-close, Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang) on the same OHLC data. Returns them side-by-side for comparison.
- **vol_garman_klass** — Garman-Klass (1980) volatility from OHLC. ~7x more efficient than close-to-close. Assumes zero drift; use rogers_satchell or yang_zhang if drift is significant.
- **vol_parkinson** — Parkinson (1980) volatility from (high, low). Uses intraday range; ~5x more efficient than close-to-close for the same sample size.
- **vol_rogers_satchell** — Rogers-Satchell (1991) volatility from OHLC. Drift-independent — preferred over Garman-Klass when the asset has a non-zero trend.
- **vol_yang_zhang** — Yang-Zhang (2000) volatility from OHLC + overnight returns. Drift-independent, ~14x more efficient than close-to-close — the recommended high-frequency estimator.
- **vwap_compute** — Volume-Weighted Average Price from parallel (prices, volumes) arrays. Σ(p·v) / Σv. Returns None if total volume is zero.
- **vwap_participation_rate** — Participation rate = own_volume / market_volume × 100. Verdict: negligible_impact (<1%), low_impact (1-5%), moderate_impact (5-10%), high_impact (10-25%), dominant_player (>25%). High participation invites market impact.
- **vwap_per_session** — Split a tick stream by session (asia / europe / us, or custom) given parallel hour arrays, and compute VWAP per session. Sessions wrapping midnight (e.g. asia 22-08) are supported.
- **vwap_slippage** — Slippage of a fill price against a benchmark VWAP. Buy slippage = fill − benchmark (positive = paid more); sell slippage = benchmark − fill (positive = received more). Reports bps + verdict (excellent / good / acceptable / poor).
- **wf_anchored_vs_rolling** — Compare anchored (expanding-window) vs rolling (fixed-window) walk-forward OOS results. Verdict: anchored_wins / rolling_wins / comparable. Useful for deciding which WF variant suits the strategy.
- **wf_efficiency** — Walk-Forward Efficiency = mean(OOS metric) / mean(IS metric) across paired folds. >0.8 = excellent, 0.6-0.8 = good, 0.4-0.6 = marginal, <0.4 = overfit. Reports per-fold detail and a verdict.
- **wf_matrix_summary** — Summarize a Walk-Forward Matrix (a grid of (IS bars, OOS bars) combinations) sorted by efficiency. Returns the rows + a Markdown-ready table + the best combination.
- **wf_recommendation** — Recommend the best (IS, OOS) combination from a WF matrix that meets minimum-efficiency and minimum-OOS-bars thresholds. Scoring rewards longer OOS periods (more statistical power). Returns the top recommendation + up to 4 alternatives.
- **workflow_compare_two_strategies** — Compare two strategies side-by-side: paired t-test on per-trade PnL, brittleness verdict for each, stress test for each, optionally alpha/beta if return series provided. Returns a synthesized verdict. Read-only.
- **workflow_morning_briefing** — Morning briefing for the workspace: dashboard overview + active alerts + freshness-driven schedule recommendations, all in one call. Returns structured payload + Markdown for each section. Read-only.
- **workflow_promote_strategy** — Full promote-to-live workflow: runs the ship pipeline (audit gates + tag + lineage + alerts). Returns the manifest. Set dry_run=True to preview without persisting. WRITE — confirm before non-dry-run.

## monitor (6)

- **monitor_alerts** — Return the full alert history for a monitor session.
- **monitor_default_rules** — List the default rules and what they detect — useful before starting a monitor.
- **monitor_list** — List active monitoring sessions.
- **monitor_start** — Start actively monitoring a project run. Polls databank count, new .sqx files, median fitness, OOS/IS ratio and project status; raises alerts and (optionally) stops the project if it's clearly wasting time. Returns immediately — query monit...
- **monitor_status** — Get current state of a monitor session (last snapshot, recent alerts).
- **monitor_stop** — Stop monitoring a project.

## mt5 (20)

- **mt5_assign_magic_numbers** — Deterministically assign unique MT5 magic numbers (31-bit ints) to a list of EA files. Uses SHA-256(namespace:name) hashed into [magic_floor, 2_000_000_000]. Returns a name→magic map ready to be patched into source via mt5_patch_magic_in_so...
- **mt5_compare_with_sqx** — Compare an MT5 backtest report against its source .sqx. Pairs MT5 metrics with SQ X-derived metrics, computes relative diff, and flags any pair whose |diff| > warn_threshold as 'warn'. Use to catch SQ/MT5 simulation disagreements before goi...
- **mt5_deploy_ea** — Deploy an Expert Advisor (.mq5 / .ex5) to MQL5/Experts/, optionally into a subfolder. Refuses to overwrite an existing file unless overwrite=True (in which case the previous file is renamed to .bak first). Useful after SQ X exports a strate...
- **mt5_ea_generate_basic** — Generate a minimal MT5 EA source (.mq5) with proper risk-percent sizing, magic-number filtering, SL/TP in points, max-lot cap, and a single entry condition you supply as MQL5 boolean expression. Use as a base for hand-rolled EAs or to wrap ...
- **mt5_ea_generate_with_trailing** — Same as mt5_ea_generate_basic, but additionally inserts a trailing stop: moves stop to break-even after +1R, then trails by trail_points. Includes the same risk-percent sizing + magic + max-lot guardrails. Returns the source string.
- **mt5_ea_inject_risk_guardrails** — Given an existing .mq5 source string, idempotently inject the minimum-viable risk guardrails: RiskPctEquity / MaxLots / MagicNumber input declarations + LotSizeFromRisk helper. Only adds what's missing; lists each change. The caller writes ...
- **mt5_health_check** — Health probe for the MT5 install: install present, expected subfolders, writability of Experts/, free disk space, last-modified log file. Use this before deploying an EA to confirm the target is ready.
- **mt5_list_experts** — List Expert Advisors (.mq5 source + .ex5 compiled) currently present in MQL5/Experts/. Recursive. Returns name, relative subfolder, size, mtime. Useful to verify an SQ-exported EA was actually deployed.
- **mt5_list_indicators** — List custom indicators (MQL5/Indicators/). Same shape as mt5_list_experts.
- **mt5_locate** — Locate the MetaTrader 5 installation. Returns the install root, MQL5 path, and platform-specific data folder (portable vs roaming). Honors `MT5_HOME` env var if you have a non-standard install. Pure filesystem probe — does not run MT5 or Wi...
- **mt5_log_tail** — Tail the MetaTrader 5 log file. Two scopes: 'terminal' = the platform-wide <install>/logs/YYYYMMDD.log; 'mql5' = the EA-specific MQL5/logs/YYYYMMDD.log. Defaults to today's log (or most recent if absent). Returns the last N lines. Use this ...
- **mt5_parse_backtest_report** — Parse an MT5 Strategy Tester Report.htm and extract metrics: net profit, profit factor, drawdown, total trades, Sharpe, recovery factor. Pure HTML scrape — handles English-default reports. Read-only.
- **mt5_patch_magic_in_source** — Surgically replace the magic-number literal in a .mq5 source file. Default regex matches common SQ-exported patterns: 'input int Magic = NNN;'. Override variable_pattern (a regex with 3 groups: prefix / current value / suffix) for non-stand...
- **mt5_set_apply_profile** — Apply a risk profile (ultra_conservative / conservative / moderate / aggressive) to a parsed .set by overriding LotSize, MaxRiskPercent, MaxDailyLossPercent (and optionally MagicNumber). Returns the rendered .set text.
- **mt5_set_diff** — Diff two .set files (or texts). Reports added / removed / changed keys per section. Useful when comparing parameter exports across optimization runs.
- **mt5_set_merge** — Merge an overlay .set into a base .set. overlay_wins=true (default) means overlay's values replace base's on key collision. Returns the merged sections + rendered text.
- **mt5_set_parse** — Parse a .set or .ini file (or raw text) into {section: {key: value}}. Default section is named 'default' when no [section] header is present. Read-only.
- **mt5_set_render** — Render a sections dict back to .set text. Inverse of mt5_set_parse. Useful for emitting a .set file the user can drop into MT5.
- **mt5_verify_deployment** — Verify a portfolio of EAs is correctly deployed in MQL5/Experts/. Pass an expected list of {'name': '...', 'sha256': '...', 'subdir': '...'} dicts; the tool reports which are present (and content-matched), which are absent, and which differ...
- **pinescript_translate_mt5_basic** — Best-effort translate the structural shape of an MT5 strategy into Pine Script. Returns the source plus a 'manual_review_required' flag — semantic gaps (magic numbers, slippage, indicator differences) need human attention.

## pipeline (5)

- **pipeline_build_filter_retest** — End-to-end Builder → Filter → Retester pipeline. Single autonomous call:   1. (optional) start the Builder project   2. wait for Builder completion + force-sync Results   3. filter Builder survivors by trades / drawdown / profit-to-DD / fit...
- **pipeline_export_to_mt5** — Mega-pipeline: from an SQ databank to a ready-to-deploy MT5 pack. Selects N diversified strategies, audits each one, classifies green/yellow/red, assigns unique magic numbers, and (optionally) copies the .sqx files into MQL5/Experts/<pack_n...
- **ship_pipeline_dry_run** — Dry-run the ship pipeline: evaluate promotion, simulate tagging + lineage, return what *would* happen, but persist nothing. Use to preview a decision before committing.
- **ship_pipeline_run** — Full ship-to-live pipeline: promotion gates → tag (if approved) → register lineage → emit alerts. Persists tagging + lineage via state.py. Returns a manifest with each step's outcome. Set dry_run=True to preview without persisting.
- **ship_pipeline_status_summary** — Produce a one-paragraph human-readable summary of a ship_pipeline manifest. Suitable for Slack / email.

## portfolio (21)

- **portfolio_ab_test** — A/B compare two databanks (typically: same project before/after a Builder re-run, or two competing Builder configs). Reports per-side summaries (fitness/DD/profitability), overlap by trades_hash (Jaccard), and a verdict line for each metric...
- **portfolio_audit** — Portfolio-level audit — runs portfolio_summary + concentration checks and emits a Finding[] list with severity codes (the same format strategy_audit uses). Flags systemic overfitting, low profitable rate, high concentration, single-symbol-o...
- **portfolio_capital_allocation** — Suggested capital allocation across the top-N strategies in a databank. Blends inverse-drawdown weighting (lower DD ⇒ more capital) with fitness-proportional weighting via the fitness_bias knob. Weights are normalized to 1. Read-only — does...
- **portfolio_combined_equity** — Synthesize a portfolio equity curve by summing the embedded sparklines of the top-N strategies in a databank, then compute DD / hit-ratio / recovery stats on the combined curve. weight_by='uniform' or 'composite' (composite_score-weighted)....
- **portfolio_combined_equity_explicit** — Same idea as portfolio_combined_equity but the caller hands in an explicit list of (sqx_path, weight) pairs. Weights are normalized to sum 1. Use this to verify the allocations returned by portfolio_capital_allocation actually produce the e...
- **portfolio_concentration** — Herfindahl-Hirschman concentration scores on a databank, computed separately for trades_hash (true strategy diversity), symbol, timeframe, and symbol_tf (combo). HHI ∈ [1/N, 1]; normalized HHI ∈ [0, 1] where 0 = perfectly diversified and 1 ...
- **portfolio_contribution** — Decompose a portfolio's return + max-DD into per-strategy contributions. Takes an explicit list of {sqx_path, weight} pairs, normalizes weights, and for each strategy reports: its individual return, individual max DD, the weighted return co...
- **portfolio_deck_markdown** — Generate a Markdown deck for the top-N strategies of a databank: headline numbers, traffic-light verdict, equity geometry, findings. Optional output_path writes to disk; otherwise the markdown is returned in the response.
- **portfolio_dedupe** — Find duplicate strategies in a databank by trades_hash (default — same trade sequence => functionally identical) or by SQ's full identity hash. Returns each duplicate group with the survivor flagged and the redundant files listed. Use this ...
- **portfolio_diversification_ratio** — Diversification ratio = Σ(w_i · σ_i) / σ_portfolio. >1.5 = strong diversification; ~1.0 = no benefit (perfectly correlated). Read-only.
- **portfolio_diversity_score** — Composite 0-100 diversity score for a databank — averages trades_hash diversity, symbol/TF diversity, and unique-fingerprint ratio (each derived from normalized HHI). Returns a tier label (excellent / good / marginal / poor) and the per-com...
- **portfolio_equal_weight** — Equal-weight portfolio: 1/N for N strategies. Also reports the resulting portfolio variance and std. Use as a baseline when comparing optimizers. Read-only.
- **portfolio_export_csv** — Write a CSV of every strategy in a databank with all the metrics derive_metrics produces. Optional rank_mode + top_n filter rows before writing. Returns the file path and row count.
- **portfolio_inverse_volatility** — Inverse-volatility weights: w_i ∝ 1/σ_i. Aims to equalize each strategy's risk contribution. Simpler proxy for risk parity when covariance is hard to estimate.
- **portfolio_min_variance** — Minimum-variance allocation via coordinate descent (long-only, sum-to-1 constraint). Useful when you want the lowest-risk portfolio regardless of expected return.
- **portfolio_rank** — Rank strategies in a databank by a chosen metric. Default 'defensive' mode is a composite that rewards OOS fitness and penalizes overfit (low OOS/IS ratio), large drawdown (>25%), and small sample size (<100 trades). Single-metric modes: fi...
- **portfolio_review_bundle** — Bundle the top-N strategies of a databank into a single tar.gz for offline review or sharing. Contains: each .sqx file, a Markdown deck per strategy, a portfolio CSV, and a manifest.json. Use to send to a teammate or archive for later revie...
- **portfolio_risk_metrics** — Distributional risk stats across every strategy in a databank: mean/median/stddev/p10/p25/p75/p90/p95/p99/min/max for drawdown_pct, fitness_oos, profit_to_dd_ratio, and return_pct. Read-only.
- **portfolio_risk_parity** — Iterative risk-parity allocation: each strategy contributes equally to the portfolio's total variance. Uses the covariance matrix. Converges in ~10–50 iterations for typical portfolios.
- **portfolio_select_diverse** — Select N strategies from a databank, maximizing diversity. Buckets strategies by trades_hash / exact fingerprint / (symbol,timeframe), ranks within each bucket, then round-robins the top picks. The result is a portfolio that avoids backtest...
- **portfolio_summary** — Aggregate statistics across an entire databank: how many strategies, how many profitable, fitness/DD/trades distributions, OOS-ratio overfit count, symbol/TF spread, duplicate rate by trades_hash. Use this as a first look at any builder out...

## projects (22)

- **project_anomaly_check** — Structural & operational audit of one project. Checks CFX validity, data coverage for every referenced symbol, naming pitfalls (spaces, phantom variants), snapshot hygiene, inverted date ranges. Pure filesystem read — no engine call.
- **project_clone** — Clone an existing project to a new name, optionally rewriting symbol / timeframe / dateFrom / dateTo across every task XML inside the .cfx. Pure filesystem operation — does not touch the running engine. The new project becomes visible to sq...
- **project_config** — Save / load a project's configuration from a .cfx file.
- **project_create_from_template** — Create a new project by finding a template that uses a target instrument or data symbol, and cloning it with optional symbol/TF/date rewrites. Picks the most recently modified matching source (or `prefer_source` if set). Use broker_registry...
- **project_force_remove** — Forcefully remove a project from BOTH the engine's internal state AND the filesystem, in the correct order. Just rm -rf'ing the directory leaves a phantom in JVM memory that breaks subsequent reuses of the same name (loadconfig creates 'NAM...
- **project_inspect_cfx** — Inspect a project's .cfx config file (tasks, databanks, version) without modifying it.
- **project_list** — List all SQ X projects in the user workspace. Falls back to a filesystem scan of <projects_dir>/*/project.cfx when the engine command is unavailable (Build 143 returns 'Error: Not implemented').
- **project_load_and_start** — Atomic loadconfig + syncfromfiles + wait + start. Fixes the race condition where calling `-project action=start` immediately after `-databank action=syncfromfiles` causes the project to run BEFORE the strategies finish loading from disk (re...
- **project_pause** — Pause a running project (most project types support this).
- **project_precheck** — Pre-flight readiness check for running a project. Verifies: engine running, license has time left, project.cfx exists + is structurally valid, every referenced symbol is present in SQ's SQLite registry, history .dat files exist for the refe...
- **project_remove** — Remove (delete) a project from the workspace. Irreversible.
- **project_resume** — Resume a paused project.
- **project_run_to_completion** — Atomic start-and-wait. Loads the project's .cfx, optionally syncs input databanks (fixing the syncfromfiles race condition that produces 'No strategies to retest'), kicks off the project, then blocks until completion. After done, force-sync...
- **project_snapshot** — Snapshot a project's project.cfx to a timestamped backup (project.cfx.bak.<UTC-ts>[.label]) so subsequent project_clone / manual edits are rollback-safe. Pass list_existing=True to enumerate existing snapshots without creating a new one.
- **project_start** — Start a project (Build / Retest / Optimize / WalkForward / etc.). Returns immediately by default; use project_status to poll.
- **project_status** — Get current status of a project (running / idle / progress %). Falls back to filesystem + engine-log-tail inspection if the SQ build does not support `-project action=status`.
- **project_stop** — Stop a running project.
- **project_wait_for_completion** — Block until a project finishes. Combines THREE completion signals so we don't rely on any single one: (a) `-project action=status` reports finished/idle, (b) databank strategy count is stable for N consecutive polls, (c) (optional) log emit...
- **projects_batch_start** — Start up to max_concurrent projects from a list, in order. The engine itself doesn't enforce a global concurrency limit, so this tool stops issuing 'start' calls after max_concurrent. The remainder are returned as 'deferred' for the caller ...
- **projects_batch_status** — Status row per project on disk in one shot. For each project: tries 'sqcli -project action=status' (best when engine is up), falls back to a filesystem-only status when the engine is silent. Returns: name, cfx_present, databanks_present, co...
- **projects_batch_stop** — Stop a list of projects in one call. Best-effort: each stop call is independent; one failure does not stop the rest. Use after projects_batch_status flagged what's running.
- **projects_inventory** — Workspace-wide inventory: every project on disk, its cfx size + mtime, and (optionally) the number of databanks plus per-databank .sqx counts. Read-only, no engine calls.

## regression (2)

- **workspace_cleanup_snapshots** — Remove .cfx.bak.* auto-snapshots older than max_age_days. Defaults to dry_run=True so nothing gets deleted without confirmation. Set dry_run=False to actually delete. Returns the list of files that were (or would be) removed plus bytes reco...
- **workspace_list_snapshots** — List every .cfx auto-snapshot (.cfx.bak.<UTC-ts>[.label]) in the workspace. Useful after cfx_apply_patch / cfx_set_* tools to know what's available to roll back to. Read-only.

## robustness (3)

- **walkforward_consistency_score** — Walk-forward consistency score (0-100) from an explicit folds array. Combines OOS magnitude vs noise, positive-fold fraction, and IS→OOS efficiency into a single defensive score. >=70 = robust, 40-70 = borderline, <40 = weak. Read-only.
- **walkforward_from_folds** — Walk-forward aggregate from an explicit folds array (works without a databank). Each fold is (fold_id, is_metric, oos_metric?). Read-only.
- **walkforward_overfit_flags** — List folds whose OOS metric is below oos_floor_ratio × IS metric (default 0.5 = OOS less than half of IS). Quick overfit screen against a walk-forward databank. Read-only.

## state (6)

- **state_delete** — Remove a key from the persistent state store. Reports whether the key existed and what value was removed.
- **state_export** — Export the persistent state store to a JSON file. Use this to back up state before risky operations, or to transfer state across machines. The exported file is the exact on-disk JSON, so the schema is documented by the state_set tool's beha...
- **state_get** — Read a value from the persistent state store at <projects_dir>/.sq_mcp_state.json (override path via SQ_MCP_STATE). Returns None if the key isn't set. Use this to recall cross-session agent memory like assigned magic numbers, user preferenc...
- **state_import** — Import a JSON snapshot (from state_export) into the persistent state store. merge=True (default): merge per-namespace, incoming wins on key collision. merge=False: REPLACE the entire store with the incoming payload — destructive.
- **state_list** — List the state store contents — namespaces, keys per namespace, and (optionally truncated) value preview. Use to discover what's been remembered from past sessions.
- **state_set** — Set a value in the persistent state store. Value can be any JSON-serializable type. With overwrite=False, refuses if the key already exists. Atomic write (tmp + os.replace) protects against corruption on crash.

## strategy (38)

- **mt5_strategy_pack** — Bundle multiple EA source files into a named subfolder under MQL5/Experts/, assign unique magic numbers, and emit a manifest.json with names + magic numbers + SHA-256s. Use this right after strategy_export_pipeline to ship a deploy-ready po...
- **sqx_diff** — Diff two .sqx archives. Pretty-prints XML members with sorted attributes so reordering doesn't produce noise, then emits a unified diff per member. Defaults to comparing settings.xml, lastSettings.xml, strategy_Portfolio.xml, version.txt — ...
- **sqx_extract_source** — Extract the text-based contents (settings.xml, lastSettings.xml, strategy_Portfolio.xml, version.txt, MANIFEST.MF, etc.) from a .sqx archive without running the engine. Useful for inspecting strategy rules, indicators, MM and fitness settin...
- **sqx_inspect** — Parse a .sqx file directly (no engine call) — extracts version, fitness IS/OOS, presence of equity & orders blobs.
- **sqx_rename** — Rename a .sqx file safely. Validates the new name has no path separators / traversal tokens. Refuses to overwrite an existing file unless overwrite=True. Returns old and new absolute paths.
- **sqx_safe_delete** — Move a .sqx into a quarantine folder (<databank>/.quarantine/) instead of deleting it. Reversible by moving it back. Use this instead of plain rm when removing strategies — it gives you a rollback window. Returns the quarantine path.
- **sqx_safe_delete_purge** — Permanently purge .sqx files from a databank's quarantine folder that are older than max_age_days. Use to reclaim disk space after you're sure you don't want to roll back. Returns the list of purged files plus bytes recovered.
- **strategy_anomaly_check** — Heuristic audit of a single .sqx strategy file. Flags overfit signals (OOS<<IS, unrealistically high profit/DD), thin samples (<30 trades), missing OOS data, ambiguous trades, engine-flagged problems, and suspicious metric distributions. Re...
- **strategy_ascii_equity_chart** — ASCII Unicode-block sparkline of a strategy's equity curve. Use as a quick visual sanity check ('does this curve look smooth or jagged?') without a plotting library. Read-only.
- **strategy_cluster_kmeans** — Cluster a databank's strategies by their metric vectors using k-means. Z-score normalizes the named metrics, runs Lloyd's algorithm (pure Python), and returns per-strategy cluster assignments and per-cluster centroid means + size. Use to sp...
- **strategy_compare** — Compare two or more .sqx files side-by-side. Returns a metrics table sorted by profit_to_dd_ratio (defensive ranking that penalizes large drawdowns), plus warnings: duplicate trades_hash (same exact backtest), wildly different trade counts,...
- **strategy_compare_two** — Side-by-side comparison of two .sqx files: metric-by-metric diff (delta + delta_pct where numeric), plus Pearson correlation on their equity-curve returns. Use this to decide if two top-ranked strategies are diverse or trading the same edge...
- **strategy_deck_one** — Generate a Markdown deck for a single .sqx file (outside any project). Returns markdown text only — caller picks where to write it.
- **strategy_drawdown_periods** — List every discrete drawdown period in a strategy's equity curve: start/end index, peak/trough values, depth (abs + % of peak), duration in samples. Open drawdowns (curve never recovered) have end_idx=None. Use to see how *frequently* a str...
- **strategy_equity_curve_stats** — Compute drawdown / underwater / hit-ratio / recovery-factor stats on the embedded equity sparkline of a .sqx (no Python plotting). Read-only.
- **strategy_explain** — Generate a human-readable explanation of a strategy: name, symbol/timeframe, backtest window, money management, trade outcome, risk warnings, and the optimized parameter table. Useful for surfacing a one-paragraph summary that you (the agen...
- **strategy_export_pipeline** — End-to-end ship-readiness pipeline: rank → diversify → audit. Picks N strategies from a databank using the same diversity rules as portfolio_select_diverse, runs the strategy audit on each one, and returns a traffic-light verdict (green/yel...
- **strategy_history** — Locate every .sqx file in the workspace that shares a given Fingerprint trades_hash. Useful for tracking a strategy's promotion path (which projects' databanks does it live in?) and detecting accidental duplication. Set project_filter to na...
- **strategy_metrics** — Full performance metrics for a single .sqx strategy file: fitness IS/OOS, trades, net profit, drawdown, derived ratios (return %, DD %, profit-to-DD, avg trade, trades/year, OOS/IS ratio), backtest window, symbol/timeframe, instrument metad...
- **strategy_monthly_returns_estimate** — Estimate per-month returns from the equity sparkline of a single .sqx. Assumes uniform per-sample spacing across history_years (which is approximate but useful for shape analysis). Read-only.
- **strategy_naming_suggestion** — Suggest a human-readable strategy name from a .sqx's metrics. Format: '[prefix_]<symbol>_<tf>_oos<NN>_dd<NN>_t<NNN>_<hashprefix>'. Use to rename .sqx files for clarity before shipping to MT5. Read-only — returns the suggested name, doesn't ...
- **strategy_nearest_neighbors** — K nearest neighbors of a target strategy within a databank by z-scored metric distance. Use to find quasi-duplicates of a specific strategy when trade-hash dedupe wasn't enough. Read-only.
- **strategy_orders_export** — Export the trade-by-trade history of a strategy (.sqx) to CSV or XLSX via the engine's `-tools action=orderstocsv|orderstoxlsx`. Produces a sibling file at <sqx_dir>/<sqx_basename>.<ext> with columns: Ticket, Symbol, Type (Buy/Sell), Open/C...
- **strategy_pairwise_distance** — Pairwise Euclidean distance matrix on the top-N strategies (ranked by rank_by) in a databank. Uses z-scored metrics so different magnitudes don't dominate. Returns the full matrix plus each strategy's nearest neighbor. Capped at 50 strategi...
- **strategy_quality_score** — Single 0-100 quality score for a strategy with a sub-component breakdown (fitness, robustness, capital protection, sample size, history coverage) and penalties for critical/high audit findings. Returns a tier label (excellent / good / margi...
- **strategy_query_by_tag** — Query strategies matching ANY of the given tags (OR semantics). Returns {strategy_key: [tags]}. Read-only.
- **strategy_query_by_tag_all** — Query strategies matching ALL of the given tags (AND semantics). Returns {strategy_key: [tags]}. Read-only.
- **strategy_ready_for_deploy** — Run the strategy audit against a single .sqx file (outside any project) and return a green/yellow/red verdict plus the full list of findings. Useful right before handing the file to mt5_deploy_ea, or for spot-checking exported strategies th...
- **strategy_recommendation** — Recommend a single action for a strategy — SHIP / RETEST / TWEAK / DROP — based on audit findings + headline metrics. Returns the action, reasons, and suggested next tool calls. Use when you need a one-shot decision per strategy.
- **strategy_risk_adjusted_metrics** — Risk-adjusted return metrics for a strategy: Sharpe, Sortino, and Calmar ratios from the equity sparkline. Returns are bucketed into approximately monthly periods (history_years × 12) and annualized with periods_per_year=12. risk_free_rate ...
- **strategy_summarize** — One-shot human-readable summary of a strategy .sqx: headline metrics, audit verdict (green/yellow/red), equity-curve geometry (max DD, longest underwater run, hit ratio, recovery factor), and a bullet list. Use as a quick triage tool before...
- **strategy_tag_add** — Attach one or more tags to a strategy. strategy_key is an arbitrary stable identifier (typically the .sqx relpath or trades_hash). Idempotent — re-adding the same tag is a no-op. Persists to the state JSON store.
- **strategy_tag_list_all** — List the full tag system: strategy → tags mapping AND tag → strategies inverse. Plus counts. Read-only.
- **strategy_tag_remove** — Remove specific tags from a strategy. Tags not present on the strategy are silently ignored. Returns which tags were actually removed and the remaining tag list. Persists to state JSON.
- **strategy_tag_rename** — Rename a tag everywhere it appears. Returns count of strategies affected. Useful to canonicalize ad-hoc tag spelling (e.g. 'mean-rev' → 'meanrev'). Persists to state JSON.
- **strategy_untag** — Remove every tag from a strategy. Returns the previously-attached tag list. Idempotent.
- **trade_csv_analyze** — Parse an SQ-exported trade CSV (from strategy_orders_export) and compute trade-level stats: win rate, profit factor, mean/median P/L, best/worst trade, max win/loss streak, per-direction counts. Pure filesystem read — no engine call. Pass E...
- **trade_csv_for_strategy** — Convenience wrapper: given a .sqx file, look for the sibling trade CSV (same stem, .csv) and analyze it. Use this after running strategy_orders_export. Read-only.

## symbols (16)

- **broker_registry** — Extract a broker/source/dataType/barType registry from every existing project's .cfx file. The H2 broker DB is locked while sqcli runs, so use this to discover the internal numeric codes needed when hand-crafting task XMLs for new symbols. ...
- **broker_registry_query** — Read a registry table from SQ X's SQLite `data.db`. Tables: BROKER (broker IDs and names), INSTRUMENTS (instrument metadata: point value, tick size, data type), DATA (symbol/timeframe rows with date ranges), STOCK / STOCK_GROUP / SESSIONS /...
- **cross_instrument_asset_class_split** — Classify each strategy's symbol into an asset class (crypto / forex / futures / equities / unknown) and report split % by weight and count. Useful for portfolio-level allocation reports.
- **cross_instrument_correlation_groups** — Group symbols into likely-correlated clusters by heuristic naming: crypto majors (BTC/ETH), crypto alts, forex USD-quote, JPY pairs, futures, other. Use to assess concentration risk beyond just unique symbol counts.
- **cross_instrument_diversification_score** — Diversification score across symbols (0-100, higher = more diverse). Uses normalized Herfindahl-Hirschman index on symbol weights. Returns a verdict: well_diversified / moderately_diversified / concentrated. Read-only.
- **cross_instrument_group_by_symbol** — Group a list of strategies by symbol. Each group reports n_strategies, total weight, weight share %, mean fitness, and example names. Read-only.
- **cross_instrument_recommend_caps** — Check whether any symbol's weight share exceeds per_symbol_cap_pct. Returns the list of violators with their excess %. Use to validate a portfolio against a max-concentration policy.
- **instrument_add** — Add a new instrument (e.g. for a crypto pair not in SQ defaults).
- **instrument_delete** — Delete an instrument from SQ X.
- **instrument_list** — List all configured instruments in SQ X. Build 143's `-instrument action=list` is broken (parser demands a value for `type` but rejects every value), so this falls back to mining `<InstrumentInfo>` blocks from every project's .cfx file.
- **symbol_coverage_gaps** — Given a list of (symbol, timeframe) pairs the user wants, return the subset that is not on disk. Use to drive a fill-the-gaps data_import loop. Read-only.
- **symbol_freshness_report** — Rank every .dat file in History/ by mtime. Flag files older than stale_days. Use before re-running long backtests on data that might no longer reflect recent market state. Read-only.
- **symbol_list** — List all data symbols (e.g. EURUSD_M1_dukas). Falls back to a filesystem scan of <data_dir>/History when sqcli redirects its output to a file and only the status line ('Data listed.') is visible over HTTP.
- **symbol_normalize** — Normalize a symbol string and produce common alias forms (BTCUSDT, BTC/USDT, BTC-USDT, etc.). Handles crypto quote pairs (USDT, USDC, BUSD, USD) and 6-char forex pairs. Useful before searching the broker registry or matching MT5 symbol nami...
- **symbol_overview** — One-call comprehensive view of a single symbol: on-disk .dat files (any timeframe), data.db registry rows, which projects reference it, and how many strategies in the workspace trade it. Read-only.
- **symbol_timeframe_matrix** — Coverage matrix: for every symbol with a History/ dir, report which of the requested timeframes have .dat files on disk and (optionally) their size + mtime. Returns per-symbol rows including a `missing` list of timeframes. Read-only.

