"""Orchestrate the 60K Builder run.

Clones GBPJPY BREAKOUT H1 - Dukascopy as-is (no date patching to avoid
inverted ranges) and bumps StopCondition.passedStrategies + hours so the
Builder runs until 60K strategies have passed all filters OR 18 hours
elapse — whichever comes first.

We deliberately keep the original date ranges (2003-2019 in-sample,
2018-2022 OOS, plus the cross-pair retest ranges). Patching dateFrom to
2023+ without also recomputing dateTo would invert several ranges and
silently fail the build phase.
"""
from __future__ import annotations

import asyncio
import io
import logging
import shutil
import sys
import zipfile
from pathlib import Path

from lxml import etree

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_ROOT))

from sq_mcp.config import detect_config
from sq_mcp.engine import EngineClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrate")

SOURCE_PROJECT = "GBPJPY BREAKOUT H1 - Dukascopy"
DEST_PROJECT = "GBPJPY_AUTO_60K_v3"
TARGET_STRATEGIES = 60000
SAFETY_HOURS = 18


def _patch_stop_condition(xml_bytes: bytes, *, passed: int, hours: int) -> bytes:
    root = etree.fromstring(xml_bytes)
    for el in root.iter():
        if etree.QName(el.tag).localname == "StopCondition":
            el.set("passedStrategies", str(passed))
            el.set("hours", str(hours))
            log.info("patched StopCondition: passedStrategies=%d, hours=%d", passed, hours)
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def _patch_config_xml_name(xml_bytes: bytes, new_name: str) -> bytes:
    root = etree.fromstring(xml_bytes)
    root.set("name", new_name)
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def _is_build_task(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return base.startswith("Build-Task") and base.endswith(".xml")


def clone_and_patch(projects_dir: Path) -> Path:
    src_dir = projects_dir / SOURCE_PROJECT
    src_cfx = src_dir / "project.cfx"
    dst_dir = projects_dir / DEST_PROJECT
    dst_cfx = dst_dir / "project.cfx"

    if not src_cfx.is_file():
        raise RuntimeError(f"source cfx missing: {src_cfx}")
    if dst_dir.exists():
        log.warning("removing existing destination: %s", dst_dir)
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(src_cfx, "r") as src, zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            raw = src.read(item.filename)
            if item.filename == "config.xml":
                raw = _patch_config_xml_name(raw, DEST_PROJECT)
            elif _is_build_task(item.filename):
                raw = _patch_stop_condition(raw, passed=TARGET_STRATEGIES, hours=SAFETY_HOURS)
            dst.writestr(item, raw)
    dst_cfx.write_bytes(buf.getvalue())
    log.info("cloned to %s", dst_cfx)
    return dst_cfx


async def main():
    cfg = detect_config()
    eng = EngineClient(cfg)
    await eng.attach_or_start()

    lic_raw = await eng.call("-license action=info")
    log.info("license: %s", lic_raw.strip()[:200])

    dst_cfx = clone_and_patch(cfg.projects_dir)

    log.info("loading project ...")
    text = await eng.call(f"-project action=loadconfig name={DEST_PROJECT} file={dst_cfx}")
    log.info("loadconfig: %s", text.strip()[:200])

    log.info("starting project ...")
    text = await eng.call(f"-project action=start name={DEST_PROJECT}")
    log.info("start: %s", text.strip()[:200])

    await eng.stop()


if __name__ == "__main__":
    asyncio.run(main())
