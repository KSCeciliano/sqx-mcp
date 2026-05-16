---
description: Attach a live monitor to a running SQ X build / optimization and supervise it.
argument-hint: <project-name> [interval-seconds] [auto-stop|alert-only]
allowed-tools:
  - mcp__strategyquant__project_status
  - mcp__strategyquant__monitor_start
  - mcp__strategyquant__monitor_status
  - mcp__strategyquant__monitor_alerts
  - mcp__strategyquant__monitor_stop
  - mcp__strategyquant__project_stop
---

The user wants you to actively watch a running SQ X project: `$ARGUMENTS`.

Parse arguments:
- `<project-name>` — required.
- Optional second token: integer interval in seconds. Default 60.
- Optional third token: `auto-stop` or `alert-only` (default).

Steps:

1. Call `project_status` to confirm the project is actually running. If not, stop and tell the user.
2. Call `monitor_start` with the parsed interval and `auto_stop_on_critical = (token == "auto-stop")`.
3. Enter a supervisory loop: every interval, call `monitor_status`. Print:
   - `strategies generated: <count> (+<delta> since last)`
   - `median fitness IS / OOS: <vals>`
   - `OOS/IS ratio: <val>` (flag if < 0.5)
   - any new alerts since last poll, in red bullet form
4. If a critical alert fires AND the user chose `auto-stop`, the monitor will already have stopped the project — confirm with the user, then suggest next steps.
5. If the user chose `alert-only`, surface the alert and ASK whether to stop the project before continuing.

Stop monitoring (`monitor_stop`) when the project finishes or the user asks you to.
