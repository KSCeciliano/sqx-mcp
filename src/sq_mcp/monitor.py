"""Active build / project monitor.

While a Builder / Optimizer / WalkForward project runs, we periodically:
  - poll databank_count (cheap, no full re-read)
  - parse the most-recent .sqx files dropped into the project's databank folder
  - compute trends: strategies-per-interval, median fitness IS/OOS, OOS/IS ratio
  - evaluate user-configurable RULES that map to ACTIONS:
      * alert_only       — record the alert
      * pause_project    — call -project action=pause
      * stop_project     — call -project action=stop (aborts a wasteful run)

The point: when SQ X is spending hours generating thousands of curve-fit
strategies, you want to *catch* that early and stop the run rather than
discover it after the fact.

Robustness notes:
  * The polling loop is a single while-True with try/except inside the body.
    A crash on one cycle records an alert and continues — never leaks a task,
    never re-spawns itself.
  * Cancellation is propagated cleanly via asyncio.CancelledError.
  * Disk I/O on .sqx parsing is bounded to the N most recent files (default 10).
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass, field
from typing import Literal

from sq_mcp.engine import EngineClient, EngineError
from sq_mcp.parsers import parse_sqx

log = logging.getLogger("sq_mcp.monitor")

Action = Literal["alert_only", "pause_project", "stop_project"]
Severity = Literal["info", "warning", "critical"]

# Bound the disk-I/O cost per cycle.
_MAX_RECENT_SQX = 10
# Tolerated consecutive failures before the monitor self-stops.
_MAX_CONSECUTIVE_FAILURES = 5


@dataclass
class MonitorRule:
    code: str
    description: str
    action: Action = "alert_only"
    threshold: float | None = None
    window_checks: int = 3


@dataclass
class MonitorAlert:
    timestamp: float
    project: str
    rule_code: str
    severity: Severity
    message: str
    action_taken: Action
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            **self.__dict__,
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.timestamp)),
        }


@dataclass
class MonitorSnapshot:
    timestamp: float
    strategy_count: int
    new_since_last: int
    median_fitness: float | None
    p75_fitness: float | None
    median_fitness_oos: float | None
    diversity_score: float | None
    project_status_lines: list[str] = field(default_factory=list)


@dataclass
class MonitorSession:
    project: str
    databank: str = "Results"
    interval_seconds: float = 60.0
    rules: list[MonitorRule] = field(default_factory=list)
    snapshots: list[MonitorSnapshot] = field(default_factory=list)
    alerts: list[MonitorAlert] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    stopped_at: float | None = None
    task: asyncio.Task | None = None
    consecutive_failures: int = 0
    fired_codes: set[str] = field(default_factory=set)  # to avoid alert spam

    def as_dict(self) -> dict:
        return {
            "project": self.project,
            "databank": self.databank,
            "interval_seconds": self.interval_seconds,
            "rules": [r.__dict__ for r in self.rules],
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "running": self.task is not None and not self.task.done(),
            "snapshot_count": len(self.snapshots),
            "alert_count": len(self.alerts),
            "consecutive_failures": self.consecutive_failures,
            "last_snapshot": self.snapshots[-1].__dict__ if self.snapshots else None,
        }


DEFAULT_RULES: list[MonitorRule] = [
    MonitorRule(
        code="STALLED_GROWTH",
        description="Strategy count hasn't increased over the last N polling windows.",
        action="alert_only",
        threshold=0,
        window_checks=5,
    ),
    MonitorRule(
        code="LOW_MEDIAN_FITNESS",
        description="Median fitness of recent strategies is below threshold.",
        action="alert_only",
        threshold=0.2,
        window_checks=3,
    ),
    MonitorRule(
        code="OOS_DEGRADATION",
        description="Median OOS fitness has been < threshold * IS fitness across recent strategies.",
        action="alert_only",
        threshold=0.5,
        window_checks=3,
    ),
    MonitorRule(
        code="PROJECT_NOT_RUNNING",
        description="Project status no longer reports an active run.",
        action="alert_only",
    ),
]


class MonitorManager:
    """Owns all active MonitorSessions for the lifetime of the MCP server."""

    def __init__(self, engine: EngineClient) -> None:
        self.engine = engine
        self._sessions: dict[str, MonitorSession] = {}
        self._lock = asyncio.Lock()

    # ---- public API ----------------------------------------------------------

    async def start(
        self,
        project: str,
        *,
        databank: str = "Results",
        interval_seconds: float = 60.0,
        rules: list[MonitorRule] | None = None,
        auto_stop_on_critical: bool = False,
    ) -> MonitorSession:
        if interval_seconds < 10.0:
            raise ValueError("interval_seconds must be >= 10")
        if interval_seconds > 3600.0:
            raise ValueError("interval_seconds must be <= 3600")
        async with self._lock:
            existing = self._sessions.get(project)
            if existing and existing.task and not existing.task.done():
                log.info("monitor for %s already running, reusing", project)
                return existing
            sess = MonitorSession(
                project=project,
                databank=databank,
                interval_seconds=interval_seconds,
                rules=[MonitorRule(**r.__dict__) for r in (rules or DEFAULT_RULES)],
            )
            if auto_stop_on_critical:
                for r in sess.rules:
                    if r.code in {"LOW_MEDIAN_FITNESS", "OOS_DEGRADATION", "STALLED_GROWTH"}:
                        r.action = "stop_project"
            sess.task = asyncio.create_task(
                self._run(sess), name=f"sq-monitor-{project}"
            )
            self._sessions[project] = sess
            return sess

    async def stop(self, project: str) -> bool:
        async with self._lock:
            sess = self._sessions.get(project)
            if not sess or not sess.task:
                return False
            sess.task.cancel()
            try:
                await sess.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            sess.stopped_at = time.time()
            return True

    async def stop_all(self) -> None:
        async with self._lock:
            for sess in self._sessions.values():
                if sess.task and not sess.task.done():
                    sess.task.cancel()
            for sess in self._sessions.values():
                if sess.task:
                    try:
                        await sess.task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    sess.stopped_at = sess.stopped_at or time.time()

    def get(self, project: str) -> MonitorSession | None:
        return self._sessions.get(project)

    def list(self) -> list[MonitorSession]:
        return list(self._sessions.values())

    # ---- internals -----------------------------------------------------------

    async def _run(self, sess: MonitorSession) -> None:
        """Polling loop. Single while-True; cycle errors are isolated."""
        log.info("monitor[%s] started, interval=%.1fs", sess.project, sess.interval_seconds)
        try:
            while True:
                try:
                    snap = await self._take_snapshot(sess)
                    sess.snapshots.append(snap)
                    sess.consecutive_failures = 0
                    await self._evaluate_rules(sess, snap)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    sess.consecutive_failures += 1
                    sess.alerts.append(
                        MonitorAlert(
                            timestamp=time.time(),
                            project=sess.project,
                            rule_code="MONITOR_ERROR",
                            severity="warning",
                            message=f"polling failed (#{sess.consecutive_failures}): {exc!r}",
                            action_taken="alert_only",
                            details={"error": repr(exc)},
                        )
                    )
                    log.warning(
                        "monitor[%s] poll failed (%d consecutive): %s",
                        sess.project,
                        sess.consecutive_failures,
                        exc,
                    )
                    if sess.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        sess.alerts.append(
                            MonitorAlert(
                                timestamp=time.time(),
                                project=sess.project,
                                rule_code="MONITOR_GIVING_UP",
                                severity="critical",
                                message=(
                                    f"giving up after {_MAX_CONSECUTIVE_FAILURES} consecutive failures"
                                ),
                                action_taken="alert_only",
                            )
                        )
                        log.error("monitor[%s] giving up", sess.project)
                        return
                await asyncio.sleep(sess.interval_seconds)
        except asyncio.CancelledError:
            log.info("monitor[%s] cancelled", sess.project)
            raise
        finally:
            sess.stopped_at = sess.stopped_at or time.time()

    async def _take_snapshot(self, sess: MonitorSession) -> MonitorSnapshot:
        # 1. count (cheap call). Build 143 doesn't implement `count`, so we
        # fall back to a list-and-count.
        count_text = await self.engine.call(
            f"-databank action=count project={sess.project} name={sess.databank}",
            timeout=30.0,
        )
        if "Not implemented" in count_text:
            list_text = await self.engine.call(
                f"-databank action=list project={sess.project} name={sess.databank}",
                timeout=60.0,
            )
            count = sum(
                1
                for ln in list_text.splitlines()
                if ln.strip() and not ln.startswith(("---", "All tasks", "Bye"))
            )
        else:
            count = _extract_count(count_text) or 0

        # 2. project status
        status_text = await self.engine.call(
            f"-project action=status name={sess.project}",
            timeout=30.0,
        )

        # 3. sample fitness from recent .sqx files (parsed in pure Python — no engine cost)
        fits_is: list[float] = []
        fits_oos: list[float] = []
        databank_dir = (
            self.engine.config.projects_dir / sess.project / "databanks" / sess.databank
        )
        if databank_dir.exists():
            try:
                sqx_paths = sorted(
                    databank_dir.glob("*.sqx"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[:_MAX_RECENT_SQX]
                for sp in sqx_paths:
                    try:
                        info = parse_sqx(sp)
                    except (ValueError, OSError) as exc:
                        log.debug("monitor[%s] could not parse %s: %s", sess.project, sp.name, exc)
                        continue
                    for r in info.results:
                        if r.is_portfolio and r.stats.fitness_is is not None:
                            fits_is.append(r.stats.fitness_is)
                            if r.stats.fitness_oos is not None:
                                fits_oos.append(r.stats.fitness_oos)
                            break
            except OSError as exc:
                log.warning("monitor[%s] databank dir scan failed: %s", sess.project, exc)

        last_count = sess.snapshots[-1].strategy_count if sess.snapshots else 0
        return MonitorSnapshot(
            timestamp=time.time(),
            strategy_count=count,
            new_since_last=max(0, count - last_count),
            median_fitness=_safe_median(fits_is),
            p75_fitness=_safe_percentile(fits_is, 0.75),
            median_fitness_oos=_safe_median(fits_oos),
            diversity_score=None,
            project_status_lines=[
                line.strip()
                for line in status_text.splitlines()
                if line.strip() and not line.startswith("---")
            ][:20],
        )

    async def _evaluate_rules(self, sess: MonitorSession, snap: MonitorSnapshot) -> None:
        for rule in sess.rules:
            alert = self._check_rule(sess, rule, snap)
            if alert:
                # avoid alert spam: only re-fire if it's an action-taking rule
                if rule.action == "alert_only" and rule.code in sess.fired_codes:
                    continue
                sess.fired_codes.add(rule.code)
                sess.alerts.append(alert)
                await self._take_action(sess, rule, alert)

    def _check_rule(
        self, sess: MonitorSession, rule: MonitorRule, snap: MonitorSnapshot
    ) -> MonitorAlert | None:
        recent = sess.snapshots[-rule.window_checks:] if rule.window_checks else [snap]
        if rule.window_checks and len(recent) < rule.window_checks:
            return None

        if rule.code == "STALLED_GROWTH":
            new_total = sum(s.new_since_last for s in recent)
            if new_total <= (rule.threshold or 0):
                return MonitorAlert(
                    timestamp=snap.timestamp,
                    project=sess.project,
                    rule_code=rule.code,
                    severity="warning",
                    message=(
                        f"no new strategies in last {rule.window_checks} checks "
                        f"(~{rule.window_checks * sess.interval_seconds:.0f}s)"
                    ),
                    action_taken=rule.action,
                    details={"new_total": new_total, "window": rule.window_checks},
                )
        elif rule.code == "LOW_MEDIAN_FITNESS":
            medians = [s.median_fitness for s in recent if s.median_fitness is not None]
            if len(medians) >= 1 and statistics.median(medians) < (rule.threshold or 0):
                return MonitorAlert(
                    timestamp=snap.timestamp,
                    project=sess.project,
                    rule_code=rule.code,
                    severity="warning",
                    message=f"median fitness {statistics.median(medians):.3f} < {rule.threshold}",
                    action_taken=rule.action,
                    details={"medians": medians, "threshold": rule.threshold},
                )
        elif rule.code == "OOS_DEGRADATION":
            ratios: list[float] = []
            for s in recent:
                if s.median_fitness and s.median_fitness_oos is not None:
                    if s.median_fitness > 0:
                        ratios.append(s.median_fitness_oos / s.median_fitness)
            if ratios and statistics.median(ratios) < (rule.threshold or 0):
                return MonitorAlert(
                    timestamp=snap.timestamp,
                    project=sess.project,
                    rule_code=rule.code,
                    severity="warning",
                    message=(
                        f"OOS/IS fitness ratio {statistics.median(ratios):.2f} < {rule.threshold} "
                        "— strong overfit signal"
                    ),
                    action_taken=rule.action,
                    details={"ratios": ratios, "threshold": rule.threshold},
                )
        elif rule.code == "PROJECT_NOT_RUNNING":
            joined = " ".join(snap.project_status_lines).lower()
            if any(tok in joined for tok in ("idle", "stopped", "finished")) and not any(
                tok in joined for tok in ("running", "in progress", "active")
            ):
                return MonitorAlert(
                    timestamp=snap.timestamp,
                    project=sess.project,
                    rule_code=rule.code,
                    severity="info",
                    message="project no longer reports an active run",
                    action_taken=rule.action,
                    details={"status_lines": snap.project_status_lines},
                )
        return None

    async def _take_action(
        self, sess: MonitorSession, rule: MonitorRule, alert: MonitorAlert
    ) -> None:
        if rule.action == "stop_project":
            try:
                await self.engine.call(f"-project action=stop name={sess.project}", timeout=30.0)
                alert.details["action_result"] = "project stopped"
                log.warning(
                    "monitor[%s] auto-stopped project (rule=%s)", sess.project, rule.code
                )
            except EngineError as exc:
                alert.details["action_error"] = str(exc)
                log.error("monitor[%s] auto-stop failed: %s", sess.project, exc)
        elif rule.action == "pause_project":
            try:
                await self.engine.call(f"-project action=pause name={sess.project}", timeout=30.0)
                alert.details["action_result"] = "project paused"
            except EngineError as exc:
                alert.details["action_error"] = str(exc)


def _extract_count(text: str) -> int | None:
    """Extract the first integer from a sqcli databank-count response."""
    for line in text.splitlines():
        for tok in line.split():
            if tok.isdigit():
                return int(tok)
    return None


def _safe_median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _safe_percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    s = sorted(xs)
    idx = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[idx]
