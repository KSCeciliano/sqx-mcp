# sq-mcp

A bridge between Claude (or any MCP client) and StrategyQuant X.

I built this because driving SQ X by hand was slowing me down. Every backtest, every retest, every parameter tweak meant click-throughs in the GUI. I wanted to describe what I needed in plain language and have it happen  clone a project for a new symbol, run the build, watch the progress, filter survivors, optimize the best ones, and end up with a shortlist on disk without my hand on the mouse.

The plugin runs as an MCP server and talks to SQ X through its built-in HTTP API on port 5050.

## What it does

**482 tools** — see [TOOLS.md](TOOLS.md) for the full catalog, [EXAMPLES.md](EXAMPLES.md) for end-to-end workflows, [AGENT_GUIDE.md](AGENT_GUIDE.md) for AI-agent usage patterns, [ARCHITECTURE.md](ARCHITECTURE.md) for the module map, [CLAUDE.md](CLAUDE.md) for new-agent onboarding, and [MAINTENANCE.md](MAINTENANCE.md) for the playbook on module reorg / mypy / profiling / PyPy / PyO3 — grouped into twenty-plus areas:

**Driving SQ X.** Listing, starting, stopping, pausing and resuming projects. Loading and saving .cfx configs. Managing databanks (count, list, force-sync from JVM memory to disk, export to CSV/XLSX). Importing, updating and exporting historical data. Multi-project batch orchestration with capacity limits.

**Authoring projects.** Cloning a working template, retargeting it to a different symbol with proper broker codes pulled from SQ's registry, patching date ranges, timeframes, fitness criteria, money-management, SL/PT ranges, genetic-evolution caps, max-strategies, and individual indicator/operator blocks  each with auto-snapshot and inverted-range validation. Structural validation of a .cfx before loading it.

**Static analysis.** Parsing .mq5, .sqx and .cfx files in pure Python without touching the engine. Useful for auditing a strategy before running it  flagging things like missing stop-loss, default magic numbers, exit-after-1-bar on tiny timeframes. Diffing two .sqx archives to see what actually changed between iterations.

**Portfolio analytics.** Dedupe by trades-hash, defensive composite ranking, diversified top-N selection, Pearson correlation matrix on equity sparklines, Herfindahl concentration scores, distributional risk metrics, inverse-DD capital allocation, and synthetic portfolio equity simulation by summing per-strategy curves.

**A/B and regression.** Compare two databanks side-by-side, snapshot metrics for later iterations, and detect green/yellow/red regressions across runs.

**MT5 deployment.** Locate MT5, list deployed EAs, deploy single .mq5/.ex5 files, assign collision-free magic numbers, surgically patch magic-number literals in source, verify a deployed pack against expected SHA-256s, pack a portfolio into a named subfolder with a JSON manifest, and chain the full SQ → MT5 pipeline (pick → audit → magic-assign → deploy) in one tool call.

**Data quality.** Cross-check SQ's SQLite registry against on-disk .dat files, bar-density coverage analysis (expected vs estimated), staleness check by mtime, .dat header diff for verifying incremental pulls, workspace-wide quality report.

**Live monitoring.** Polling a running build, watching fitness curves, aborting runs that have stalled. Auto-stop on configurable rules (low fitness, OOS divergence, stalled growth).

**Meta + health.** Self-describing tool catalog, full-environment health probe (SQ X install, sqcli binary, engine port, data.db, MT5 install, Python deps), workspace overview, $PATH dependency check, common-workflow recipes.

**Visualization + reports.** ASCII Unicode-block sparklines of equity curves, ASCII histograms of databank metrics, CSV exports of portfolio metrics, Markdown decks for top-N strategies with per-strategy summary sections.

**Persistence.** A flat JSON state store at `<projects_dir>/.sq_mcp_state.json` (override with `SQ_MCP_STATE`) for cross-session agent memory (magic numbers, preferences, session notes), workspace fingerprint hashing, manifest diffing.

