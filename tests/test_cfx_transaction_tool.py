import hashlib
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from sq_mcp.server import mcp
from sq_mcp.tools import safety_control


class FakeContext:
    def __init__(self, projects_dir: Path):
        engine = SimpleNamespace(config=SimpleNamespace(projects_dir=projects_dir))
        self.request_context = SimpleNamespace(lifespan_context=engine)


@pytest.mark.asyncio
async def test_cfx_transaction_rolls_back_successful_mutation_when_requested(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    project = projects / "HERMES_CANARY_GATE"
    project.mkdir(parents=True)
    source = Path("/mnt/c/SQX_144_Full/user/projects/HERMES_CANARY_20260714_041341/project.cfx")
    if not source.is_file():
        pytest.skip("real canary CFX fixture unavailable")
    cfx = project / "project.cfx"
    shutil.copy2(source, cfx)
    before = hashlib.sha256(cfx.read_bytes()).hexdigest()

    transaction_tool = mcp._tool_manager.get_tool("cfx_transaction")

    class FakeTool:
        async def run(self, arguments, context=None, convert_result=False):
            cfx.write_bytes(cfx.read_bytes() + b"temporary-change")
            return {"ok": True, "changed": True}

    monkeypatch.setattr(
        mcp._tool_manager,
        "get_tool",
        lambda name: transaction_tool if name == "cfx_transaction" else FakeTool(),
    )
    monkeypatch.setattr(safety_control, "get_engine", lambda _: SimpleNamespace(config=SimpleNamespace(projects_dir=projects)))
    monkeypatch.setattr(safety_control, "_validate_cfx_structure", lambda _: {"ok": True})
    monkeypatch.setattr("sq_mcp.tools.cfx_lint._analyze_cfx", lambda _: {})
    monkeypatch.setattr("sq_mcp.tools.cfx_lint._lint_cfx", lambda _: [])

    tool = mcp._tool_manager.get_tool("cfx_transaction")
    result = await tool.fn(
        safety_control.CfxTransactionArgs(
            project="HERMES_CANARY_GATE",
            mutation_tool="cfx_set_max_strategies",
            mutation_args={"max_strategies": 11},
            rollback_on_success=True,
        ),
        FakeContext(projects),
    )
    assert result["ok"] is True
    assert result["rolled_back"] is True
    assert hashlib.sha256(cfx.read_bytes()).hexdigest() == before
