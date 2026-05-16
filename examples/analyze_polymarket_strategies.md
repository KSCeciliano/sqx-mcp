# Example: auditing the "polymarketfinalistas" strategies

A realistic walk-through of using `/sq:analyze-mq5` on three SQ-generated EAs that share the same backtest project.

## The setup

You have three SQ X exports in `~/Downloads/`:

```
polymarketfinalistasStrategy 1.2.17436.mq5
polymarketfinalistasStrategy 2.1.19136.mq5
polymarketfinalistasStrategy 2.2.23436.mq5
```

All three were generated for **BTCUSDT M5**, backtest window 2024-05 → 2026-05.

## Run the audit

```
/sq:analyze-mq5 ~/Downloads/polymarketfinalistasStrategy\ 1.2.17436.mq5 \
                ~/Downloads/polymarketfinalistasStrategy\ 2.1.19136.mq5 \
                ~/Downloads/polymarketfinalistasStrategy\ 2.2.23436.mq5
```

Because more than one file was passed, Claude calls `compare_mq5_files` instead of `analyze_mq5_file`.

## What you get back

A structured response with:

- **Summary table** — name, symbol/TF, magic, exit_after_bars, has_sl, has_pt, indicator count, finding counts.
- **Warnings**:
  - `magic_number_collisions: { 11111: [path1, path2, path3] }` — all three share the SQ default magic. Running them in parallel on the same MT5 will cross-close trades.
  - `all_same_backtest_window: True` — single backtest period, single instrument, multiple winning strategies = high curve-fit risk.
- **Per-strategy findings**, with `NO_STOP_LOSS` and `MM_FIXED_AMOUNT_NO_SL` flagged as `critical` for every file.

## What to do

The plugin's recommendations will say roughly:

1. Reassign unique `MagicNumber` per EA (e.g. 11111, 11112, 11113) before running together.
2. Add a hard StopLoss in SQ X — without one, the declared `mmRiskedMoney = 100$` is fiction because `sqMMFixedAmount` derives position size from the SL.
3. Re-run an out-of-sample test on a *different* window before going live.
4. If you intend to keep all three running, audit correlation — they share too many design assumptions.

## Why this matters

Each of those three findings has cost real traders real money. The MCP catches them in seconds, before the strategy ever touches a live account.
