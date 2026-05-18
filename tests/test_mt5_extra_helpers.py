"""Unit tests for mt5_extra helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sq_mcp.tools.mt5_extra import (
    _assign_magic_numbers,
    _patch_magic_in_text,
    _sha256,
    _verify_deployment_against,
)

# ---- _assign_magic_numbers ------------------------------------------------


def test_assign_magic_numbers_is_deterministic() -> None:
    names = ["a.mq5", "b.mq5", "c.mq5"]
    a = _assign_magic_numbers(names, namespace="sq", floor=100000)
    b = _assign_magic_numbers(names, namespace="sq", floor=100000)
    assert a == b


def test_assign_magic_numbers_unique_per_name() -> None:
    names = [f"strat{i}.mq5" for i in range(50)]
    out = _assign_magic_numbers(names, namespace="sq", floor=100000)
    assert len(set(out.values())) == 50


def test_assign_magic_numbers_respects_floor() -> None:
    names = [f"s{i}.mq5" for i in range(20)]
    out = _assign_magic_numbers(names, namespace="sq", floor=999000)
    assert all(m >= 999000 for m in out.values())
    assert all(m <= 2_000_000_000 for m in out.values())


def test_assign_magic_numbers_namespace_separates_assignments() -> None:
    names = ["x.mq5"]
    acc_a = _assign_magic_numbers(names, namespace="acct-A", floor=100000)
    acc_b = _assign_magic_numbers(names, namespace="acct-B", floor=100000)
    # Same EA, different account → different magic (vanishingly unlikely to collide)
    assert acc_a["x.mq5"] != acc_b["x.mq5"]


# ---- _patch_magic_in_text -------------------------------------------------


_EA_SOURCE = """
//+------------------------------------------------------------------+
// Sample EA
//+------------------------------------------------------------------+
input int Magic = 123456;
input string Name = "demo";

int OnInit() {
   return 0;
}
"""


def test_patch_magic_in_text_replaces_first_match() -> None:
    new, count = _patch_magic_in_text(
        _EA_SOURCE,
        pattern=r"^(\s*(?:input\s+)?(?:int|long|uint)\s+(?:[A-Za-z_][A-Za-z0-9_]*)?[Mm]agic[A-Za-z0-9_]*\s*=\s*)(\d+)(\s*;.*)$",
        new_magic=999,
        target_all=False,
    )
    assert count == 1
    assert "Magic = 999;" in new
    assert "123456" not in new


def test_patch_magic_in_text_returns_zero_when_no_match() -> None:
    src = "// no magic here\nint OnInit() {return 0;}\n"
    _, count = _patch_magic_in_text(
        src, pattern=r"^(magic = )(\d+)(;)$", new_magic=42, target_all=False
    )
    assert count == 0


def test_patch_magic_in_text_target_all_replaces_every_match() -> None:
    src = "input int Magic = 1;\ninput int MagicTwo = 2;\n"
    new, count = _patch_magic_in_text(
        src,
        pattern=r"^(\s*(?:input\s+)?(?:int|long|uint)\s+(?:[A-Za-z_][A-Za-z0-9_]*)?[Mm]agic[A-Za-z0-9_]*\s*=\s*)(\d+)(\s*;.*)$",
        new_magic=42,
        target_all=True,
    )
    assert count == 2
    assert "= 1" not in new
    assert "= 2" not in new


def test_patch_magic_in_text_target_first_only_by_default() -> None:
    src = "input int Magic = 1;\ninput int MagicTwo = 2;\n"
    new, count = _patch_magic_in_text(
        src,
        pattern=r"^(\s*(?:input\s+)?(?:int|long|uint)\s+(?:[A-Za-z_][A-Za-z0-9_]*)?[Mm]agic[A-Za-z0-9_]*\s*=\s*)(\d+)(\s*;.*)$",
        new_magic=42,
        target_all=False,
    )
    assert count == 1
    # First was changed, second kept
    assert "Magic = 42;" in new
    assert "MagicTwo = 2;" in new


# ---- _sha256 --------------------------------------------------------------


def test_sha256_matches_stdlib(tmp_path: Path) -> None:
    p = tmp_path / "f.mq5"
    p.write_bytes(b"hello, world")
    expected = hashlib.sha256(b"hello, world").hexdigest()
    assert _sha256(p) == expected


# ---- _verify_deployment_against -----------------------------------------


def test_verify_deployment_finds_missing(tmp_path: Path) -> None:
    experts = tmp_path / "Experts"
    experts.mkdir()
    report = _verify_deployment_against(
        experts, [{"name": "abs.mq5"}, {"name": "miss.mq5"}]
    )
    assert report["present_count"] == 0
    assert report["absent_count"] == 2


def test_verify_deployment_validates_hash(tmp_path: Path) -> None:
    experts = tmp_path / "Experts"
    experts.mkdir()
    f = experts / "a.mq5"
    payload = b"some content"
    f.write_bytes(payload)
    expected_sha = hashlib.sha256(payload).hexdigest()

    # Good hash
    ok = _verify_deployment_against(experts, [{"name": "a.mq5", "sha256": expected_sha}])
    assert ok["present_count"] == 1
    assert ok["mismatched_count"] == 0

    # Bad hash → mismatched
    bad = _verify_deployment_against(experts, [{"name": "a.mq5", "sha256": "0" * 64}])
    assert bad["present_count"] == 0
    assert bad["mismatched_count"] == 1


def test_verify_deployment_handles_subdir(tmp_path: Path) -> None:
    experts = tmp_path / "Experts"
    sub = experts / "SQ"
    sub.mkdir(parents=True)
    f = sub / "a.mq5"
    f.write_bytes(b"x")
    report = _verify_deployment_against(
        experts, [{"name": "a.mq5", "subdir": "SQ"}]
    )
    assert report["present_count"] == 1


def test_verify_deployment_flags_missing_name() -> None:
    experts = Path("/tmp/never-existed-experts-9999")
    report = _verify_deployment_against(experts, [{}])
    assert report["absent_count"] == 1
    assert "missing 'name'" in report["absent"][0]["reason"]
