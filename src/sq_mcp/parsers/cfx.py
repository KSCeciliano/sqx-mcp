"""Parse and (eventually) modify .cfx (StrategyQuant project config) files.

A .cfx is a ZIP archive containing:
  config.xml                 — top-level Project metadata (tasks list, databanks)
  <Task>-Task<n>.xml         — one per task: Build / Retest / Optimize / WalkForward...

We expose a read-only SqProjectConfig view here. Programmatic mutation
(e.g. patching backtest dates, fitness function, robustness checks) lives in
sq_mcp.tools.projects so it can use this as a source of truth.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree


@dataclass
class CfxTask:
    name: str
    type: str  # "Retest" | "Build" | "Optimize" | "WalkForwardOptimization" | ...
    active: bool
    sample_name: str | None
    xml_file: str
    xml_size: int


@dataclass
class CfxDatabank:
    name: str
    view: str
    sync_type: str


@dataclass
class CfxFile:
    path: Path
    project_name: str
    sqx_version: str | None
    tasks: list[CfxTask] = field(default_factory=list)
    databanks: list[CfxDatabank] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "project_name": self.project_name,
            "sqx_version": self.sqx_version,
            "tasks": [t.__dict__ for t in self.tasks],
            "databanks": [d.__dict__ for d in self.databanks],
        }


def parse_cfx(path: str | Path) -> CfxFile:
    p = Path(path)
    if not zipfile.is_zipfile(p):
        raise ValueError(f"{p} is not a ZIP — not a valid .cfx file")

    with zipfile.ZipFile(p) as z:
        names = set(z.namelist())
        if "config.xml" not in names:
            raise ValueError(f"{p} is missing config.xml")
        cfg_bytes = z.read("config.xml")
        try:
            cfg = etree.fromstring(cfg_bytes)
        except etree.XMLSyntaxError as exc:
            raise ValueError(f"config.xml in {p} is not valid XML: {exc}") from exc

        info = CfxFile(
            path=p,
            project_name=cfg.get("name", ""),
            sqx_version=cfg.get("version"),
        )
        for task_el in cfg.iterfind(".//Task"):
            xml_file = task_el.get("taskXMLFile", "")
            xml_size = z.getinfo(xml_file).file_size if xml_file in names else 0
            info.tasks.append(
                CfxTask(
                    name=task_el.get("name", ""),
                    type=task_el.get("type", ""),
                    active=task_el.get("active", "true").lower() == "true",
                    sample_name=task_el.get("sampleName"),
                    xml_file=xml_file,
                    xml_size=xml_size,
                )
            )
        for db_el in cfg.iterfind(".//Databank"):
            info.databanks.append(
                CfxDatabank(
                    name=db_el.get("name", ""),
                    view=db_el.get("view", ""),
                    sync_type=db_el.get("syncType", ""),
                )
            )
    return info
