---
description: Generate a one-shot Markdown deck for the top-N strategies of a databank — headline numbers, audit verdicts, equity geometry, findings.
argument-hint: <project> [databank=Results] [N=10] [output_path]
allowed-tools:
  - mcp__strategyquant__portfolio_deck_markdown
  - mcp__strategyquant__portfolio_summary
  - mcp__strategyquant__portfolio_concentration
---

The user wants a portfolio deck for `$ARGUMENTS`.

Steps:

1. Parse args: `project [databank] [N] [output_path]`. Defaults: databank=Results, N=10.
2. Call `portfolio_summary` first to set context (how big is the databank, % profitable, etc.).
3. Call `portfolio_concentration` to surface diversity issues *before* the deck.
4. Call `portfolio_deck_markdown` with `n=N`, rank_mode='defensive', min_trades=30, and `output_path` if provided.
5. Present the deck inline if no output_path; otherwise tell the user where it was written and show the first ~30 lines as a preview.

Tip for the user: pair the deck with `portfolio_export_csv` if they want raw data for spreadsheet analysis.
