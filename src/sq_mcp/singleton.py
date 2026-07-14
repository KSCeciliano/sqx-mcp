"""Cross-process singleton ownership for the managed SQX backend."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class SingletonLock:
    def __init__(self, path: Path, *, pid: int | None = None, pid_alive: Callable[[int], bool] | None = None) -> None:
        self.path = path
        self.pid = pid or os.getpid()
        self._pid_alive = pid_alive or self._default_pid_alive
        self._owned = False

    @staticmethod
    def _default_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": self.pid, "hostname": socket.gethostname(), "created_at": time.time()}
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                self._owned = True
                return
            except FileExistsError:
                try:
                    current = json.loads(self.path.read_text(encoding="utf-8"))
                    owner = int(current.get("pid", 0))
                except Exception:
                    owner = 0
                if owner and self._pid_alive(owner):
                    raise RuntimeError(f"singleton already owned by pid {owner}") from None
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
        raise RuntimeError("unable to acquire singleton lock")

    def release(self) -> None:
        if not self._owned:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if int(current.get("pid", 0)) == self.pid:
                self.path.unlink(missing_ok=True)
        finally:
            self._owned = False

    def __enter__(self) -> SingletonLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class ManagedEnginePool:
    """One EngineClient shared by every Streamable HTTP session."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._engine: Any | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> Any:
        async with self._lock:
            if self._engine is None:
                engine = self._factory()
                await engine.attach_or_start()
                self._engine = engine
            return self._engine

    async def shutdown(self) -> None:
        async with self._lock:
            if self._engine is None:
                return
            engine, self._engine = self._engine, None
            await engine.stop()


__all__ = ["ManagedEnginePool", "SingletonLock"]
