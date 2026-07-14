import pytest

from sq_mcp.policy_server import install_policy_interceptor
from sq_mcp.tools import safety_control


class FakeMetadata:
    def convert_result(self, result):
        return {"converted": result}


class FakeTool:
    fn_metadata = FakeMetadata()


class FakeManager:
    _sqx_policy_installed = False

    def __init__(self):
        self.convert_flags = []
        self.tool = FakeTool()

    def get_tool(self, name):
        return self.tool

    async def call_tool(self, name, arguments, context=None, convert_result=False):
        self.convert_flags.append(convert_result)
        return {"ok": True, "value": 7}


class FakeMcp:
    _tool_manager = FakeManager()


@pytest.mark.asyncio
async def test_interceptor_governs_raw_result_then_converts_once(monkeypatch):
    monkeypatch.setenv("SQX_POLICY_LEVEL", "read-only")
    safety_control._CONTROLLER = None
    mcp = FakeMcp()
    install_policy_interceptor(mcp)

    result = await mcp._tool_manager.call_tool(
        "health_check", {"args": {}}, convert_result=True
    )

    assert mcp._tool_manager.convert_flags == [False]
    assert result["converted"]["ok"] is True
    assert result["converted"]["value"] == 7
    assert "evidence_envelope" in result["converted"]
