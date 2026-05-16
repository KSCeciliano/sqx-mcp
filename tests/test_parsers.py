"""Parser tests — exercise the pure-Python file parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from sq_mcp.parsers import analyze_mq5, parse_cfx, parse_sqx
from sq_mcp.parsers.mq5 import RiskFinding


def test_mq5_parser_extracts_metadata(sample_mq5_path: Path):
    s = analyze_mq5(sample_mq5_path)
    assert s.path == sample_mq5_path
    assert s.sq_build is not None
    # SQ EAs should always carry these
    assert s.magic_number is not None
    assert s.indicators, "should detect at least one tester_indicator declaration"


def test_mq5_parser_runs_rules(sample_mq5_path: Path):
    s = analyze_mq5(sample_mq5_path)
    assert isinstance(s.findings, list)
    for f in s.findings:
        assert isinstance(f, RiskFinding)
        assert f.severity in {"critical", "high", "medium", "low", "info"}
        assert f.code
        assert f.title


def test_mq5_default_magic_number_finding(sample_mq5_path: Path):
    s = analyze_mq5(sample_mq5_path)
    if s.magic_number == 11111:
        codes = {f.code for f in s.findings}
        assert "DEFAULT_MAGIC_NUMBER" in codes


def test_sqx_parser_reads_zip(sample_sqx_path: Path):
    info = parse_sqx(sample_sqx_path)
    assert info.path == sample_sqx_path
    # Either has a result_name or has results — at least one should be true
    assert info.result_name or info.results


def test_sqx_parser_extracts_fitness(sample_sqx_path: Path):
    info = parse_sqx(sample_sqx_path)
    if info.results:
        # at least one result should have a numeric fitness
        any_fit = any(
            r.stats.fitness_is is not None or r.stats.fitness_oos is not None
            for r in info.results
        )
        assert any_fit, "expected at least one parseable Fitnesses block"


def test_cfx_parser_reads_project(sample_cfx_path: Path):
    info = parse_cfx(sample_cfx_path)
    assert info.path == sample_cfx_path
    assert info.project_name
    # CFX always declares at least one task
    assert info.tasks


def test_sqx_rejects_non_zip(tmp_path: Path):
    bad = tmp_path / "fake.sqx"
    bad.write_text("not a zip")
    with pytest.raises(ValueError, match="not a ZIP"):
        parse_sqx(bad)


def test_cfx_rejects_non_zip(tmp_path: Path):
    bad = tmp_path / "fake.cfx"
    bad.write_text("not a zip")
    with pytest.raises(ValueError, match="not a ZIP"):
        parse_cfx(bad)
