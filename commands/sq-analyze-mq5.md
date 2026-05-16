---
description: Run a deep risk audit on one or more SQ-generated .mq5 Expert Advisor files.
argument-hint: <file.mq5> [more .mq5 files...]
allowed-tools:
  - mcp__strategyquant__analyze_mq5_file
  - mcp__strategyquant__compare_mq5_files
  - Read
---

You are running a focused risk audit on the SQ-generated MQL5 file(s) the user passed: `$ARGUMENTS`.

Steps:

1. If the user passed exactly one file, call `analyze_mq5_file` with that path.
2. If the user passed two or more files, call `compare_mq5_files` with all of them.
3. Present results in this exact order:
   - **Header**: name, SQ build, symbol/TF, backtest period.
   - **Trading rules**: long entry, short entry, exits, indicators used (concise — no MQL5 verbatim).
   - **MM / risk**: magic number, money management settings, time range.
   - **🚨 Critical findings** first (severity = critical), with the suggestion verbatim.
   - **High findings** next.
   - **Medium / low / info** as a compact bullet list.
4. If multiple files were analyzed: highlight magic-number collisions and same-backtest-window warnings prominently — these are the kind of mistake that destroys live accounts.
5. End with a one-paragraph "What to fix before live" summary.

Keep the output under 400 words unless findings require more.
