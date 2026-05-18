---
description: Run a full environment health probe — SQ X install, engine, data registry, MT5, dependencies — and report blockers + warnings.
argument-hint: (no args)
allowed-tools:
  - mcp__strategyquant__environment_health_check
  - mcp__strategyquant__health_check
  - mcp__strategyquant__env_path_tools_check
  - mcp__strategyquant__broker_data_integrity
  - mcp__strategyquant__data_age_check
  - mcp__strategyquant__workspace_overview
---

The user wants a comprehensive health check. They've typed `/sq:health`.

Steps:

1. Call `environment_health_check` first — that's the wide net.
2. If engine HTTP port is reachable, call `health_check` for engine-side details (license, fatal errors).
3. Call `env_path_tools_check` to see what shell deps are missing.
4. Run `broker_data_integrity` with `include_details=False` for a quick orphan count.
5. `data_age_check max_age_days=14` to flag stale data.
6. `workspace_overview` to round out the picture (counts of projects + databanks + sqx files).

Synthesize all of that into:

- **Blockers** (red): anything that prevents the engine from running.
- **Warnings** (yellow): stale data, missing MT5, etc.
- **Healthy** (green): everything that's fine.

End with one concrete next action if there are any blockers (e.g. "start sqcli with `cd ~/Apps/StrategyQuantX && nohup ./sqcli -gui ...`").
