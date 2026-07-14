"""FastMCP call interception for server-side policy enforcement."""

from __future__ import annotations

from typing import Any

from sq_mcp.tools.safety_control import CONTROL_TOOLS, get_controller

_POLICY_META_KEY = "_safety"


def install_policy_interceptor(mcp: Any) -> None:
    manager = mcp._tool_manager
    if getattr(manager, "_sqx_policy_installed", False):
        return
    original = manager.call_tool

    async def guarded_call_tool(
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
        convert_result: bool = False,
    ) -> Any:
        if name in CONTROL_TOOLS:
            return await original(name, arguments, context=context, convert_result=convert_result)
        raw_metadata = arguments.pop(_POLICY_META_KEY, {}) if isinstance(arguments, dict) else {}
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        if context is not None:
            metadata.setdefault("client_id", str(context.client_id or "anonymous"))

        async def operation() -> dict[str, Any]:
            result = await original(name, arguments, context=context, convert_result=convert_result)
            if isinstance(result, dict):
                return result
            return {"ok": True, "result": result}

        return await get_controller().execute(name, arguments, metadata, operation)

    manager.call_tool = guarded_call_tool
    manager._sqx_policy_installed = True


__all__ = ["install_policy_interceptor"]
