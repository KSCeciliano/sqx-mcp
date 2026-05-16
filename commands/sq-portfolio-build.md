---
description: Assemble a portfolio from a set of candidate .sqx strategies — analyze correlation, fitness, and select the diversified subset.
argument-hint: <strategies-folder>
allowed-tools:
  - mcp__strategyquant__sqx_inspect
  - mcp__strategyquant__databank_load
  - mcp__strategyquant__project_start
  - mcp__strategyquant__databank_export
  - Bash
  - Glob
---

The user wants to build a portfolio from the .sqx files in `$ARGUMENTS`.

Steps:

1. Use `Glob` (or `Bash` `find`) to enumerate `*.sqx` under the folder.
2. For each file, call `sqx_inspect` and collect: name, IS fitness, OOS fitness, OOS/IS ratio, has_orders_bin.
3. Filter:
   - drop strategies with OOS/IS ratio < 0.5 (overfit)
   - drop strategies with IS fitness < 0.3 (low edge)
4. Group remaining by symbol/TF (extracted from the strategy name where possible).
5. Load the survivors into the `PortfolioComposer` project's input databank (`databank_load`).
6. Start the PortfolioComposer project (`project_start`).
7. When done, `databank_export` and summarize the chosen portfolio: number of strategies, expected aggregate fitness, correlation summary.

If `PortfolioComposer` doesn't exist as a project, fall back to: present the filtered list to the user, with a recommendation of how to combine them manually in SQ X.
