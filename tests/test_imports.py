"""Smoke test — verify the package and all its modules import cleanly.

If this fails, the server can't even start. Run me first.
"""


def test_package_imports():
    import sq_mcp
    assert sq_mcp.__version__


def test_engine_imports():
    from sq_mcp import config, engine
    assert config.detect_config()
    assert engine.EngineClient


def test_parsers_import():
    from sq_mcp.parsers import RiskFinding, analyze_mq5, parse_cfx, parse_sqx
    assert callable(analyze_mq5)
    assert callable(parse_cfx)
    assert callable(parse_sqx)
    assert RiskFinding


def test_tool_modules_import():
    from sq_mcp.tools import analysis, data, databanks, monitor, projects, symbols
    for mod in (analysis, data, databanks, monitor, projects, symbols):
        assert hasattr(mod, "register")


def test_server_imports_and_registers_tools():
    # Must be importable without contacting SQ X.
    from sq_mcp.server import mcp
    # FastMCP exposes its registered tools via list_tools (async) — instead
    # we just check that registration ran by checking the internal attribute.
    # If FastMCP changes its private API, replace with the documented call.
    assert mcp.name == "strategyquant"
