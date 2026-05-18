"""Engine watchdog — detect a hung sqcli process.

When a Builder spends a long time in JVM-native code (e.g. data prep, MEC
sparkline serialization), it can go quiet on the log for many minutes
without being stuck. But sometimes the JVM is genuinely deadlocked and
needs a kill. This module exposes a heuristic check.

Heuristic signals:

- HTTP API responsive? Engine is at least somewhat alive.
- Log file mtime recent? Engine is producing output.
- Process alive (engine.is_running)? Lower-level liveness.

The check returns a state of: ``responsive`` (fully OK), ``quiet`` (process
alive but no recent log/HTTP), ``unresponsive`` (HTTP timeout), or ``dead``
(process gone).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload


class EngineWatchdogArgs(BaseModel):
    quiet_threshold_seconds: int = Field(
        300,
        ge=10,
        le=3600,
        description=(
            "Log file mtime older than this signals 'quiet'. Default 5 min."
        ),
    )
    http_probe_timeout: float = Field(
        3.0,
        ge=0.5,
        le=30.0,
        description="Per-call HTTP probe timeout in seconds.",
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Watchdog probe for the sqcli engine. Combines: process liveness, "
            "HTTP responsiveness, log file mtime. Returns one coarse state — "
            "'responsive', 'quiet', 'unresponsive', or 'dead' — plus the raw "
            "signal data. Use during long Builder runs to detect a hung JVM."
        )
    )
    async def engine_watchdog(args: EngineWatchdogArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            now = datetime.now(tz=timezone.utc)
            signals: dict[str, Any] = {
                "process_alive": eng.is_running,
                "attached": eng.attached,
                "pid": eng.pid,
            }

            if not eng.is_running:
                return {
                    "ok": True,
                    "state": "dead",
                    "now": now.isoformat(),
                    "signals": signals,
                    "summary": "engine process is not running",
                }

            # HTTP probe
            http_ok = False
            try:
                http_ok = await eng.health()
            except (EngineError, OSError):
                http_ok = False
            signals["http_responsive"] = http_ok

            # Log file mtime
            log_path = eng.config.log_path
            log_mtime: str | None = None
            log_age_seconds: float | None = None
            if log_path and log_path.is_file():
                try:
                    ts = log_path.stat().st_mtime
                    log_mtime = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    log_age_seconds = (now.timestamp() - ts)
                except OSError:
                    pass
            signals["log_path"] = str(log_path) if log_path else None
            signals["log_mtime"] = log_mtime
            signals["log_age_seconds"] = (
                round(log_age_seconds, 2) if log_age_seconds is not None else None
            )

            recent_lines = eng.recent_log
            signals["recent_log_lines"] = len(recent_lines)
            signals["last_log_line"] = recent_lines[-1] if recent_lines else None

            # Classify
            quiet = (
                log_age_seconds is not None
                and log_age_seconds > args.quiet_threshold_seconds
            )
            if not http_ok and quiet:
                state = "unresponsive"
            elif quiet:
                state = "quiet"
            elif not http_ok:
                state = "unresponsive"
            else:
                state = "responsive"

            return {
                "ok": True,
                "state": state,
                "now": now.isoformat(),
                "quiet_threshold_seconds": args.quiet_threshold_seconds,
                "signals": signals,
                "summary": {
                    "responsive": "engine OK — HTTP and log both active",
                    "quiet": (
                        "process alive + HTTP OK but log hasn't written for "
                        f">{args.quiet_threshold_seconds}s — JVM may be in native code"
                    ),
                    "unresponsive": (
                        "process alive but HTTP not answering — engine may be "
                        "blocked or restarting"
                    ),
                    "dead": "engine process is gone",
                }[state],
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)


__all__ = ["EngineWatchdogArgs", "register"]
