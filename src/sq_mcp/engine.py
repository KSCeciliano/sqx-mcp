"""StrategyQuant X engine lifecycle.

Boots a single long-lived `sqcli -gui` process so the HTTP API stays up on
:5050. All tool calls then go through `EngineClient.call(cmd, **args)` which
hits `GET /call?cmd=...`, paying no JVM-startup cost per call.

Hardening features:
  * stdout/stderr filtered to drop the noisy oshi/Java DEBUG spam (~200 lines/start)
  * `_call_lock` serializes HTTP calls so we don't overlap two long-running
    sqcli operations on the same engine
  * configurable per-call timeout, with bounded retries on transient
    httpx.TransportError
  * graceful shutdown: try `-exit` first, then terminate(), then kill()
    (POSIX: SIGTERM → SIGKILL; Windows: both call TerminateProcess)
  * the stdout-consumer task is tracked so a silent crash surfaces
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import subprocess
import sys
import urllib.parse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from sq_mcp.config import SqConfig, detect_config

# On Windows, asyncio.create_subprocess_exec spawns the JVM with a visible
# console window unless CREATE_NO_WINDOW is set. Flag is undefined on POSIX.
_SUBPROCESS_KW: dict = {}
if sys.platform == "win32":
    _SUBPROCESS_KW["creationflags"] = subprocess.CREATE_NO_WINDOW

log = logging.getLogger("sq_mcp.engine")


# ---- log noise filtering -----------------------------------------------------

_NOISE_PATTERNS = (
    re.compile(r"\bDEBUG\s+oshi\."),
    re.compile(r"\bDEBUG\s+o\.s\.os\."),
    re.compile(r"\bReading file /(?:proc|sys|etc)/"),
    re.compile(r"\bWARNING:\s+Invalid cookie header"),
    re.compile(r"^\s*$"),
)
# Only this single line means "the CLI dispatcher is online and accepts commands".
# Earlier sqcli log lines like "Server started on port" or "Projects loaded in"
# fire while the dispatcher is still warming up and will return
# "Error: CLI not ready." if you call them too soon.
_READY_PATTERNS = (
    re.compile(r"HTTP API started, you can access it on"),
)
# When sqcli replies with this exact text, the dispatcher hasn't finished init yet.
_NOT_READY_RESPONSE = "CLI not ready"


def _is_noise(line: str) -> bool:
    return any(p.search(line) for p in _NOISE_PATTERNS)


def _strip_noise(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _is_noise(line))


# ---- defaults ---------------------------------------------------------------

# Per-call HTTP timeout. Some sqcli operations (project_start, walk-forward)
# return promptly because they kick off background work, but databank_load can
# block until done. Caller can override via call(..., timeout=...).
DEFAULT_CALL_TIMEOUT = 120.0
DEFAULT_START_TIMEOUT = 90.0
DEFAULT_STOP_TIMEOUT = 15.0
MAX_CALL_RETRIES = 2


# ---- state ------------------------------------------------------------------


@dataclass
class _EngineState:
    process: asyncio.subprocess.Process | None = None
    ready: asyncio.Event | None = None
    http: httpx.AsyncClient | None = None
    consumer_task: asyncio.Task[None] | None = None
    fatal_error: str | None = None
    log_tail: list[str] = field(default_factory=list)
    attached: bool = False
    attached_log_path: Path | None = None


class EngineError(RuntimeError):
    """Raised when the engine subprocess fails or never reaches ready state."""


class EngineClient:
    """Async client owning the sqcli subprocess and the HTTP client.

    Thread-/task-safe via:
      * `_lifecycle_lock` — guards start() / stop() to avoid concurrent boots.
      * `_call_lock` — serializes outbound HTTP calls (sqcli's HTTP server is
        not designed for parallel command dispatch).
    """

    def __init__(self, config: SqConfig | None = None) -> None:
        self.config = config or detect_config()
        self._state = _EngineState()
        self._lifecycle_lock = asyncio.Lock()
        self._call_lock = asyncio.Lock()
        # Bounded log tail kept for diagnostics
        self._log_tail_max = 200

    # ---- public lifecycle ---------------------------------------------------

    async def attach_or_start(
        self,
        *,
        timeout: float = DEFAULT_START_TIMEOUT,
        prefer_attach: bool = True,
    ) -> str:
        """Attach to an externally-running sqcli on :5050, or spawn one.

        Returns "attached" or "spawned" so callers know which path was taken.
        """
        if prefer_attach and await self._try_attach():
            return "attached"
        await self.start(timeout=timeout)
        return "spawned"

    async def _try_attach(self) -> bool:
        """Probe HTTP API — if responsive, switch to attached mode (no subprocess)."""
        async with self._lifecycle_lock:
            if self.is_running:
                return True
            http = httpx.AsyncClient(
                base_url=self.config.http_url,
                timeout=httpx.Timeout(DEFAULT_CALL_TIMEOUT, connect=2.0),
            )
            try:
                url = "/call?cmd=" + _encode_cmd("-h")
                r = await http.get(url, timeout=5.0)
                if r.status_code != 200 or _NOT_READY_RESPONSE in r.text:
                    await http.aclose()
                    return False
            except Exception:  # noqa: BLE001
                try:
                    await http.aclose()
                except Exception:  # noqa: BLE001
                    pass
                return False
            ready = asyncio.Event()
            ready.set()
            self._state = _EngineState(
                ready=ready,
                http=http,
                attached=True,
                attached_log_path=self.config.log_path,
            )
            if self.config.log_path and self.config.log_path.exists():
                self._state.consumer_task = asyncio.create_task(
                    self._consume_tailed_file(self.config.log_path),
                    name="sq-mcp-engine-tail",
                )
            log.info("attached to external sqcli at %s", self.config.http_url)
            return True

    async def start(self, *, timeout: float = DEFAULT_START_TIMEOUT) -> None:
        """Spawn `sqcli -gui` if not already running. Wait until HTTP is reachable."""
        async with self._lifecycle_lock:
            if self.is_running:
                return
            if not self.config.is_valid:
                raise EngineError(
                    f"sqcli not found at {self.config.sqcli}. "
                    "Set SQX_HOME env var or install StrategyQuant X."
                )
            log.info("starting sqcli engine: %s -gui (cwd=%s)", self.config.sqcli, self.config.sqx_home)
            self._state = _EngineState(ready=asyncio.Event())
            try:
                self._state.process = await asyncio.create_subprocess_exec(
                    str(self.config.sqcli),
                    "-gui",
                    cwd=str(self.config.sqx_home),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    **_SUBPROCESS_KW,
                )
            except (OSError, FileNotFoundError) as exc:
                raise EngineError(f"failed to spawn sqcli: {exc}") from exc

            self._state.consumer_task = asyncio.create_task(
                self._consume_output(), name="sq-mcp-engine-stdout"
            )
            self._state.http = httpx.AsyncClient(
                base_url=self.config.http_url,
                timeout=httpx.Timeout(DEFAULT_CALL_TIMEOUT, connect=5.0),
                # No retries at the transport layer — we do bounded retries in call()
            )

        # Wait for either the ready signal OR the process to die unexpectedly.
        try:
            await asyncio.wait_for(self._wait_for_ready(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            await self._abort_failed_start("HTTP API never signalled ready")
            raise EngineError(
                f"sqcli failed to signal HTTP API ready within {timeout}s"
            ) from exc

        # Confirm the endpoint actually answers before returning.
        try:
            await self._probe_http(timeout=10.0)
        except Exception as exc:
            await self._abort_failed_start(str(exc))
            raise EngineError(f"engine started but HTTP probe failed: {exc}") from exc

    async def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT) -> None:
        """Gracefully shut down the engine subprocess and HTTP client."""
        async with self._lifecycle_lock:
            await self._teardown(timeout=timeout)

    @property
    def is_running(self) -> bool:
        if self._state.attached:
            return self._state.http is not None
        proc = self._state.process
        return proc is not None and proc.returncode is None

    @property
    def attached(self) -> bool:
        return self._state.attached

    @property
    def pid(self) -> int | None:
        proc = self._state.process
        return proc.pid if proc else None

    async def health(self) -> bool:
        if not self.is_running or self._state.http is None:
            return False
        try:
            r = await self._state.http.get("/call", params={"cmd": "-h"}, timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    @property
    def recent_log(self) -> list[str]:
        return list(self._state.log_tail)

    # ---- HTTP layer ---------------------------------------------------------

    async def call(
        self,
        cmd: str,
        *,
        timeout: float | None = None,
        retries: int = MAX_CALL_RETRIES,
        **params: str | int | float | bool,
    ) -> str:
        """Issue a sqcli-style command via HTTP API.

        Args:
            cmd: full sqcli command, e.g. "-project action=list".
            timeout: per-call HTTP timeout in seconds (None = client default).
            retries: bounded retries on transient transport errors.
            **params: extra `key=value` pairs appended to the command.
        """
        if not isinstance(cmd, str) or not cmd.strip():
            raise EngineError("call(cmd): cmd must be a non-empty string")

        await self._ensure_started()
        assert self._state.http is not None  # type checker

        full = cmd if not params else f"{cmd} " + " ".join(
            f"{k}={_quote_arg(str(v))}" for k, v in params.items()
        )
        log.debug("sq.call %s", full)

        http_timeout = httpx.Timeout(timeout, connect=5.0) if timeout else None
        # sqcli's HTTP server reads the query string literally — see _probe_http.
        url = "/call?cmd=" + _encode_cmd(full)
        last_exc: Exception | None = None
        async with self._call_lock:
            for attempt in range(retries + 1):
                try:
                    r = await self._state.http.get(url, timeout=http_timeout)
                    r.raise_for_status()
                    text = _strip_noise(r.text)
                    if _NOT_READY_RESPONSE in text:
                        # Should not happen post-start, but treat as transient
                        last_exc = EngineError("sqcli replied 'CLI not ready'")
                    else:
                        return text
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code < 500:
                        raise EngineError(
                            f"sqcli rejected '{cmd}': HTTP {exc.response.status_code} {exc.response.text[:200]}"
                        ) from exc
                    last_exc = exc
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise EngineError(
            f"sqcli call '{cmd}' failed after {retries + 1} attempts: {last_exc}"
        )

    # ---- internals ----------------------------------------------------------

    async def _ensure_started(self) -> None:
        if self._state.fatal_error:
            raise EngineError(f"engine in fatal state: {self._state.fatal_error}")
        if not self.is_running:
            await self.start()

    async def _wait_for_ready(self) -> None:
        """Wait for either the ready event OR the process to die early."""
        proc = self._state.process
        ready = self._state.ready
        assert proc is not None and ready is not None

        ready_task = asyncio.create_task(ready.wait())
        died_task = asyncio.create_task(proc.wait())
        done, pending = await asyncio.wait(
            {ready_task, died_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        if ready_task in done and not ready.is_set():
            # ready_task completed but somehow ready not set — shouldn't happen
            raise EngineError("internal: ready event reported done but not set")
        if died_task in done:
            tail = "\n".join(self._state.log_tail[-30:])
            raise EngineError(
                f"sqcli exited with code {proc.returncode} during startup. "
                f"Last log lines:\n{tail}"
            )

    async def _consume_tailed_file(self, path: Path) -> None:
        """Tail an external sqcli log file (attached mode). Polls every 0.5s."""
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)  # start at EOF — only see new lines
                while True:
                    line = f.readline()
                    if not line:
                        await asyncio.sleep(0.5)
                        continue
                    line = line.rstrip()
                    if not line or _is_noise(line):
                        continue
                    self._state.log_tail.append(line)
                    if len(self._state.log_tail) > self._log_tail_max:
                        del self._state.log_tail[
                            : len(self._state.log_tail) - self._log_tail_max
                        ]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._state.fatal_error = f"log tail crashed: {exc!r}"
            log.exception("attached log tail crashed")

    async def _consume_output(self) -> None:
        proc = self._state.process
        if not proc or not proc.stdout:
            return
        try:
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                if not line:
                    continue
                if _is_noise(line):
                    continue
                # store in tail (bounded)
                self._state.log_tail.append(line)
                if len(self._state.log_tail) > self._log_tail_max:
                    del self._state.log_tail[: len(self._state.log_tail) - self._log_tail_max]
                if self._state.ready and not self._state.ready.is_set():
                    if any(p.search(line) for p in _READY_PATTERNS):
                        self._state.ready.set()
                # Forward to logger (stderr-bound)
                log.info("[sqcli] %s", line)
        except Exception as exc:  # noqa: BLE001 — protect server from consumer crash
            self._state.fatal_error = f"output consumer crashed: {exc!r}"
            log.exception("sqcli stdout consumer crashed")

    async def _probe_http(self, *, timeout: float) -> None:
        assert self._state.http is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last_err: Exception | None = None
        while loop.time() < deadline:
            try:
                # Use a real CLI command. sqcli's HTTP server reads the query
                # string LITERALLY (it does not URL-decode `+` to space or
                # decode `%3D`), so we hand-roll the URL.
                url = "/call?cmd=" + _encode_cmd("-project action=list")
                r = await self._state.http.get(url, timeout=3.0)
                if r.status_code == 200 and _NOT_READY_RESPONSE not in r.text \
                   and "Unrecognized command" not in r.text:
                    return
                last_err = EngineError(f"HTTP {r.status_code}: {r.text[:200]}")
            except Exception as exc:  # noqa: BLE001
                last_err = exc
            await asyncio.sleep(0.5)
        raise EngineError(
            f"HTTP API at {self.config.http_url} never reported ready: {last_err}"
        )

    async def _teardown(self, *, timeout: float) -> None:
        attached = self._state.attached
        # Close HTTP client first so in-flight calls fail fast.
        if self._state.http:
            try:
                await self._state.http.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._state.http = None

        if attached:
            # Leave the external process alone. Just cancel any tail task.
            if self._state.consumer_task and not self._state.consumer_task.done():
                self._state.consumer_task.cancel()
                try:
                    await self._state.consumer_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._state = _EngineState()
            return

        proc = self._state.process
        if proc and proc.returncode is None:
            log.info("stopping sqcli engine (pid=%s)", proc.pid)
            # Try -exit via stdin (sqcli replies to a newline by exiting).
            try:
                if proc.stdin:
                    proc.stdin.write(b"-exit\n")
                    await proc.stdin.drain()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout / 2)
            except asyncio.TimeoutError:
                # On Windows terminate() and kill() both call TerminateProcess —
                # there is no SIGTERM/SIGKILL distinction, so the second escalation
                # is redundant but harmless.
                log.warning("sqcli did not respond to -exit, terminating")
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=timeout / 2)
                except asyncio.TimeoutError:
                    log.warning("sqcli ignored terminate, killing")
                    proc.kill()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        log.error("sqcli still alive after kill — leaking process %s", proc.pid)

        # Cancel consumer task last — its source stream is now closed.
        if self._state.consumer_task and not self._state.consumer_task.done():
            self._state.consumer_task.cancel()
            try:
                await self._state.consumer_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._state = _EngineState()

    async def _abort_failed_start(self, reason: str) -> None:
        log.error("aborting failed start: %s", reason)
        async with self._lifecycle_lock:
            await self._teardown(timeout=5.0)


# ---- helpers ----------------------------------------------------------------


def _quote_arg(value: str) -> str:
    """Quote a value for inclusion in a sqcli `key=value` argument list.

    sqcli splits its argument string on whitespace; any value containing a
    space or quote needs shell-style quoting.
    """
    if any(c in value for c in (' ', '\t', '"', "'", '&', '|', ';', '\n')):
        return shlex.quote(value)
    return value


def _encode_cmd(cmd: str) -> str:
    """URL-encode a sqcli command for use as the `cmd=` query value.

    sqcli's embedded HTTP server does NOT decode `+` to space or `%3D` to `=`
    — it reads the raw query value. So we encode only the characters that
    actually have to be encoded for HTTP transit (space → %20, `?` → %3F,
    `&` → %26, `#` → %23) and leave everything else alone (notably `=` and
    `-`). RFC 3986 allows this — the `?` and `&` in a query value MUST be
    percent-encoded; `=` may be left literal.

    Windows paths: convert `\\` to `/` before encoding. SQ X is Java-based
    and accepts forward-slash paths on every OS; leaving backslashes literal
    causes httpx to %5C-encode them and sqcli does not URL-decode.
    """
    return urllib.parse.quote(cmd.replace("\\", "/"), safe="=-/.,:_+()[]{}*\"'")


@asynccontextmanager
async def engine_lifespan(config: SqConfig | None = None) -> AsyncIterator[EngineClient]:
    """FastMCP lifespan helper. Yields a ready-to-use EngineClient."""
    engine = EngineClient(config)
    await engine.start()
    try:
        yield engine
    finally:
        await engine.stop()


# Module-level no-op to keep `sys` import live for diagnostic scripts; some
# tests probe sys.modules to verify the engine module loaded.
_ = sys.version_info
