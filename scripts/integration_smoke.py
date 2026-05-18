#!/usr/bin/env python3
"""End-to-end smoke test of the sq-mcp stack against a live SQ X install.

Exercises every tool category once and prints a compact report. Use this
after any change to the engine or tools to verify the whole pipeline still
works against the local SQ X.

Usage: source .venv/bin/activate && python scripts/integration_smoke.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

from sq_mcp.config import detect_config
from sq_mcp.engine import EngineClient, EngineError
from sq_mcp.parsers import analyze_mq5, parse_cfx, parse_sqx

logging.basicConfig(
    stream=sys.stderr,
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _h(title: str) -> None:
    print(f"\n=== {title} " + "=" * (60 - len(title)))


def _kv(label: str, value: object) -> None:
    print(f"  {label:<28}: {value}")


async def main() -> int:
    cfg = detect_config()
    _h("environment")
    _kv("SQX_HOME", cfg.sqx_home)
    _kv("sqcli", cfg.sqcli)
    _kv("install valid?", cfg.is_valid)
    _kv("HTTP URL", cfg.http_url)
    _kv("data dir", cfg.data_dir)
    _kv("projects dir", cfg.projects_dir)
    if not cfg.is_valid:
        print("\nABORT: SQ X not installed where expected.")
        return 1

    # --- offline parsers (no engine needed) ----------------------------------
    _h("offline analysis: sample .mq5")
    # Override via SMOKE_MQ5_PATH env var to point at a real .mq5 to audit.
    import os
    sample_path = os.environ.get("SMOKE_MQ5_PATH")
    poly = Path(sample_path) if sample_path else Path.home() / "sample.mq5"
    if poly.exists():
        s = analyze_mq5(poly)
        _kv("strategy", s.name)
        _kv("symbol/TF", f"{s.backtest_symbol} / {s.backtest_timeframe}")
        _kv("magic", s.magic_number)
        _kv("SL / PT", f"{s.has_stop_loss} / {s.has_profit_target}")
        _kv("ExitAfterBars", s.exit_after_bars)
        _kv("indicators count", len(s.indicators))
        crit = [f for f in s.findings if f.severity == "critical"]
        high = [f for f in s.findings if f.severity == "high"]
        _kv("critical findings", len(crit))
        _kv("high findings", len(high))
        for f in crit + high:
            print(f"    [{f.severity:>8}] {f.code}: {f.title}")
    else:
        print(f"  (skip — {poly} not found)")

    _h("offline analysis: existing .sqx in workspace")
    sqx_candidates = list((cfg.projects_dir / "Retester" / "databanks" / "Results").glob("*.sqx"))
    if sqx_candidates:
        info = parse_sqx(sqx_candidates[0])
        _kv("file", sqx_candidates[0].name)
        _kv("version", info.version)
        _kv("results count", len(info.results))
        for r in info.results[:3]:
            _kv(
                f"  result[{r.name[:20]}]",
                f"IS={r.stats.fitness_is}, OOS={r.stats.fitness_oos}",
            )
    else:
        print("  (no .sqx files in workspace)")

    _h("offline analysis: project.cfx")
    for proj in ("Retester", "Builder", "Optimizer"):
        cfx = cfg.projects_dir / proj / "project.cfx"
        if cfx.exists():
            try:
                cfo = parse_cfx(cfx)
                _kv(
                    proj,
                    f"version={cfo.sqx_version} tasks={len(cfo.tasks)} databanks={len(cfo.databanks)}",
                )
            except Exception as exc:  # noqa: BLE001
                _kv(proj, f"PARSE ERROR: {exc!r}")

    # --- engine-backed tools -------------------------------------------------
    _h("engine: starting sqcli (this takes ~5–10s)")
    engine = EngineClient()
    t0 = time.time()
    try:
        await engine.start()
    except EngineError as exc:
        print(f"  ENGINE START FAILED: {exc}")
        return 2
    _kv("startup time", f"{time.time() - t0:.2f}s")

    try:
        for label, cmd in (
            ("project_list", "-project action=list"),
            ("symbol_list", "-symbol action=list"),
            ("instrument_list", "-instrument action=list"),
        ):
            _h(f"engine call: {label}")
            try:
                out = await engine.call(cmd, timeout=30.0)
            except EngineError as exc:
                _kv("ERROR", str(exc))
                continue
            lines = [ln for ln in out.splitlines() if ln.strip()]
            _kv("response lines", len(lines))
            for line in lines[:8]:
                print(f"    {line}")
            if len(lines) > 8:
                print(f"    … +{len(lines) - 8} more")

        _h("engine call: -databank action=count project=Retester name=Results")
        try:
            out = await engine.call(
                "-databank action=count project=Retester name=Results", timeout=30.0
            )
            for line in (line for line in out.splitlines() if line.strip()):
                print(f"    {line}")
        except EngineError as exc:
            _kv("ERROR", str(exc))

        _h("engine call: -data action=timezones (first 10)")
        try:
            out = await engine.call("-data action=timezones", timeout=30.0)
            tzs = [ln for ln in out.splitlines() if "/" in ln][:10]
            for tz in tzs:
                print(f"    {tz}")
        except EngineError as exc:
            _kv("ERROR", str(exc))

        _h("engine: data_list_local equivalent")
        history = cfg.history_dir
        if history.exists():
            for sym_dir in sorted(history.iterdir()):
                if not sym_dir.is_dir():
                    continue
                tfs = sorted(
                    f.stem.replace(f"{sym_dir.name}_", "") for f in sym_dir.glob("*.dat")
                )
                _kv(sym_dir.name, ",".join(tfs))

        _h("engine: noise filter check (raw vs filtered)")
        out = await engine.call("-h", timeout=10.0)
        bad = sum(1 for ln in out.splitlines() if "DEBUG oshi" in ln)
        _kv("DEBUG oshi lines after filter", bad)
        _kv("clean output line count", len(out.splitlines()))

    finally:
        _h("engine: stopping")
        await engine.stop()
        _kv("stopped", True)

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