**Risk math.** Position-sizing for crypto / forex / generic instruments (risk-percent + Kelly + pyramiding + risk-of-ruin), Monte Carlo bootstrap over equity curves (terminal-return + max-DD distribution + probability-of-drawdown), walk-forward fold analysis (IS/OOS slippage, consistency scoring), k-means strategy clustering and pairwise distance, performance attribution (by strategy, by month, by regime, with Herfindahl concentration), and live-vs-backtest drift detection (Wilson CI on win rate, z-score on mean trade, two-sample KS).

**Risk profiles + MT5 codegen.** Curated `ultra_conservative / conservative / moderate / aggressive` profiles that bundle risk-per-trade, max-DD threshold, max-concurrent-positions and MM-method parameters; apply or compare against any strategy. Generate production-shaped MT5 EA scaffolds with proper risk-percent sizing, magic-number filtering, max-lot caps, and optional break-even / trailing stop, or idempotently inject those guardrails into an existing .mq5 source.

**Tagging + notifications.** Label strategies with arbitrary tags (`production`, `meanrev`, `do_not_deploy`), query by tag (AND / OR), rename tags everywhere. Format build status, audit summaries and deployment manifests for Slack / Discord / email / one-line cron output.

**Symbol intelligence.** Coverage matrix across every symbol on disk for the requested timeframes, freshness report flagging stale .dat files, single-symbol deep dive, gap analysis for fill-the-gaps imports, and a normalizer that resolves common alias forms (BTCUSDT ↔ BTC/USDT ↔ BTC-USDT, …).

**Robustness & promotion.** Brittle-strategy detection (Gini + Pareto on trade PnLs + consecutive loss streaks), stress tests (slippage, skip-best, random-skip, worst-case streak), declarative alert rules with curated workspace defaults, and a gated promotion workflow that combines metrics + audit + drift + stress into a single "approved / blocked" verdict.

**Operations.** Backtest-cadence recommender (no_action / retest / rebuild driven by data freshness and drift), strategy lineage tracking (parent → child relationships persisted with cycle safety), tag system with AND/OR queries and renaming, full risk-adjusted ratio suite (Sharpe, Sortino, Calmar, MAR, Information, Omega, Pain Index, Ulcer Index), explicit-input cost model (commission + slippage overlay + break-even cost + sensitivity grid), calendar-effects analysis (by hour, day-of-week, month, quarter, custom sessions), benchmark comparison (alpha, beta, excess return, outperformance streaks), and Markdown annual/period report composer.

