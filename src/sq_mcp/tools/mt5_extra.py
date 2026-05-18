"""MT5 deployment helpers: magic numbers, manifest, integrity checks.

These tools build on ``mt5.py`` (which handles raw filesystem deploy + log
tail) and add the operational pieces an agent needs when shipping a
portfolio of EAs to MetaTrader 5:

- ``mt5_assign_magic_numbers`` — generate a deterministic, collision-free
  magic-number map for a list of .mq5 paths. Magic numbers must be unique
  per account; the agent uses this map to wire each EA to its slot.
- ``mt5_patch_magic_in_source`` — surgically replace the ``input int
  Magic[...]; = NNN;`` line in a .mq5 file, backing up the original to
  ``<name>.mq5.bak``.
- ``mt5_verify_deployment`` — given an expected EA manifest, report which
  files are present in MQL5/Experts/, which are absent, and which differ
  by SHA-256 from a reference source.
- ``mt5_strategy_pack`` — convenience: take a list of .mq5 paths, copy them
  into a named subfolder under MQL5/Experts/, assign magic numbers, and
  emit a JSON manifest.

None of these spawn MT5 or talk to a Wine bridge; everything is local
filesystem manipulation that an agent can drive safely.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, resolve_safe_path
from sq_mcp.tools._common import safe_error_payload
from sq_mcp.tools.mt5 import _resolve_mt5_install_root

# ---- argument schemas ------------------------------------------------------


class Mt5AssignMagicArgs(BaseModel):
    ea_paths: list[str] = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Absolute paths to .mq5 / .ex5 files (one per EA).",
    )
    namespace: str = Field(
        "sq",
        max_length=32,
        description=(
            "Salt for the hash. Use a per-account or per-portfolio salt so the "
            "same EA gets a different magic across accounts."
        ),
    )
    magic_floor: int = Field(
        100000, ge=1, le=2_000_000_000,
        description="Lowest magic to issue (MT5 magic is int → fits in 31 bits).",
    )

    @field_validator("namespace")
    @classmethod
    def _v_ns(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", v):
            raise ValueError("namespace must be alphanumeric / _ / -")
        return v


class Mt5PatchMagicArgs(BaseModel):
    ea_path: str = Field(..., description="Path to the .mq5 source file to patch.")
    new_magic: int = Field(..., ge=1, le=2_000_000_000)
    variable_pattern: str = Field(
        r"^(\s*(?:input\s+)?(?:int|long|uint)\s+(?:[A-Za-z_][A-Za-z0-9_]*)?[Mm]agic[A-Za-z0-9_]*\s*=\s*)(\d+)(\s*;.*)$",
        description=(
            "Regex with three groups: prefix / current-value / suffix. Default "
            "matches common SQ patterns like 'input int Magic = 123;'."
        ),
    )
    target_all_matches: bool = Field(
        False,
        description=(
            "By default the FIRST match is patched. Set True to flip every match "
            "in the file (useful if an SQ-exported EA defines magic in 2 places)."
        ),
    )


class Mt5VerifyDeploymentArgs(BaseModel):
    expected: list[dict[str, str]] = Field(
        ...,
        description=(
            "List of {'name': 'BTCUSDT_strat1.mq5', 'sha256': '...', 'subdir': "
            "'SQ'} dicts. 'sha256' and 'subdir' are optional."
        ),
    )


class Mt5StrategyPackArgs(BaseModel):
    ea_paths: list[str] = Field(..., min_length=1, max_length=200)
    pack_name: str = Field(
        ...,
        max_length=64,
        description="Subfolder name under MQL5/Experts/ to hold this pack.",
    )
    namespace: str = Field("sq", max_length=32)
    overwrite: bool = Field(False, description="Replace existing files in the pack folder.")

    @field_validator("pack_name", "namespace")
    @classmethod
    def _v_safe_name(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", v):
            raise ValueError("must be alphanumeric / _ / -")
        return v


# ---- helpers ---------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _assign_magic_numbers(
    names: list[str], *, namespace: str, floor: int
) -> dict[str, int]:
    """Deterministic magic assignment. Stable across runs for the same inputs."""
    used: set[int] = set()
    out: dict[str, int] = {}
    span = 2_000_000_000 - floor
    for name in names:
        # Probe sequentially in case of collisions — bounded retries so we don't loop.
        for attempt in range(64):
            key = f"{namespace}:{name}:{attempt}".encode()
            n = int.from_bytes(hashlib.sha256(key).digest()[:4], "big")
            magic = floor + (n % span)
            if magic not in used:
                used.add(magic)
                out[name] = magic
                break
        else:  # pragma: no cover — only fires on astronomically unlikely collisions
            raise RuntimeError(f"could not assign collision-free magic for {name!r}")
    return out


def _patch_magic_in_text(
    text: str, *, pattern: str, new_magic: int, target_all: bool
) -> tuple[str, int]:
    """Return (new_text, replacements). 0 replacements means pattern not found."""
    rgx = re.compile(pattern, re.MULTILINE)
    count = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal count
        if not target_all and count >= 1:
            return match.group(0)
        count += 1
        return f"{match.group(1)}{new_magic}{match.group(3)}"

    new_text = rgx.sub(_sub, text)
    return new_text, count


def _verify_deployment_against(
    experts_dir: Path, expected: list[dict[str, str]]
) -> dict[str, Any]:
    """Compare an expected manifest against MQL5/Experts/."""
    present: list[dict[str, Any]] = []
    absent: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    for entry in expected:
        name = entry.get("name")
        subdir = entry.get("subdir") or ""
        sha = entry.get("sha256")
        if not name:
            absent.append({"reason": "missing 'name'", "entry": entry})
            continue
        target = experts_dir
        if subdir:
            target = target / subdir
        candidate = target / name
        if not candidate.is_file():
            absent.append({"name": name, "subdir": subdir, "expected_at": str(candidate)})
            continue
        if sha:
            try:
                actual = _sha256(candidate)
            except OSError as exc:
                mismatched.append({"name": name, "reason": str(exc)})
                continue
            if actual != sha:
                mismatched.append(
                    {
                        "name": name,
                        "subdir": subdir,
                        "expected_sha": sha,
                        "actual_sha": actual,
                    }
                )
                continue
        present.append({"name": name, "subdir": subdir, "path": str(candidate)})
    return {
        "present_count": len(present),
        "absent_count": len(absent),
        "mismatched_count": len(mismatched),
        "present": present,
        "absent": absent,
        "mismatched": mismatched,
    }


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Deterministically assign unique MT5 magic numbers (31-bit ints) to "
            "a list of EA files. Uses SHA-256(namespace:name) hashed into "
            "[magic_floor, 2_000_000_000]. Returns a name→magic map ready to be "
            "patched into source via mt5_patch_magic_in_source. Pure / no I/O."
        )
    )
    async def mt5_assign_magic_numbers(
        args: Mt5AssignMagicArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            names = [Path(p).name for p in args.ea_paths]
            mapping = _assign_magic_numbers(
                names, namespace=args.namespace, floor=args.magic_floor
            )
            return {
                "ok": True,
                "namespace": args.namespace,
                "magic_floor": args.magic_floor,
                "count": len(mapping),
                "assignment": [
                    {"name": n, "magic": mapping[n], "source": p}
                    for n, p in zip(names, args.ea_paths, strict=True)
                ],
            }
        except (ValidationError, OSError, RuntimeError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Surgically replace the magic-number literal in a .mq5 source file. "
            "Default regex matches common SQ-exported patterns: 'input int Magic = NNN;'. "
            "Override variable_pattern (a regex with 3 groups: prefix / current value / "
            "suffix) for non-standard EAs. Backs the original up to <name>.mq5.bak."
        )
    )
    async def mt5_patch_magic_in_source(
        args: Mt5PatchMagicArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            p = resolve_safe_path(args.ea_path, must_exist=True)
            if p.suffix.lower() != ".mq5":
                return {"ok": False, "error": "expected a .mq5 source file"}
            original = p.read_text(encoding="utf-8", errors="replace")
            new_text, replacements = _patch_magic_in_text(
                original,
                pattern=args.variable_pattern,
                new_magic=args.new_magic,
                target_all=args.target_all_matches,
            )
            if replacements == 0:
                return {
                    "ok": False,
                    "error": "pattern did not match — magic line not found",
                    "hint": (
                        "Inspect the file and pass a custom variable_pattern. "
                        "Required: 3 capture groups (prefix / value / suffix)."
                    ),
                }
            backup_path = p.with_suffix(p.suffix + ".bak")
            backup_path.write_text(original, encoding="utf-8")
            # Atomic write of the patched .mq5: write to .tmp then os.replace.
            # Prevents a half-written EA source from being loaded if the
            # process is killed mid-write.
            tmp_path = p.with_suffix(p.suffix + ".tmp")
            tmp_path.write_text(new_text, encoding="utf-8")
            os.replace(tmp_path, p)
            return {
                "ok": True,
                "ea_path": str(p),
                "backup": str(backup_path),
                "new_magic": args.new_magic,
                "replacements": replacements,
            }
        except (ValidationError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Verify a portfolio of EAs is correctly deployed in MQL5/Experts/. Pass "
            "an expected list of {'name': '...', 'sha256': '...', 'subdir': '...'} "
            "dicts; the tool reports which are present (and content-matched), "
            "which are absent, and which differ by hash. Read-only."
        )
    )
    async def mt5_verify_deployment(
        args: Mt5VerifyDeploymentArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            root = _resolve_mt5_install_root()
            if root is None:
                return {"ok": False, "error": "MT5 install not found"}
            experts_dir = root / "MQL5" / "Experts"
            report = _verify_deployment_against(experts_dir, args.expected)
            return {
                "ok": True,
                "experts_dir": str(experts_dir),
                **report,
            }
        except OSError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Bundle multiple EA source files into a named subfolder under "
            "MQL5/Experts/, assign unique magic numbers, and emit a manifest.json "
            "with names + magic numbers + SHA-256s. Use this right after "
            "strategy_export_pipeline to ship a deploy-ready portfolio. "
            "Does NOT patch source — that's mt5_patch_magic_in_source's job."
        )
    )
    async def mt5_strategy_pack(
        args: Mt5StrategyPackArgs, ctx: Context  # noqa: ARG001
    ) -> dict:
        try:
            root = _resolve_mt5_install_root()
            if root is None:
                return {"ok": False, "error": "MT5 install not found"}
            target_root = root / "MQL5" / "Experts" / args.pack_name
            target_root.mkdir(parents=True, exist_ok=True)

            names = [Path(p).name for p in args.ea_paths]
            magic = _assign_magic_numbers(
                names, namespace=args.namespace, floor=100_000
            )

            entries: list[dict[str, Any]] = []
            skipped: list[dict[str, str]] = []
            for src_str in args.ea_paths:
                src = resolve_safe_path(src_str, must_exist=True)
                dst = target_root / src.name
                if dst.exists() and not args.overwrite:
                    skipped.append({"name": src.name, "reason": "exists; pass overwrite=True"})
                    continue
                shutil.copy2(src, dst)
                entries.append(
                    {
                        "name": src.name,
                        "magic": magic[src.name],
                        "sha256": _sha256(dst),
                        "source": str(src),
                        "deployed_to": str(dst),
                    }
                )

            manifest = {
                "pack_name": args.pack_name,
                "namespace": args.namespace,
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "ea_count": len(entries),
                "entries": entries,
            }
            manifest_path = target_root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return {
                "ok": True,
                "pack_dir": str(target_root),
                "manifest_path": str(manifest_path),
                "manifest": manifest,
                "skipped": skipped,
            }
        except (ValidationError, OSError, RuntimeError) as exc:
            return safe_error_payload(exc)


__all__ = [
    "Mt5AssignMagicArgs",
    "Mt5PatchMagicArgs",
    "Mt5StrategyPackArgs",
    "Mt5VerifyDeploymentArgs",
    "_assign_magic_numbers",
    "_patch_magic_in_text",
    "_sha256",
    "_verify_deployment_against",
    "register",
]
