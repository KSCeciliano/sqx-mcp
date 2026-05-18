# sq-mcp examples

End-to-end workflows showing how the tools compose. Each example assumes an MCP client (Claude Code, etc.) with the plugin attached. Tool names below are the MCP-namespaced form; in conversation you'd usually say "use `portfolio_audit`" etc.

> All scenarios assume `sqcli -gui` is already running on port 5050. If not, see [README.md](README.md) for the engine bring-up.

## 1. Build a portfolio from scratch (BTCUSDT crypto)

```text
1. /sq:bootstrap                                 (orient)
2. preset_crypto_24_7 project=Builder            (disable forex filters, switch SLPT to %)
3. cfx_set_data_range project=Builder
       date_from=2023.01.01 date_to=2026.05.01   (3y BTCUSDT window per CLAUDE.md)
4. preset_recommended_genetic project=Builder    (Population=100, Generations=100, 50% OOS)
5. cfx_set_max_strategies project=Builder
       max_strategies=1000
6. cfx_lint project=Builder                      (sanity)
7. project_precheck project=Builder              (license, data, cfx)
8. project_start project=Builder nowait=true
9. monitor_start project=Builder auto_stop=False (watch)
10. monitor_status project=Builder               (poll)
11. databank_force_sync project=Builder name=Results
12. portfolio_audit project=Builder              (triage the run)
13. portfolio_select_diverse project=Builder n=10
14. strategy_export_pipeline project=Builder n=10  (audit each, traffic light)
15. pipeline_export_to_mt5 project=Builder
       pack_name=btc_v1 n=5 deploy=False         (plan)
16. pipeline_export_to_mt5 ... deploy=True       (actually copy)
17. mt5_verify_deployment expected=...           (after MT5 restart)
```

## 2. Quick triage of an existing run

```text
1. portfolio_summary project=Builder
2. portfolio_concentration project=Builder       (any one-edge clusters?)
3. portfolio_audit project=Builder               (systemic findings)
4. databank_correlation_matrix project=Builder top_n=20
5. portfolio_diversity_score project=Builder
6. databank_top_bottom project=Builder metric=fitness_oos k=10  (best vs worst)
```

## 3. Regression-check after a Builder iteration

```text
Before:
  databank_snapshot_metrics project=Builder label="pre-tweak"

[Make a config change, e.g. cfx_set_genetic_options population_size=200]

  project_start project=Builder
  monitor_status until stopped
  databank_force_sync project=Builder
  databank_snapshot_metrics project=Builder label="post-tweak"
  databank_regression_check project=Builder
       baseline=pre-tweak.json current=post-tweak.json
```

The check returns green/yellow/red per metric (fitness_oos, drawdown_pct, return_pct, profit_to_dd_ratio). If any metric regresses by more than 10% (default), it's red.

## 4. Risk-aware portfolio construction

```text
1. portfolio_risk_metrics project=Builder
   → see the DD distribution
2. databank_partition project=Builder
       metric=drawdown_pct threshold=20 → keep "below" group only
3. portfolio_select_diverse from the survivors
4. portfolio_capital_allocation n=5 fitness_bias=0.3
   → inverse-DD weighted (lower DD gets more capital)
5. portfolio_combined_equity_explicit items=[{sqx_path, weight}, ...]
   → see what the combined curve looks like
```

## 5. Pre-flight a strategy for live deployment

```text
1. strategy_summarize sqx_path=/path/to/strat.sqx
   → headline + audit verdict
2. strategy_quality_score sqx_path=...
   → single 0-100 + component breakdown
3. strategy_risk_adjusted_metrics sqx_path=...
   → annualized Sharpe / Sortino / Calmar
4. strategy_drawdown_periods sqx_path=...
   → how often it goes underwater
5. strategy_equity_curve_stats sqx_path=...
6. pre_live_checklist path=/path/to/strat.sqx
   → STRICT pass-fail gate (require_min_trades, max_drawdown_pct, ...)
```

## 6. Compare two Builder runs of the same project

```text
1. workspace_export_projects projects=[Builder] label=run_A
   (manual: rename the archive afterwards)
2. [run again, possibly with cfx_set_genetic_options]
3. workspace_export_projects projects=[Builder] label=run_B
4. cfx_compare_paths cfx_a=... cfx_b=...
   → structural diff
5. portfolio_ab_test
   project_a=Builder databank_a=Results
   project_b=Builder_old databank_b=Results
   → per-metric verdict per side
6. databank_correlation_matrix on each
   → diversity comparison
```

## 7. Data hygiene

```text
1. broker_data_integrity   (registry vs disk mismatch)
2. data_age_check max_age_days=14   (stale .dat files)
3. data_workspace_quality_report
4. data_bar_density symbol=BTCUSDT timeframe=M1
5. history_disk_inventory
```

## 8. MT5 deployment with magic-number assignment

```text
1. mt5_locate
2. mt5_assign_magic_numbers
       ea_paths=[/sq/builder/.../strat1.mq5, .../strat2.mq5]
       namespace=acct-A
3. mt5_patch_magic_in_source ea_path=... new_magic=...   (per file)
4. mt5_strategy_pack
       ea_paths=[...] pack_name=btc_v1 namespace=acct-A
5. mt5_verify_deployment expected=[...]   (after MT5 restart)
6. mt5_log_tail log_type=mql5   (debug)
```

If you have an MT5 backtest report:

```text
mt5_compare_with_sqx
    report_path=Report.htm
    sqx_path=strat.sqx
    warn_threshold=0.10
```

## 9. CFX template library

```text
1. cfx_template_capture project=Builder template_name=btc_24_7_v1
2. project_clone project=Builder target=Builder_ETH
3. cfx_set_instrument project=Builder_ETH symbol=ETHUSDT
4. cfx_template_apply project=Builder_ETH template_name=btc_24_7_v1
   → replicate the tuned settings on the new project
```

## 10. State persistence across sessions

```text
Save:
  state_set namespace=magic_numbers key=acct-A.BTCUSDT_strat1 value=123456

Recall in next session:
  state_get namespace=magic_numbers key=acct-A.BTCUSDT_strat1
  state_list namespace=magic_numbers
```

## 11. Emergency hung-build detection

```text
1. monitor_status project=Builder
2. engine_watchdog quiet_threshold_seconds=300
   → 'responsive' / 'quiet' / 'unresponsive' / 'dead'
3. engine_recent_errors
   → fatal/error lines from the log tail
4. If state=='dead': engine_start
   If state=='unresponsive': engine_stop then engine_start
   If state=='quiet': wait — JVM is probably in data prep
```

## 12. Visualization (terminal-friendly)

```text
strategy_ascii_equity_chart sqx_path=/path/to/strat.sqx width=80
databank_metric_histogram project=Builder metric=fitness_oos bins=20
```

The histogram returns a Markdown-friendly text rendering you can paste straight into a conversation.

## 13. Read-only "what could I do here?" discovery

```text
tools_catalog name_contains="cfx"        → all CFX-related tools
common_workflows                         → curated recipes
cfx_recommend_for_symbol symbol=BTCUSDT goal=balanced
   → recommended settings bundle for BTCUSDT
```

## Escape hatch for unsupported sqcli commands

```text
engine_call command="-foo action=bar"
```

Use only when a typed tool doesn't cover your need.