**Statistics & pipelines.** Statistical hypothesis testing (paired t-test, sign test, Wilcoxon signed-rank, Welch's t-test) to defend "strategy A is really better than B" claims. Full "ship to live" pipeline orchestrator that chains promotion gates → tagging → lineage registration → alerting in one call with dry-run preview. Cross-instrument diversification scoring (HHI on symbol weights) and asset-class splits. Batch audit pipeline that applies promotion gates to every .sqx in a databank, producing a ranked ready / marginal / blocked queue. Workspace dashboard with project inventory, tag inventory, lineage tree size, and stale-data flagging, rendered as Markdown for morning briefings.

**Tail risk + overfitting diagnostics.** Historical / parametric VaR + CVaR (Expected Shortfall), gain-to-pain and tail ratio, Deflated Sharpe Ratio (Bailey/Lopez de Prado) and haircut Sharpe to penalize Sharpe for multiple-testing, Probability of Backtest Overfitting (PBO) from IS/OOS Sharpe pairs, minimum-track-record-length to claim significance vs a benchmark.

**Regime + advanced statistics.** Rolling volatility and trend regime classifiers, regime-conditioned PnL split with trade/skip recommendations, sample skewness / excess kurtosis / Jarque-Bera normality, single-lag autocorrelation + Ljung-Box, Wald-Wolfowitz runs test, IRR (Newton-Raphson) and time-weighted return.

**Fingerprint + portfolio optimization.** Deterministic strategy fingerprints (param hash + numeric bucket hash + equity-curve digest) with Jaccard+Pearson similarity scoring and near-duplicate grouping. Equal-weight / inverse-volatility / iterative risk-parity / coordinate-descent minimum-variance allocators, plus diversification ratio.

**Trade replay + workspace operations.** Replay a trade list under different commission/slippage, position-sizing policy (fixed_lot / fixed_fractional / percent_risk), risk caps (per-trade, daily, weekly), or regime filter  and compare to the original. Clone .sqx files, atomically archive sets of strategies to zip, restore archives with path-traversal filtering, batch-rename by regex with collision safety.

**Code generation extras.** Pine Script v5 indicator/strategy scaffolds (SMA/EMA crossover, RSI, MACD) with TP/SL, sizing and date filter, plus alert webhook templates and best-effort MT5 → Pine translation. MT5 .set/.ini parser, diff, merge, render, and risk-profile applicator.

**Data health.** Bar-interval gap detection, freshness gates, monotonicity checks, expected-vs-actual bar count with market-hours factor, and an end-to-end `workflow_data_health_check` that combines them.

**Lopez de Prado / AFML techniques.** Triple-barrier labeling for entries (profit-target / stop-loss / time-horizon barriers), meta-labeling for stacking a secondary classifier on top of a primary signal, bet sizing from probabilities, fractional differentiation to preserve long memory while achieving stationarity, Combinatorial Purged Cross-Validation with embargo for distribution-of-OOS-paths backtesting, and AFML Ch.2 information-driven bar construction (tick / volume / dollar / imbalance bars).

**Hansen SPA + reality check + System Parameter Permutation.** Multiple-testing-corrected p-value for "no strategy beats the benchmark" via stationary bootstrap (White's Reality Check + Hansen's studentized SPA + per-strategy consistency report). QuantAnalyzer's flagship System Parameter Permutation: perturb each parameter ±x%, run the distribution, recommend the median-metric (typical out-of-sample) parameter set rather than the in-sample best.

**Volatility estimators.** Close-to-close, Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang — all five high-frequency OHLC volatility estimators with side-by-side comparison.

**Walk-Forward Matrix + Efficiency.** WF efficiency = OOS/IS metric ratio across folds with verdict (excellent / good / marginal / overfit / destructive). Anchored-vs-rolling WF comparison. Auto-recommendation of the best (IS, OOS) combo from a WF matrix, weighted by sqrt(oos_bars) for statistical power.

**Crypto perpetual-future corrections.** Funding-rate-adjusted PnL (subtracts 8-hour funding payments from longs, adds to shorts; flags when funding > 10% of strategy PnL), liquidation-price + distance calculator, intra-bar liquidation stress test, contango / neutral / backwardation funding-regime classifier.

**Mean-reversion diagnostics.** Hurst exponent (R/S), Ornstein-Uhlenbeck half-life, CUSUM change-point detector, heuristic ADF stationarity test.

**Information theory.** Shannon entropy, KL divergence, mutual information (catches non-linear dependence that correlation misses), Schreiber's transfer entropy for directional information flow, portfolio-level Jensen-Shannon diversity index.

**Execution metrics + classic levels.** VWAP and TWAP computation, slippage measurement in bps with verdict, market-participation rate with impact-tier labeling, per-session VWAP split. Classic / Fibonacci / Camarilla / Woodie / DeMark pivot points with a side-by-side comparison tool.

**Bar resampling + MCP-specific safety.** Resample OHLCV to any new interval (M1→M5, M5→H1, …) without re-importing data, with session alignment and timeframe-string conversion. Plus prompt-injection detector and recursive output sanitizer that escapes Markdown fences and fake role tags so untrusted sqcli output can't hijack the LLM, plus naive PII redaction.

**Rolling-window live monitoring.** Rolling Sharpe, drawdown, volatility, correlation, beta/alpha, and win rate over user-supplied trailing windows. Catch Sharpe decay, edge erosion, or correlation drift earlier than a once-a-week full-backtest refresh.

**Drawdown shape + pairs trading.** Per-episode drawdown breakdown (duration / depth / drop bars / recovery bars / ongoing), time-underwater fraction with verdict, pain decomposition by depth bucket. Engle-Granger cointegration test for pairs trading, spread series with Ornstein-Uhlenbeck half-life, rolling z-score signal, and O(N²) pair scan to find the most-cointegrated combinations across a series set.

**Live exposure + signal quality.** Net / gross exposure, HHI concentration, leverage breach detection vs policy, correlation-weighted effective exposure, portfolio VaR decomposition by symbol contribution. Information Coefficient and IC decay across horizons, quantile sort with hit-rate-by-bucket and monotonicity check, signal-to-noise ratio in dB, signal turnover (raw or normalized).

A typical session looks like:

```
Me:     Analyze ~/Downloads/myStrategy.mq5

Claude: One critical risk. The strategy declares a per-trade risk of $100 via
        sqMMFixedAmount, but sl=0  that risk parameter is fiction without an
        actual stop. Three medium risks too: default magic number 0 (will
        collide with anything else running on the same chart), ExitAfterBars=1
        on M5 (positions close at the next bar regardless of P/L), no time
        filter on a strategy that's clearly intraday.
```

Or:

```
Me:     Start the Builder, monitor every minute, stop it if it stalls.

Claude: Started. After 6 minutes: 87 strategies, growth flat for the last
        3 cycles, OOS/IS ratio drifting down. Stopped. The building blocks
        are probably too narrow  try widening the indicator pool or
        relaxing the entry conditions.
```

## What it doesn't do (honest)

- **Export .sqx to .mq5.** GUI-only in Build 143. The CLI doesn't expose the MQL5 codegen. You still have to click "Export to MT5" yourself when you want to take a strategy live.
- **Edit strategy logic.** .sqx files are partially compiled binaries. You can read the rules, change SL/PT or date ranges, retarget the symbol, but you can't rewrite the entry/exit logic from outside.
- **Create new brokers.** The H2 database holding broker metadata is locked while the engine is running. The forex/general registry actually lives in a SQLite file that I can read without locking, but writes still need the GUI.
- **Windows support is validated primarily against SQX 144 Full.** The current best-validated target is SQX 144 on Windows-native deployment. SQX 141 appears practical with caveats. SQX 136 is not currently a practical support target without extra engine-compatibility work.

## Install

If you have `uvx` (recommended  no Python setup needed):

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

Drop that in `~/.claude.json` (or whichever config your MCP client uses) and restart it.

From source:

```bash
git clone https://github.com/DAVIDAROCA27/sqx-mcp
cd sqx-mcp
pip install -e ".[dev]"
sq-mcp     # runs the server on stdio
```

## Configuration

All environment variables are optional.

| Variable | What | Default |
|---|---|---|
| `SQX_HOME` | StrategyQuant X install root | Auto-detected from `~/Apps/StrategyQuantX`, `/opt/StrategyQuantX`, `C:/Program Files/StrategyQuant X`, `/Applications/StrategyQuantX.app/...` |
| `SQX_HTTP_URL` | Base URL of the SQ HTTP API | `http://localhost:5050` |
| `SQX_HTTP_PORT` | Port to use when spawning sqcli | `5050` |
| `SQX_LOG_PATH` | Where sqcli writes its log | Auto-detected (`/tmp/sqcli.log` if present) |

If sqcli is already running on :5050 when the plugin starts up, it attaches to that instance instead of spawning its own. So if you normally start the GUI manually, the plugin slots in alongside without a fight.

## Tools

All tools are namespaced `mcp__strategyquant__*` when invoked. Full catalog with descriptions is in [TOOLS.md](TOOLS.md). A short tour by category:

**Projects + cfx authoring**
`project_list`, `project_start`, `project_stop`, `project_pause`, `project_resume`, `project_status`, `project_inspect_cfx`, `project_config`, `project_remove`, `project_force_remove`, `project_load_and_start`, `project_precheck`, `project_clone`, `project_create_from_template`, `project_snapshot`, `cfx_validate`, `cfx_apply_patch`, `cfx_set_instrument`, `cfx_set_fitness_criterion`, `cfx_set_money_management`, `cfx_set_data_range`, `cfx_set_trade_caps`, `cfx_set_sl_pt_range`, `cfx_set_genetic_options`, `cfx_set_max_strategies`, `cfx_list_building_blocks`, `cfx_toggle_building_blocks`, `cfx_configure_robustness`, `cfx_configure_walkforward`, `projects_batch_status`, `projects_batch_start`, `projects_batch_stop`, `projects_inventory`, `workspace_overview`

**Databanks + .sqx**
`databank_list`, `databank_count`, `databank_save`, `databank_load`, `databank_clear`, `databank_export`, `databank_force_sync`, `databank_top_n`, `databank_filter`, `databank_promote`, `databank_merge`, `databank_snapshot_metrics`, `databank_regression_check`, `databank_correlation_matrix`, `sqx_inspect`, `sqx_extract_source`, `sqx_diff`, `strategy_orders_export`

**Portfolio + strategy analytics**
`portfolio_dedupe`, `portfolio_rank`, `portfolio_summary`, `portfolio_select_diverse`, `portfolio_ab_test`, `portfolio_risk_metrics`, `portfolio_concentration`, `portfolio_capital_allocation`, `portfolio_combined_equity`, `portfolio_combined_equity_explicit`, `strategy_compare_two`, `strategy_summarize`, `strategy_equity_curve_stats`, `strategy_monthly_returns_estimate`, `strategy_export_pipeline`, `strategy_ready_for_deploy`

**MT5 deployment**
`mt5_locate`, `mt5_list_experts`, `mt5_list_indicators`, `mt5_deploy_ea`, `mt5_log_tail`, `mt5_assign_magic_numbers`, `mt5_patch_magic_in_source`, `mt5_verify_deployment`, `mt5_strategy_pack`, `pipeline_export_to_mt5`

**Historical data + quality**
`data_import`, `data_update`, `data_export`, `data_list_local`, `data_timezones`, `data_coverage_check`, `history_data_summary`, `history_disk_inventory`, `broker_data_integrity`, `data_bar_density`, `data_workspace_quality_report`, `data_dat_header_diff`, `data_age_check`

**Symbols, instruments, registry**
`symbol_list`, `instrument_list`, `instrument_add`, `instrument_delete`, `data_registry_lookup`, `broker_registry_query`

**Static analysis (no engine needed)**
`analyze_mq5_file`, `compare_mq5_files`, `inspect_cfx`, `inspect_cfx_robustness`, `strategy_audit`, `project_audit`, `workspace_audit`, `pre_live_checklist`

**Engine lifecycle + diagnostics**
`engine_start`, `engine_stop`, `engine_status`, `engine_log_stream`, `license_info`, `task_progress`, `health_check`, `environment_health_check`, `tools_catalog`, `env_path_tools_check`

**Active monitoring**
`monitor_start`, `monitor_stop`, `monitor_list`, `monitor_status`, `monitor_alerts`, `monitor_default_rules`

## Slash commands

These are higher-level workflows the plugin ships with:

- `/sq:analyze-mq5 <file.mq5>`  risk audit on one or more EAs.
- `/sq:test-strategy <file.sqx>`  load and retest with default robustness.
- `/sq:walk-forward <project>`  orchestrate a Walk-Forward optimization.
- `/sq:monitor-build <project> [interval] [auto-stop|alert-only]`  supervise a long run.
- `/sq:portfolio-build <folder>`  assemble a diversified portfolio.
- `/sq:data-fix <symbol> [tf]`  diagnose and repair data issues.
- `/sq:ship-to-mt5 <project> <databank> <pack_name> [N]`  pick → audit → deploy a portfolio to MT5.
- `/sq:portfolio-deck <project> [databank] [N]`  Markdown deck of top-N strategies with audit verdicts.
- `/sq:rebuild-and-compare <project>`  snapshot, rebuild, snapshot, regression-check.
- `/sq:health`  full environment audit with traffic-light blockers.
- `/sq:cfx-lint <project>`  heuristic lint of a Builder/Optimizer config.
- `/sq:bootstrap`  first-call orientation: health + workspace + state + next-step suggestion.
- `/sq:overview`  workspace-wide snapshot: running projects + recent activity + data quality + state fingerprint.
- `/sq:risk-audit <project>`  run every risk analysis tool in sequence: concentration, audit, diversity, correlation, distribution.

## Subagents

For tasks where the model benefits from running in its own context window:

- `sq-strategy-reviewer`  second-opinion review with an explicit ship/no-ship verdict.
- `sq-build-watcher`  babysits long-running builds.
- `sq-data-curator`  keeps historical data clean.
- `sq-portfolio-architect`  designs portfolios from candidate strategies.

## How it's organized

```
src/sq_mcp/
  engine.py            sqcli subprocess + HTTP client + log tailing
  config.py            SQ X install path detection
  monitor.py           active build monitor with rules
  parsers/             pure-Python parsers for .mq5/.sqx/.cfx
  tools/
    projects.py        project + cfx + engine lifecycle
    databanks.py       databank + .sqx + result-set tools
    symbols.py         symbol/instrument + SQLite registry reads
    data.py            history import/update/export
    monitor.py         monitor-session MCP tools
    analysis.py        static-analysis MCP tools
    audit.py           heuristic risk-finding scans
    robustness.py      Walk-Forward / Monte-Carlo / Retest config helpers
    cfx_config.py      surgical cfx patchers (fitness, MM, dates)
    cfx_advanced.py    deeper cfx patches (SL/PT, genetic, building blocks)
    portfolio.py       dedupe / rank / summary / diverse selection
    portfolio_risk.py  distributional risk + HHI + capital allocation
    comparison.py      portfolio A/B + Pearson correlation matrix
    pipeline.py        end-to-end picks → audit → traffic-light verdict
    pipeline_mt5.py    SQ databank → MT5 pack mega-pipeline
    regression.py      snapshot / regression-check across iterations
    integrity.py       SQLite registry ↔ on-disk .dat cross-checks
    data_quality.py    bar density, header diff, staleness
    mt5.py             MT5 install detection + EA deploy + log tail
    mt5_extra.py       magic-number assignment + verification + pack
    batch.py           multi-project orchestration (start/stop/status)
    strategy_inspect.py per-strategy summary + compare + curve stats
    analytics.py       combined equity simulation + monthly returns
    meta.py            self-describing catalog + health probe
    diagnostics.py     engine health + license + recent fatal-error tail
```

The tool layer talks to two surfaces: SQ X's HTTP API on :5050 for live engine work, and the filesystem for offline parsing and the SQLite data registry. Build 143's CLI has a handful of real quirks I've worked around (commands that just say "Not implemented", parser bugs on quotes and spaces, databank sync that's async-without-promise)  those are noted in code comments and in the memory notes if you load the project in Claude Code.

## Development

```bash
pip install -e ".[dev]"
pytest -q                     # 520+ unit + parser + integration tests
ruff check src tests          # lint
```

To use the in-development version in your MCP client, point its config at the local `sq-mcp` script from your venv's `bin` directory instead of `uvx`.

## License

MIT. See [LICENSE](LICENSE).
