"""Test fixtures.

Tests are split into two tiers:

* unit/parser tests — run anywhere, no SQ X required. Use sample files
  fixtures from tests/fixtures/ (or skip if absent).
* engine tests — require a working SQ X install on disk; auto-skip
  otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

SAMPLE_MQ5_ENV = "SQ_MCP_TEST_MQ5"
SAMPLE_SQX_ENV = "SQ_MCP_TEST_SQX"
SAMPLE_CFX_ENV = "SQ_MCP_TEST_CFX"


@pytest.fixture
def sample_mq5_path() -> Path:
    """Path to a real SQ-generated .mq5 — set via env or skip."""
    p = os.environ.get(SAMPLE_MQ5_ENV)
    if not p or not Path(p).exists():
        pytest.skip(f"Set {SAMPLE_MQ5_ENV}=/path/to/strategy.mq5 to enable")
    return Path(p)


@pytest.fixture
def sample_sqx_path() -> Path:
    p = os.environ.get(SAMPLE_SQX_ENV)
    if not p or not Path(p).exists():
        pytest.skip(f"Set {SAMPLE_SQX_ENV}=/path/to/strategy.sqx to enable")
    return Path(p)


@pytest.fixture
def sample_cfx_path() -> Path:
    p = os.environ.get(SAMPLE_CFX_ENV)
    if not p or not Path(p).exists():
        pytest.skip(f"Set {SAMPLE_CFX_ENV}=/path/to/project.cfx to enable")
    return Path(p)


@pytest.fixture
def sqx_installed() -> bool:
    from sq_mcp.config import detect_config
    return detect_config().is_valid
