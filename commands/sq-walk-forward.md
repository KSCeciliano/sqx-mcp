---
description: Configure and run a Walk-Forward optimization on a strategy or project.
argument-hint: <project-name> [strategy.sqx]
allowed-tools:
  - mcp__strategyquant__project_inspect_cfx
  - mcp__strategyquant__project_start
  - mcp__strategyquant__project_status
  - mcp__strategyquant__monitor_start
  - mcp__strategyquant__monitor_status
  - mcp__strategyquant__databank_export
---

The user wants to run a Walk-Forward analysis on `$ARGUMENTS`.

Steps:

1. Call `project_inspect_cfx` on the target project to confirm it has a Walk-Forward task. If it doesn't, tell the user clearly and stop — they need to set the WF task up in the SQ X UI first.
2. If a .sqx path was passed, briefly explain to the user that they need to load it into the project's input databank first (point them at `/sq:test-strategy` for that flow), then continue assuming the strategy is already there.
3. Start the project with `project_start`.
4. Call `monitor_start` with `interval_seconds=120` (Walk-Forward runs are long), `auto_stop_on_critical=True` so a stalled or overfitting run auto-aborts.
5. Periodically (every 5 min) call `monitor_status` and report progress. Highlight the OOS/IS ratio trend — it's the headline metric for WF.
6. When done, `databank_export` to CSV. Summarize: number of WF windows passed, average OOS/IS ratio, parameter drift across windows.
