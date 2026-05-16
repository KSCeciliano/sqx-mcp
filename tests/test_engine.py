"""Engine tests — require a real SQ X install. Auto-skip if missing."""

from __future__ import annotations

import asyncio

import pytest

from sq_mcp.engine import EngineClient

pytestmark = pytest.mark.asyncio


async def test_engine_starts_and_health_check(sqx_installed: bool):
    if not sqx_installed:
        pytest.skip("SQ X not installed locally")
    eng = EngineClient()
    try:
        await asyncio.wait_for(eng.attach_or_start(timeout=90.0), timeout=120.0)
        assert await eng.health()
    finally:
        await eng.stop()


async def test_engine_call_project_list(sqx_installed: bool):
    if not sqx_installed:
        pytest.skip("SQ X not installed locally")
    eng = EngineClient()
    try:
        await asyncio.wait_for(eng.attach_or_start(timeout=90.0), timeout=120.0)
        text = await eng.call("-project action=list")
        # we can't guarantee specific projects exist, but the call must succeed
        # and the response must not contain unfiltered DEBUG noise
        assert "DEBUG oshi" not in text
        assert "DEBUG o.s.os" not in text
    finally:
        await eng.stop()
