"""Unit tests for dashboard helpers."""

from __future__ import annotations

from pathlib import Path

from sq_mcp.tools.dashboard import (
    _lineage_inventory,
    _project_inventory,
    _render_markdown,
    _stale_data,
    _tag_inventory,
)

# ---- _project_inventory ----------------------------------------------------


def test_project_inventory_missing_dir(tmp_path: Path) -> None:
    out = _project_inventory(tmp_path / "ghost")
    assert out["count"] == 0
    assert out["projects"] == []


def test_project_inventory_finds_cfx(tmp_path: Path) -> None:
    p = tmp_path / "MyProj"
    p.mkdir()
    (p / "project.cfx").write_bytes(b"X")
    out = _project_inventory(tmp_path)
    assert out["count"] == 1
    assert out["projects"][0]["name"] == "MyProj"


def test_project_inventory_counts_results(tmp_path: Path) -> None:
    p = tmp_path / "MyProj"
    p.mkdir()
    (p / "project.cfx").write_bytes(b"X")
    results = p / "databanks" / "Results"
    results.mkdir(parents=True)
    for i in range(5):
        (results / f"s{i}.sqx").write_bytes(b"X")
    out = _project_inventory(tmp_path)
    assert out["projects"][0]["results_count"] == 5


def test_project_inventory_skips_dirs_without_cfx(tmp_path: Path) -> None:
    (tmp_path / "no_cfx").mkdir()
    (tmp_path / "Has").mkdir()
    (tmp_path / "Has" / "project.cfx").write_bytes(b"X")
    out = _project_inventory(tmp_path)
    assert out["count"] == 1
    assert out["projects"][0]["name"] == "Has"


# ---- _tag_inventory --------------------------------------------------------


def test_tag_inventory_empty() -> None:
    out = _tag_inventory({"data": {}})
    assert out["n_strategies_tagged"] == 0
    assert out["n_distinct_tags"] == 0


def test_tag_inventory_counts_tags() -> None:
    state = {
        "data": {
            "tags": {
                "s1": ["prod", "trend"],
                "s2": ["prod"],
                "s3": ["test"],
            }
        }
    }
    out = _tag_inventory(state)
    assert out["n_strategies_tagged"] == 3
    assert out["n_distinct_tags"] == 3
    # Top tag should be 'prod' (2 strategies)
    assert out["top_tags"][0]["tag"] == "prod"
    assert out["top_tags"][0]["n_strategies"] == 2


# ---- _lineage_inventory ----------------------------------------------------


def test_lineage_inventory_empty() -> None:
    out = _lineage_inventory({"data": {}})
    assert out["n_nodes"] == 0
    assert out["n_root_strategies"] == 0


def test_lineage_inventory_counts_roots() -> None:
    state = {
        "data": {
            "lineage": {
                "root1": {"node_id": "root1", "parent_id": None},
                "root2": {"node_id": "root2", "parent_id": None},
                "child": {"node_id": "child", "parent_id": "root1"},
            }
        }
    }
    out = _lineage_inventory(state)
    assert out["n_nodes"] == 3
    assert out["n_root_strategies"] == 2


# ---- _stale_data -----------------------------------------------------------


def test_stale_data_empty_dir(tmp_path: Path) -> None:
    out = _stale_data(tmp_path / "ghost", stale_days=7)
    assert out == []


def test_stale_data_skips_fresh(tmp_path: Path) -> None:
    import os
    import time

    sym = tmp_path / "BTC"
    sym.mkdir()
    fresh = sym / "BTC_M1.dat"
    fresh.write_bytes(b"X")
    os.utime(fresh, (time.time(), time.time()))
    out = _stale_data(tmp_path, stale_days=7)
    assert out == []


def test_stale_data_flags_old(tmp_path: Path) -> None:
    import os
    import time

    sym = tmp_path / "BTC"
    sym.mkdir()
    old = sym / "BTC_M1.dat"
    old.write_bytes(b"X")
    old_mtime = time.time() - 30 * 86400
    os.utime(old, (old_mtime, old_mtime))
    out = _stale_data(tmp_path, stale_days=7)
    assert len(out) == 1
    assert out[0]["symbol"] == "BTC"
    assert out[0]["age_days"] > 7


# ---- _render_markdown ------------------------------------------------------


def test_render_markdown_smoke() -> None:
    payload = {
        "generated_at": "2026-05-18T00:00:00+00:00",
        "projects": {
            "count": 3,
            "total_results_strategies": 50,
            "recent": [{"name": "X", "results_count": 5, "mtime": "now"}],
        },
        "tags": {
            "n_strategies_tagged": 2,
            "n_distinct_tags": 2,
            "top_tags": [{"tag": "prod", "n_strategies": 1}],
        },
        "lineage": {"n_nodes": 5, "n_root_strategies": 2},
        "stale_data": [{"symbol": "BTC", "filename": "BTC_M1.dat", "age_days": 30.0}],
    }
    md = _render_markdown(payload, "Test")
    assert "Test" in md
    assert "**3**" in md
    assert "prod" in md
    assert "BTC_M1.dat" in md


def test_render_markdown_no_stale() -> None:
    payload = {
        "generated_at": "2026-05-18",
        "projects": {"count": 0, "total_results_strategies": 0, "recent": []},
        "tags": {"n_strategies_tagged": 0, "n_distinct_tags": 0, "top_tags": []},
        "lineage": {"n_nodes": 0, "n_root_strategies": 0},
        "stale_data": None,
    }
    md = _render_markdown(payload, "T")
    assert "Stale data" not in md
