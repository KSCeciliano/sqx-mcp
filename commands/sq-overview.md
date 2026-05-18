---
description: Workspace-wide overview — every project's status + databank counts + recent activity, with one recommendation per project.
argument-hint: (no args)
allowed-tools:
  - mcp__strategyquant__workspace_overview
  - mcp__strategyquant__projects_batch_status
  - mcp__strategyquant__workspace_list_snapshots
  - mcp__strategyquant__data_workspace_quality_report
  - mcp__strategyquant__workspace_fingerprint
---

The user typed `/sq:overview`. Goal: in one screen, show what's in the workspace and what state it's in.

Steps:

1. `workspace_overview` → top-level counts + most-recently-modified projects.
2. `projects_batch_status` → who's running, who's stopped.
3. `workspace_list_snapshots` → recent .cfx auto-snapshots (where the user was working).
4. `workspace_fingerprint` → quick state hash.
5. (Optional, if quick) `data_workspace_quality_report` for stale data flags.

Present the information as:

```
sq-mcp workspace overview — <now>

Projects (N total):
  • <project>    [running|stopped|paused]   <databanks> databanks, <sqx_total> .sqx  (mtime: ...)
  ...

Recent activity (last 10 snapshots):
  • <project> — <snapshot_filename>   <age>

Data quality:
  • <K> stale .dat files (>14 days old)
  • <K> registry-vs-disk orphans

Fingerprint: <16-char>

Suggested next step:
  → ... (pick one specific recommendation based on the data above)
```

Do not run any write tool. This is pure orientation.
