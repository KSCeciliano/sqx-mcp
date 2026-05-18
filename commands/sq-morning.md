---
description: Morning briefing — dashboard + active alerts + freshness-driven schedule recommendations in one call.
argument-hint: (no args)
allowed-tools:
  - mcp__strategyquant__workflow_morning_briefing
  - mcp__strategyquant__dashboard_overview
  - mcp__strategyquant__dashboard_render_markdown
  - mcp__strategyquant__alert_workspace_defaults
  - mcp__strategyquant__alert_rules_batch
---

The user typed `/sq:morning`. Goal: give them a single Markdown report
they can read in 30 seconds at the start of a session.

Steps:

1. Call `workflow_morning_briefing` with default args. It returns:
   - `dashboard_markdown` — projects, tags, lineage, stale data.
   - `alerts_markdown` — any triggered workspace alerts.
   - `schedule_actions` — short list of stale data refreshes.
2. Concatenate the three Markdown blocks (dashboard first, then alerts,
   then a brief "Recommended actions" section listing
   `schedule_actions` if non-empty).
3. Surface the most pressing 1-3 actions the user should take next at
   the very top of the report (e.g. "Refresh BTCUSDT M1 data; resume
   build X; review Strategy Y drift").

Do not run any write tool. This is pure read-only briefing.
