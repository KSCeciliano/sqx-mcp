"""Managed-service safety, approval, rollback, and evidence tools."""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import validate_project_name
from sq_mcp.safety import SafetyController, restore_project_snapshot
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.projects import _validate_cfx_structure

_CONTROLLER: SafetyController | None = None


def get_controller() -> SafetyController:
    global _CONTROLLER
    if _CONTROLLER is None:
        evidence = os.getenv("SQX_EVIDENCE_DIR")
        _CONTROLLER = SafetyController(
            policy_level=os.getenv("SQX_POLICY_LEVEL", "read-only"),
            evidence_dir=Path(evidence) if evidence else None,
        )
    return _CONTROLLER


class WriterLeaseArgs(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=128)
    ttl_seconds: float = Field(60.0, ge=5.0, le=3600.0)


class WriterReleaseArgs(BaseModel):
    client_id: str
    lease_id: str


class ApprovalIssueArgs(BaseModel):
    admin_key: str
    scope: str
    tool: str
    parameters: dict[str, Any]
    ttl_seconds: float = Field(300.0, ge=5.0, le=3600.0)


class RestoreSnapshotArgs(BaseModel):
    project: str
    snapshot_path: str

    @field_validator("project")
    @classmethod
    def _project(cls, value: str) -> str:
        return validate_project_name(value)


class CfxTransactionArgs(BaseModel):
    project: str
    mutation_tool: str
    mutation_args: dict[str, Any]
    rollback_on_success: bool = False

    @field_validator("project")
    @classmethod
    def _project(cls, value: str) -> str:
        return validate_project_name(value)


CONTROL_TOOLS = {
    "safety_writer_acquire", "safety_writer_release", "safety_writer_status",
    "safety_issue_approval", "safety_policy_status",
}


def register(mcp: FastMCP) -> None:
    @mcp.tool(description="Acquire or renew the singleton writer lease used by managed SQX mutations.")
    async def safety_writer_acquire(args: WriterLeaseArgs) -> dict:
        return get_controller().leases.acquire(args.client_id, args.ttl_seconds)

    @mcp.tool(description="Release the singleton writer lease.")
    async def safety_writer_release(args: WriterReleaseArgs) -> dict:
        return {"ok": get_controller().leases.release(args.client_id, args.lease_id)}

    @mcp.tool(description="Return current writer lease and server-side safety policy state.")
    async def safety_writer_status() -> dict:
        return get_controller().leases.status()

    @mcp.tool(description="Return the enforced server-side safety level. No secrets are returned.")
    async def safety_policy_status() -> dict:
        controller = get_controller()
        return {"ok": True, "policy_level": controller.policy_level}

    @mcp.tool(description="Issue a scoped, expiring, single-use approval token. Requires SQX_APPROVAL_ADMIN_KEY.")
    async def safety_issue_approval(args: ApprovalIssueArgs) -> dict:
        expected = os.getenv("SQX_APPROVAL_ADMIN_KEY")
        if not expected or not secrets.compare_digest(args.admin_key, expected):
            return {"ok": False, "error": "invalid approval administrator key"}
        if args.scope not in {"admin-approved", "deploy-approved"}:
            return {"ok": False, "error": "scope must be admin-approved or deploy-approved"}
        token = get_controller().approvals.issue(
            scope=args.scope,
            tool=args.tool,
            parameters=args.parameters,
            ttl_seconds=args.ttl_seconds,
        )
        return {"ok": True, "approval_token": token, "scope": args.scope, "tool": args.tool}

    @mcp.tool(description="Restore project.cfx from a project-owned snapshot. Restricted to HERMES_CANARY_* projects by server policy.")
    async def project_restore_snapshot(args: RestoreSnapshotArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            project_dir = eng.config.projects_dir / args.project
            return restore_project_snapshot(project_dir, Path(args.snapshot_path))
        except (OSError, ValueError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(description="Execute a typed CFX mutation as snapshot -> mutation -> inspect/lint/validate with automatic rollback on failure. HERMES_CANARY_* only.")
    async def cfx_transaction(args: CfxTransactionArgs, ctx: Context) -> dict:
        if not args.project.startswith("HERMES_CANARY_"):
            return {"ok": False, "error": "CFX transactions are limited to HERMES_CANARY_* projects"}
        allowed = {
            "cfx_set_fitness_criterion", "cfx_set_money_management", "cfx_set_data_range",
            "cfx_set_max_strategies", "cfx_set_genetic_options", "cfx_set_instrument",
            "cfx_set_trade_caps", "cfx_set_sl_pt_range", "cfx_set_setup_attrs",
            "cfx_toggle_building_blocks",
        }
        if args.mutation_tool not in allowed:
            return {"ok": False, "error": "mutation tool is not transaction-approved"}
        eng = get_engine(ctx)
        project_dir = eng.config.projects_dir / args.project
        cfx_path = project_dir / "project.cfx"
        if not cfx_path.is_file():
            return {"ok": False, "error": f"project.cfx not found at {cfx_path}"}
        from sq_mcp.tools.cfx_lint import _analyze_cfx, _lint_cfx
        from sq_mcp.tools.projects import _make_snapshot

        snapshot = _make_snapshot(cfx_path, label="transaction")
        tool = mcp._tool_manager.get_tool(args.mutation_tool)
        if tool is None:
            return {"ok": False, "error": f"unknown mutation tool: {args.mutation_tool}"}
        mutation_payload = dict(args.mutation_args)
        mutation_payload["project"] = args.project
        try:
            mutation = await tool.run({"args": mutation_payload}, context=ctx, convert_result=False)
            validation = _validate_cfx_structure(cfx_path)
            analysis = _analyze_cfx(cfx_path)
            findings = _lint_cfx(analysis)
            lint = {
                "ok": True,
                "has_critical": any(f.severity == "critical" for f in findings),
                "findings": [f.as_dict() for f in findings],
            }
            ok = bool(mutation.get("ok", True) and validation.get("ok") and not lint["has_critical"])
            rolled_back = not ok or args.rollback_on_success
            if rolled_back:
                tmp = cfx_path.with_suffix(".cfx.tmp")
                shutil.copy2(snapshot, tmp)
                os.replace(tmp, cfx_path)
            return {
                "ok": ok,
                "rolled_back": rolled_back,
                "snapshot": str(snapshot),
                "rollback_handle": str(snapshot),
                "mutation": mutation,
                "inspect": {"path": str(cfx_path), "size": cfx_path.stat().st_size},
                "lint": lint,
                "validate": validation,
            }
        except Exception as exc:
            restore_project_snapshot(project_dir, snapshot)
            return {"ok": False, "rolled_back": True, "rollback_handle": str(snapshot), "error": str(exc)}


__all__ = ["CONTROL_TOOLS", "get_controller", "register"]
