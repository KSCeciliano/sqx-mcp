import json
from pathlib import Path

import pytest

from sq_mcp.safety import (
    ApprovalAuthority,
    EvidenceFactory,
    IdempotencyStore,
    PolicyDecision,
    SafetyController,
    WriterLeaseManager,
)


def test_writer_lease_rejects_concurrent_writer_and_recovers_stale_lease():
    now = [100.0]
    leases = WriterLeaseManager(clock=lambda: now[0])
    first = leases.acquire("client-a", ttl_seconds=10)
    assert first["ok"] is True
    rejected = leases.acquire("client-b", ttl_seconds=10)
    assert rejected["ok"] is False
    assert rejected["error"] == "writer lease already held"
    now[0] = 111.0
    second = leases.acquire("client-b", ttl_seconds=10)
    assert second["ok"] is True
    assert second["lease_id"] != first["lease_id"]


def test_idempotency_store_returns_original_result_for_duplicate_key():
    store = IdempotencyStore()
    store.remember("k1", "tool", {"x": 1}, {"ok": True, "value": 7})
    assert store.lookup("k1", "tool", {"x": 1}) == {"ok": True, "value": 7}
    with pytest.raises(ValueError, match="different request"):
        store.lookup("k1", "tool", {"x": 2})


def test_approval_token_is_scoped_expiring_and_single_use():
    now = [100.0]
    auth = ApprovalAuthority(secret=b"test-secret", clock=lambda: now[0])
    token = auth.issue(
        scope="admin-approved",
        tool="project_remove",
        parameters={"args": {"name": "HERMES_CANARY_X"}},
        ttl_seconds=30,
    )
    assert auth.consume(token, "project_remove", {"args": {"name": "HERMES_CANARY_X"}})["ok"]
    with pytest.raises(ValueError, match="already used"):
        auth.consume(token, "project_remove", {"args": {"name": "HERMES_CANARY_X"}})

    expiring = auth.issue(scope="deploy-approved", tool="mt5_deploy_ea", parameters={}, ttl_seconds=5)
    now[0] = 106.0
    with pytest.raises(ValueError, match="expired"):
        auth.consume(expiring, "mt5_deploy_ea", {})


def test_policy_enforces_read_only_scratch_and_approval_levels():
    controller = SafetyController(policy_level="scratch-write", approval_secret=b"secret")
    read = controller.authorize("project_list", {"args": {}}, {})
    assert read == PolicyDecision(True, "read-only", None)

    scratch = controller.authorize(
        "cfx_set_fitness_criterion",
        {"args": {"project": "HERMES_CANARY_CFG"}},
        {"client_id": "c", "lease_id": controller.leases.acquire("c", 30)["lease_id"]},
    )
    assert scratch.allowed is True

    denied = controller.authorize(
        "cfx_set_fitness_criterion",
        {"args": {"project": "Builder"}},
        {"client_id": "c", "lease_id": scratch.lease_id},
    )
    assert denied.allowed is False


def test_evidence_factory_matches_required_envelope_shape(tmp_path: Path):
    schema_path = Path(
        "/mnt/c/Users/Administrator/AppData/Local/hermes/hermes-os/schemas/evidence-envelope.schema.json"
    )
    if not schema_path.exists():
        pytest.skip("Hermes OS evidence schema not present")
    schema = json.loads(schema_path.read_text())
    envelope = EvidenceFactory(profile="sqx-specialist").build(
        tool="project_list",
        sanitized_inputs={"args": {}},
        result={"ok": True},
        before_state=None,
        approval=None,
        rollback_handle=None,
    )
    assert set(schema["required"]).issubset(envelope)
    assert envelope["profile"] == "sqx-specialist"
    assert envelope["final_status"] == "PASS"


@pytest.mark.asyncio
async def test_controller_deduplicates_async_writer_execution():
    controller = SafetyController(policy_level="scratch-write", approval_secret=b"secret")
    lease = controller.leases.acquire("client-a", 30)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return {"ok": True, "calls": calls}

    metadata = {
        "client_id": "client-a",
        "lease_id": lease["lease_id"],
        "idempotency_key": "same-op",
    }
    args = {"args": {"project": "HERMES_CANARY_CFG"}}
    first = await controller.execute("cfx_set_fitness_criterion", args, metadata, operation)
    second = await controller.execute("cfx_set_fitness_criterion", args, metadata, operation)
    assert first["calls"] == 1
    assert second["calls"] == 1
    assert second["_idempotent_replay"] is True
    assert calls == 1


def test_project_restore_snapshot_rejects_non_canary(tmp_path: Path):
    from sq_mcp.safety import restore_project_snapshot

    project = tmp_path / "Builder"
    project.mkdir()
    current = project / "project.cfx"
    snap = project / "project.cfx.bak.test"
    current.write_bytes(b"current")
    snap.write_bytes(b"old")
    with pytest.raises(ValueError, match="HERMES_CANARY"):
        restore_project_snapshot(project, snap)


def test_cfx_transaction_rolls_back_when_validation_fails(tmp_path: Path):
    from sq_mcp.safety import run_cfx_transaction

    project = tmp_path / "HERMES_CANARY_CFG"
    project.mkdir()
    cfx = project / "project.cfx"
    cfx.write_bytes(b"before")

    def mutate(path: Path):
        path.write_bytes(b"broken")
        return {"ok": True}

    result = run_cfx_transaction(
        project_dir=project,
        mutate=mutate,
        inspect=lambda _: {"ok": True},
        lint=lambda _: {"ok": True, "has_critical": False},
        validate=lambda _: {"ok": False, "error": "invalid"},
    )
    assert result["ok"] is False
    assert result["rolled_back"] is True
    assert cfx.read_bytes() == b"before"

def test_singleton_lock_rejects_second_owner_and_recovers_stale(tmp_path: Path):
    from sq_mcp.singleton import SingletonLock

    lock_path = tmp_path / "sqx.lock"
    first = SingletonLock(lock_path, pid=111, pid_alive=lambda pid: pid == 111)
    first.acquire()
    second = SingletonLock(lock_path, pid=222, pid_alive=lambda pid: pid == 111)
    with pytest.raises(RuntimeError, match="already owned"):
        second.acquire()
    first.release()
    second.acquire()
    second.release()

    lock_path.write_text('{"pid":333}')
    stale = SingletonLock(lock_path, pid=444, pid_alive=lambda pid: False)
    stale.acquire()
    assert json.loads(lock_path.read_text())["pid"] == 444
    stale.release()
