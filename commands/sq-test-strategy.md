---
description: Load a .sqx into the Retester project and run a robustness test on it.
argument-hint: <path-to-strategy.sqx> [project-name]
allowed-tools:
  - mcp__strategyquant__databank_load
  - mcp__strategyquant__project_start
  - mcp__strategyquant__project_status
  - mcp__strategyquant__databank_export
  - mcp__strategyquant__sqx_inspect
  - mcp__strategyquant__monitor_start
  - mcp__strategyquant__monitor_status
---

The user wants to retest the strategy file(s) in: `$ARGUMENTS`.

If they did not specify a project name as the second argument, default to `Retester`.

Workflow:

1. Call `sqx_inspect` on the .sqx first — confirm it parses, capture IS/OOS fitness as the *baseline* the retest should reproduce.
2. Call `databank_load` to import the .sqx into the project's `Results` databank, in a new folder named `incoming/<short-hash>`.
3. Call `project_start` to launch the Retester project. Do NOT wait synchronously.
4. Call `monitor_start` with `interval_seconds=30` and `auto_stop_on_critical=False`.
5. Loop: poll `monitor_status` every 30–60s. After the first three snapshots, summarize trends.
6. Once the project is no longer running (status reports idle / finished), call `databank_export` to dump results to a CSV at `~/Desktop/StrategyQuant/reports/<strategy>-retest-<timestamp>.csv`.
7. Compare retest stats vs the baseline and flag deltas > 10%.

If at any point a `monitor_status` call returns critical alerts, stop the project (`project_stop`) and tell the user why before proceeding.
