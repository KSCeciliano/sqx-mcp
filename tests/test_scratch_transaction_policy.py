from sq_mcp.safety import SafetyController, classify_tool


def test_canary_transaction_and_snapshot_are_scratch_write():
    assert classify_tool("cfx_transaction") == "scratch-write"
    assert classify_tool("project_snapshot") == "scratch-write"
    assert classify_tool("project_restore_snapshot") == "scratch-write"


def test_scratch_policy_authorizes_canary_transaction_with_lease():
    controller = SafetyController(policy_level="scratch-write", approval_secret=b"test")
    lease = controller.leases.acquire("canary-client", 60)
    decision = controller.authorize(
        "cfx_transaction",
        {"args": {"project": "HERMES_CANARY_GATE"}},
        {"client_id": "canary-client", "lease_id": lease["lease_id"]},
    )
    assert decision.allowed is True
    assert decision.required_level == "scratch-write"

def test_scratch_policy_still_rejects_non_canary_transaction():
    controller = SafetyController(policy_level="scratch-write", approval_secret=b"test")
    lease = controller.leases.acquire("canary-client", 60)
    decision = controller.authorize(
        "cfx_transaction",
        {"args": {"project": "Builder"}},
        {"client_id": "canary-client", "lease_id": lease["lease_id"]},
    )
    assert decision.allowed is False
    assert "HERMES_CANARY" in (decision.reason or "")
