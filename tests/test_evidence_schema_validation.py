import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from sq_mcp.safety import EvidenceFactory

SCHEMA = Path("/mnt/c/Users/Administrator/AppData/Local/hermes/hermes-os/schemas/evidence-envelope.schema.json")


def test_persisted_evidence_is_validated_against_runtime_schema(tmp_path):
    factory = EvidenceFactory(evidence_dir=tmp_path, schema_path=SCHEMA)
    envelope = factory.build(
        tool="health_check",
        sanitized_inputs={"args": {}},
        result={"ok": True},
        before_state=None,
        approval=None,
        rollback_handle=None,
    )
    saved = list(tmp_path.glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text())["profile"] == "sqx-specialist"
    assert envelope["artifacts"]


def test_invalid_evidence_is_rejected_before_persistence(tmp_path, monkeypatch):
    factory = EvidenceFactory(evidence_dir=tmp_path, schema_path=SCHEMA)
    monkeypatch.setattr(factory, "_make_envelope", lambda **_: {"profile": "sqx-specialist"})
    with pytest.raises(ValidationError):
        factory.build(
            tool="health_check",
            sanitized_inputs={},
            result={"ok": True},
            before_state=None,
            approval=None,
            rollback_handle=None,
        )
    assert list(tmp_path.glob("*.json")) == []
