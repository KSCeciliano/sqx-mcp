---
description: Snapshot a databank, run the Builder, snapshot again, then report green/yellow/red regression across the run.
argument-hint: <project> [databank=Results]
allowed-tools:
  - mcp__strategyquant__databank_snapshot_metrics
  - mcp__strategyquant__databank_regression_check
  - mcp__strategyquant__project_start
  - mcp__strategyquant__project_status
  - mcp__strategyquant__monitor_start
  - mcp__strategyquant__databank_force_sync
  - mcp__strategyquant__portfolio_ab_test
---

The user wants to rebuild `$ARGUMENTS` and quantify whether the new run is actually better.

Steps:

1. Parse: `project [databank]`. Default databank=Results.
2. **Confirm** with the user before starting — Builder runs consume trial license time.
3. Snapshot the current databank: `databank_snapshot_metrics` with `label="pre-rebuild"`. Note the snapshot path.
4. Start the project: `project_start` with `nowait=True`.
5. Optionally start monitor with `auto_stop=False, alert_only=True` to track progress.
6. Wait for completion (poll `project_status` until stopped/finished).
7. `databank_force_sync` to flush JVM memory to disk.
8. Snapshot again: `databank_snapshot_metrics` with `label="post-rebuild"`.
9. `databank_regression_check` comparing baseline vs current — report green/yellow/red per metric.
10. As a sanity backup, `portfolio_ab_test` between the *same project* on itself reading old vs new files isn't possible directly; instead, save the pre snapshot to a sibling databank if you want a side-by-side later.

Output: a one-paragraph verdict (improved / regressed / stable per metric) plus the raw regression report.
