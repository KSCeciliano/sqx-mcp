import asyncio

import pytest

from sq_mcp.singleton import ManagedEnginePool


class FakeEngine:
    def __init__(self):
        self.starts = 0
        self.stops = 0

    async def attach_or_start(self):
        self.starts += 1
        return "spawned"

    async def stop(self):
        self.stops += 1


@pytest.mark.asyncio
async def test_managed_engine_pool_shares_one_engine_across_concurrent_clients():
    created = []

    def factory():
        engine = FakeEngine()
        created.append(engine)
        return engine

    pool = ManagedEnginePool(factory)
    a, b, c = await asyncio.gather(pool.acquire(), pool.acquire(), pool.acquire())
    assert a is b is c
    assert len(created) == 1
    assert created[0].starts == 1
    await pool.shutdown()
    assert created[0].stops == 1


@pytest.mark.asyncio
async def test_managed_engine_pool_recovers_after_shutdown():
    created = []

    def factory():
        engine = FakeEngine()
        created.append(engine)
        return engine

    pool = ManagedEnginePool(factory)
    first = await pool.acquire()
    await pool.shutdown()
    second = await pool.acquire()
    assert first is not second
    assert len(created) == 2
