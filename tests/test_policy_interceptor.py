import pytest

from sq_mcp.policy_server import install_policy_interceptor
from sq_mcp.tools import safety_control


class FakeContext:
    client_id = "client-a"


@pytest.mark.asyncio
async def test_interceptor_allows_read_and_denies_default_write(monkeypatch):
    monkeypatch.setenv("SQX_POLICY_LEVEL", "read-only")
    safety_control._CONTROLLER = None
    from sq_mcp.server import mcp

    install_policy_interceptor(mcp)
    read = await mcp._tool_manager.call_tool("tools_catalog", {"args": {}}, context=FakeContext())
    assert read["ok"] is True
    denied = await mcp._tool_manager.call_tool(
        "cfx_set_fitness_criterion",
        {"args": {"project": "HERMES_CANARY_CFG", "criterion": "Sharpe"}},
        context=FakeContext(),
    )
    assert denied["ok"] is False
    assert denied["required_level"] == "scratch-write"


@pytest.mark.asyncio
async def test_interceptor_requires_lease_and_replays_idempotently(monkeypatch):
    monkeypatch.setenv("SQX_POLICY_LEVEL", "scratch-write")
    safety_control._CONTROLLER = None
    controller = safety_control.get_controller()
    lease = controller.leases.acquire("client-a", 60)

    class FakeTool:
        calls = 0
        async def run(self, arguments, context=None, convert_result=False):
            self.calls += 1
            return {"ok": True, "calls": self.calls}

    class FakeManager:
        _sqx_policy_installed = False
        def __init__(self):
            self.tool = FakeTool()
        async def call_tool(self, name, arguments, context=None, convert_result=False):
            return await self.tool.run(arguments, context, convert_result)

    class FakeMcp:
        _tool_manager = FakeManager()

    install_policy_interceptor(FakeMcp)
    payload = {
        "args": {"project": "HERMES_CANARY_CFG"},
        "_safety": {"client_id": "client-a", "lease_id": lease["lease_id"], "idempotency_key": "once"},
    }
    first = await FakeMcp._tool_manager.call_tool("cfx_set_fitness_criterion", dict(payload), context=FakeContext())
    second = await FakeMcp._tool_manager.call_tool("cfx_set_fitness_criterion", dict(payload), context=FakeContext())
    assert first["calls"] == 1
    assert second["calls"] == 1
    assert second["_idempotent_replay"] is True
    assert FakeMcp._tool_manager.tool.calls == 1
