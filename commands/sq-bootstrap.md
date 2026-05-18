---
description: First-call orientation for a new session — health probe, workspace state, recent state-store entries, and a recommended next-step prompt.
argument-hint: (no args)
allowed-tools:
  - mcp__strategyquant__session_bootstrap
  - mcp__strategyquant__environment_health_check
  - mcp__strategyquant__workspace_overview
  - mcp__strategyquant__common_workflows
  - mcp__strategyquant__state_list
---

The user typed `/sq:bootstrap` — they want a single-shot orientation.

Steps:

1. Call `session_bootstrap` first. It returns:
   - environment health summary
   - workspace summary (project count, total .sqx files)
   - state-store summary (namespaces, last update)
   - tool category counts
   - common-workflows count

2. If `health_summary.sqx_home_exists=False` or `sqcli_binary_exists=False`, lead with a blocker message and tell the user to set `SQX_HOME` or install sqcli. Don't go further.

3. Otherwise, present a one-paragraph status:
   - "SQ X home: <path>. <N> projects, <M> .sqx files on disk. State store at <path> has <K> namespaces."
   - Recent activity: if the state store has any `session_log` or `last_*` namespace, surface a one-line "last action" summary.

4. Recommend a next step from `common_workflows` based on what's present:
   - 0 projects → "Create a Builder project in the GUI first, then call `/sq:cfx-lint <name>`."
   - Projects exist but engine is unreachable → "Start sqcli with `cd ~/Apps/StrategyQuantX && nohup ./sqcli -gui ...`."
   - Engine reachable, projects exist → suggest `portfolio_summary`, `portfolio_audit`, or `databank_top_bottom` on the most-recently-modified project.

5. End with three concrete tool calls the user can run next (e.g. `portfolio_audit project=Builder`).

This command is intended as the first thing an agent runs each session, before doing any meaningful work.
