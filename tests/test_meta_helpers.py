"""Unit tests for meta (catalog + health_check) helpers."""

from __future__ import annotations

from pathlib import Path

from sq_mcp.tools.meta import _categorize_tool, _probe_tcp, _summarize_dir

# ---- _categorize_tool -----------------------------------------------------


def test_categorize_known_prefixes() -> None:
    assert _categorize_tool("project_start") == "projects"
    assert _categorize_tool("databank_filter") == "databanks"
    assert _categorize_tool("portfolio_dedupe") == "portfolio"
    assert _categorize_tool("strategy_summarize") == "strategy"
    assert _categorize_tool("cfx_set_data_range") == "cfx_config"
    assert _categorize_tool("data_import") == "data"
    assert _categorize_tool("mt5_deploy_ea") == "mt5"


def test_categorize_meta_tools() -> None:
    assert _categorize_tool("health_check") == "meta"
    assert _categorize_tool("tools_catalog") == "meta"
    assert _categorize_tool("workspace_overview") == "meta"


def test_categorize_unknown_falls_back_to_misc() -> None:
    assert _categorize_tool("totally_random_name") == "misc"


# ---- _probe_tcp -----------------------------------------------------------


def test_probe_tcp_false_for_unreachable_port() -> None:
    # 1 = privileged, almost certainly nothing listens
    assert _probe_tcp("127.0.0.1", 1, timeout=0.1) is False


def test_probe_tcp_handles_bad_host_safely() -> None:
    # Should return False, not raise
    assert _probe_tcp("definitely-not-a-host-12345.invalid", 80, timeout=0.1) is False


# ---- _summarize_dir -------------------------------------------------------


def test_summarize_dir_missing(tmp_path: Path) -> None:
    out = _summarize_dir(tmp_path / "nope")
    assert out["exists"] is False


def test_summarize_dir_existing(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "file.txt").write_text("x")
    out = _summarize_dir(tmp_path)
    assert out["exists"] is True
    assert out["is_dir"] is True
    assert out["entry_count"] == 2
    assert out["subdir_count"] == 1


def test_summarize_dir_file_not_dir(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("x")
    out = _summarize_dir(f)
    assert out["exists"] is True
    assert out["is_dir"] is False
