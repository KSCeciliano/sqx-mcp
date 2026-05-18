---
description: Heuristic lint of a project's .cfx — flags missing OOS holdout, undersized populations, inverted date ranges, fixed-size MM, etc.
argument-hint: <project>
allowed-tools:
  - mcp__strategyquant__cfx_lint
  - mcp__strategyquant__cfx_inspect
  - mcp__strategyquant__cfx_archetype
  - mcp__strategyquant__cfx_validate
---

The user wants to lint `$ARGUMENTS`.

Steps:

1. `cfx_validate` first — make sure the .cfx is structurally sound. Abort if not.
2. `cfx_archetype` to identify whether this is a builder/optimizer/etc. If not build-like, tell the user `cfx_lint` is most useful on Builder/Optimizer projects.
3. `cfx_lint project=<name>` — get findings + analysis.
4. Group findings by severity, ordering critical → high → medium → low → info.
5. For each finding, surface the `suggestion` field — that's the concrete next call.
6. If `has_critical=True`, lead with a one-line warning the user should resolve before starting the project.

If everything's clean (no findings), say so — but also remind the user that lint is heuristic, not a guarantee of success.
