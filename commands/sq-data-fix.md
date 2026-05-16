---
description: Diagnose and (optionally) repair data issues for a symbol — gaps, missing timeframes, wrong timezone.
argument-hint: <symbol> [timeframe]
allowed-tools:
  - mcp__strategyquant__data_list_local
  - mcp__strategyquant__data_update
  - mcp__strategyquant__data_import
  - mcp__strategyquant__data_timezones
  - mcp__strategyquant__instrument_list
  - mcp__strategyquant__instrument_add
---

The user wants to fix data issues for: `$ARGUMENTS`.

Steps:

1. Call `data_list_local` and find the symbol entry. Report which timeframes are present.
2. If the requested timeframe is missing, propose an `import` plan and ASK before running.
3. If present, call `data_update` for that symbol and check for errors.
4. If the symbol isn't an instrument yet, call `instrument_list` to confirm — if missing, walk the user through `instrument_add` (especially for crypto: `data_type=crypto`, correct `tick_size`).
5. After repair, call `data_list_local` again and confirm the timeframes appear.

Always tell the user *what* you're about to change before running mutating tools.
