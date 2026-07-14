"""Server-side safety control plane for shared sq-mcp deployments."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

READ_ONLY_PREFIXES = (
    "health_", "environment_", "session_", "workspace_", "engine_status",
    "engine_log", "project_list", "project_status", "project_inspect", "project_precheck",
    "inspect_", "sqx_inspect", "cfx_inspect", "cfx_validate", "cfx_lint", "cfx_compare",
    "databank_list", "databank_count", "databank_top", "databank_metric", "databank_snapshot",
    "strategy_", "stats_", "ratios_", "tail_risk_", "benchmark_", "portfolio_", "regime_",
    "tools_catalog", "common_workflows", "broker_registry", "data_timezones", "history_",
)
SCRATCH_WRITE_PREFIXES = ("cfx_set_", "cfx_toggle_", "cfx_configure_", "cfx_template_")
SCRATCH_WRITE_TOOLS = {"project_snapshot", "project_restore_snapshot", "cfx_transaction"}
MANAGED_WRITE_TOOLS = {
    "databank_force_sync", "databank_save", "databank_load", "project_config",
}
ADMIN_TOOLS = {
    "project_start", "project_stop", "project_pause", "project_resume", "project_remove",
    "project_force_remove", "databank_clear", "databank_merge", "databank_promote",
    "data_import", "data_update", "instrument_add", "instrument_delete",
}
DEPLOY_TOOLS = {
    "mt5_deploy_ea", "mt5_verify_deployment", "pipeline_export_to_mt5", "ship_pipeline_run",
}
LEVELS = {"read-only": 0, "scratch-write": 1, "managed-write": 2, "admin-approved": 3, "deploy-approved": 4}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _project_from(arguments: dict[str, Any]) -> str | None:
    payload = arguments.get("args", arguments)
    if not isinstance(payload, dict):
        return None
    for key in ("project", "name", "dest"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    path = payload.get("path") or payload.get("cfx_path")
    if isinstance(path, str):
        for part in Path(path).parts:
            if part.startswith("HERMES_CANARY_"):
                return part
    return None


def classify_tool(name: str) -> str:
    if name in DEPLOY_TOOLS or name.startswith("mt5_deploy"):
        return "deploy-approved"
    if name in ADMIN_TOOLS:
        return "admin-approved"
    if name in MANAGED_WRITE_TOOLS:
        return "managed-write"
    if name in SCRATCH_WRITE_TOOLS or name.startswith(SCRATCH_WRITE_PREFIXES):
        return "scratch-write"
    if name.startswith(READ_ONLY_PREFIXES):
        return "read-only"
    # Unknown tools are conservative: they require admin approval.
    return "admin-approved"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    required_level: str
    lease_id: str | None
    reason: str | None = None
    approval: dict[str, Any] | None = None


class WriterLeaseManager:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lease: dict[str, Any] | None = None

    def _active(self) -> dict[str, Any] | None:
        if self._lease and self._lease["expires_at"] > self._clock():
            return self._lease
        self._lease = None
        return None

    def acquire(self, client_id: str, ttl_seconds: float = 60.0) -> dict[str, Any]:
        active = self._active()
        if active and active["client_id"] != client_id:
            return {"ok": False, "error": "writer lease already held", "holder": active["client_id"], "expires_at": active["expires_at"]}
        lease_id = active["lease_id"] if active else secrets.token_urlsafe(18)
        self._lease = {"client_id": client_id, "lease_id": lease_id, "expires_at": self._clock() + ttl_seconds}
        return {"ok": True, **self._lease}

    def validate(self, client_id: str, lease_id: str | None) -> bool:
        active = self._active()
        return bool(active and active["client_id"] == client_id and active["lease_id"] == lease_id)

    def release(self, client_id: str, lease_id: str) -> bool:
        if self.validate(client_id, lease_id):
            self._lease = None
            return True
        return False

    def status(self) -> dict[str, Any]:
        return {"ok": True, "active": self._active()}


class IdempotencyStore:
    def __init__(self) -> None:
        self._items: dict[str, tuple[str, str, dict[str, Any]]] = {}

    def lookup(self, key: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        item = self._items.get(key)
        if not item:
            return None
        saved_tool, digest, result = item
        if saved_tool != tool or digest != hashlib.sha256(_canonical(arguments).encode()).hexdigest():
            raise ValueError("idempotency key reused for different request")
        return json.loads(json.dumps(result))

    def remember(self, key: str, tool: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        self._items[key] = (tool, hashlib.sha256(_canonical(arguments).encode()).hexdigest(), json.loads(json.dumps(result, default=str)))


class ApprovalAuthority:
    def __init__(self, *, secret: bytes, clock: Callable[[], float] = time.time) -> None:
        self._secret = secret
        self._clock = clock
        self._used: set[str] = set()

    def issue(self, *, scope: str, tool: str, parameters: dict[str, Any], ttl_seconds: float = 300.0) -> str:
        payload = {"jti": secrets.token_urlsafe(18), "scope": scope, "tool": tool, "params_hash": hashlib.sha256(_canonical(parameters).encode()).hexdigest(), "exp": self._clock() + ttl_seconds}
        raw = base64.urlsafe_b64encode(_canonical(payload).encode()).decode().rstrip("=")
        sig = hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()
        return raw + "." + sig

    def consume(self, token: str, tool: str, parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            raw, signature = token.split(".", 1)
            expected = hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid approval token signature")
            payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("invalid approval token") from exc
        if payload["jti"] in self._used:
            raise ValueError("approval token already used")
        if payload["exp"] <= self._clock():
            raise ValueError("approval token expired")
        if payload["tool"] != tool:
            raise ValueError("approval token tool mismatch")
        if payload["params_hash"] != hashlib.sha256(_canonical(parameters).encode()).hexdigest():
            raise ValueError("approval token parameters mismatch")
        self._used.add(payload["jti"])
        return {"ok": True, **payload}


class EvidenceFactory:
    def __init__(
        self,
        *,
        profile: str = "sqx-specialist",
        evidence_dir: Path | None = None,
        schema_path: Path | None = None,
    ) -> None:
        self.profile = profile
        self.evidence_dir = evidence_dir
        configured = os.getenv("SQX_EVIDENCE_SCHEMA")
        self.schema_path = schema_path or (Path(configured) if configured else None)
        self._validator: Draft202012Validator | None = None
        if self.schema_path:
            schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
            self._validator = Draft202012Validator(schema)

    def _make_envelope(self, *, tool: str, sanitized_inputs: dict[str, Any], result: dict[str, Any], before_state: dict[str, Any] | None, approval: dict[str, Any] | None, rollback_handle: Any) -> dict[str, Any]:
        ok = bool(result.get("ok", True))
        result_snapshot = json.loads(json.dumps(result, default=str))
        return {
            "initiative_id": None, "task_id": None, "session_id": None,
            "board": "sqx", "profile": self.profile, "specialist": "sqx-specialist",
            "tool_calls": [{"tool": tool, "ok": ok}], "sanitized_inputs": sanitized_inputs,
            "artifacts": [], "source_evidence": [], "approvals": [approval] if approval else [],
            "before_state": before_state, "after_state": result_snapshot,
            "warnings": list(result.get("warnings", [])) if isinstance(result.get("warnings"), list) else [],
            "rollback_handle": rollback_handle, "final_status": "PASS" if ok else "FAIL_BLOCKED",
            "residual_risks": [], "deep_links": [], "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def build(self, *, tool: str, sanitized_inputs: dict[str, Any], result: dict[str, Any], before_state: dict[str, Any] | None, approval: dict[str, Any] | None, rollback_handle: Any) -> dict[str, Any]:
        envelope = self._make_envelope(
            tool=tool,
            sanitized_inputs=sanitized_inputs,
            result=result,
            before_state=before_state,
            approval=approval,
            rollback_handle=rollback_handle,
        )
        if self._validator:
            self._validator.validate(envelope)
        if self.evidence_dir:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            path = self.evidence_dir / f"{int(time.time()*1000)}-{tool}.json"
            envelope["artifacts"].append(str(path))
            if self._validator:
                self._validator.validate(envelope)
            path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
        return envelope


class SafetyController:
    def __init__(self, *, policy_level: str | None = None, approval_secret: bytes | None = None, evidence_dir: Path | None = None) -> None:
        self.policy_level = policy_level or os.getenv("SQX_POLICY_LEVEL", "read-only")
        if self.policy_level not in LEVELS:
            raise ValueError(f"unknown policy level: {self.policy_level}")
        self.leases = WriterLeaseManager()
        self.idempotency = IdempotencyStore()
        self.approvals = ApprovalAuthority(secret=approval_secret or os.getenv("SQX_APPROVAL_SECRET", "local-dev-only").encode())
        self.evidence = EvidenceFactory(evidence_dir=evidence_dir)
        self._writer_lock = asyncio.Lock()

    def authorize(self, tool: str, arguments: dict[str, Any], metadata: dict[str, Any]) -> PolicyDecision:
        required = classify_tool(tool)
        if LEVELS[self.policy_level] < LEVELS[required]:
            return PolicyDecision(False, required, metadata.get("lease_id"), f"server policy {self.policy_level} is below {required}")
        project = _project_from(arguments)
        if required in {"scratch-write", "managed-write"} and (not project or not project.startswith("HERMES_CANARY_")):
            return PolicyDecision(False, required, metadata.get("lease_id"), "writes are limited to HERMES_CANARY_* projects")
        lease_id = metadata.get("lease_id")
        client_id = metadata.get("client_id")
        if required != "read-only" and not self.leases.validate(str(client_id), lease_id):
            return PolicyDecision(False, required, lease_id, "valid writer lease required")
        approval = None
        if required in {"admin-approved", "deploy-approved"}:
            token = metadata.get("approval_token")
            if not token:
                return PolicyDecision(False, required, lease_id, "approval token required")
            try:
                approval = self.approvals.consume(str(token), tool, arguments)
            except ValueError as exc:
                return PolicyDecision(False, required, lease_id, str(exc))
            if LEVELS[approval["scope"]] < LEVELS[required]:
                return PolicyDecision(False, required, lease_id, "approval token scope too low")
        return PolicyDecision(True, required, lease_id, approval=approval)

    async def execute(self, tool: str, arguments: dict[str, Any], metadata: dict[str, Any], operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        decision = self.authorize(tool, arguments, metadata)
        if not decision.allowed:
            return {"ok": False, "error": decision.reason, "required_level": decision.required_level}
        key = metadata.get("idempotency_key")
        if key:
            replay = self.idempotency.lookup(str(key), tool, arguments)
            if replay is not None:
                replay["_idempotent_replay"] = True
                return replay
        async def run() -> dict[str, Any]:
            result = await operation()
            if not isinstance(result, dict):
                result = {"ok": True, "result": result}
            result["evidence_envelope"] = self.evidence.build(tool=tool, sanitized_inputs=arguments, result=result, before_state=None, approval=decision.approval, rollback_handle=result.get("rollback_handle"))
            return result
        result = await run() if decision.required_level == "read-only" else await self._run_writer(run)
        if key:
            self.idempotency.remember(str(key), tool, arguments, result)
        return result

    async def _run_writer(self, operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        if self._writer_lock.locked():
            return {"ok": False, "error": "concurrent writer rejected"}
        async with self._writer_lock:
            return await operation()


def restore_project_snapshot(project_dir: Path, snapshot: Path) -> dict[str, Any]:
    if not project_dir.name.startswith("HERMES_CANARY_"):
        raise ValueError("project restore is limited to HERMES_CANARY_* projects")
    current = project_dir / "project.cfx"
    if snapshot.parent != project_dir or not snapshot.name.startswith("project.cfx.bak."):
        raise ValueError("snapshot must belong to the project")
    if not snapshot.is_file():
        raise ValueError("snapshot not found")
    pre_restore = current.with_name(f"project.cfx.bak.{int(time.time())}.pre_restore")
    shutil.copy2(current, pre_restore)
    tmp = current.with_suffix(".cfx.tmp")
    shutil.copy2(snapshot, tmp)
    os.replace(tmp, current)
    return {"ok": True, "project": project_dir.name, "restored_from": str(snapshot), "rollback_handle": str(pre_restore)}


def run_cfx_transaction(*, project_dir: Path, mutate: Callable[[Path], dict[str, Any]], inspect: Callable[[Path], dict[str, Any]], lint: Callable[[Path], dict[str, Any]], validate: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    if not project_dir.name.startswith("HERMES_CANARY_"):
        raise ValueError("CFX transactions are limited to HERMES_CANARY_* projects")
    current = project_dir / "project.cfx"
    snapshot = current.with_name(f"project.cfx.bak.{int(time.time()*1000)}.transaction")
    shutil.copy2(current, snapshot)
    mutation = mutate(current)
    inspection = inspect(current)
    lint_result = lint(current)
    validation = validate(current)
    ok = bool(mutation.get("ok", True) and inspection.get("ok", True) and lint_result.get("ok", True) and not lint_result.get("has_critical", False) and validation.get("ok", False))
    if not ok:
        tmp = current.with_suffix(".cfx.tmp")
        shutil.copy2(snapshot, tmp)
        os.replace(tmp, current)
    return {"ok": ok, "rolled_back": not ok, "snapshot": str(snapshot), "rollback_handle": str(snapshot), "mutation": mutation, "inspect": inspection, "lint": lint_result, "validate": validation}


__all__ = ["ApprovalAuthority", "EvidenceFactory", "IdempotencyStore", "PolicyDecision", "SafetyController", "WriterLeaseManager", "classify_tool", "restore_project_snapshot", "run_cfx_transaction"]
