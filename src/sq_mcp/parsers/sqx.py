"""Parse .sqx (StrategyQuant strategy + results) files.

A .sqx is a ZIP archive (Java JAR) containing:
  settings.xml             — strategy logic, indicators, exits, MM (the rules)
  lastSettings.xml         — last-saved config
  strategy_Portfolio.xml   — portfolio composition
  Results/<key>/dailyEquity.bin   — binary equity curves (Java-serialized; not parsed)
  orders.bin               — binary trade list (use sqcli -tools orderstocsv to extract)
  version.txt
  META-INF/MANIFEST.MF

We extract the high-level stats from settings.xml's <Fitnesses ...> attributes
and the ResultsGroup metadata. Per-trade data requires the engine.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree


@dataclass
class SqxStats:
    fitness_is: float | None = None
    fitness_oos: float | None = None
    fitness_full: float | None = None
    raw: dict[str, str] = field(default_factory=dict)


@dataclass
class SqxResult:
    name: str
    is_portfolio: bool
    stats: SqxStats


@dataclass
class SqxFile:
    path: Path
    version: str | None
    result_name: str | None
    results: list[SqxResult] = field(default_factory=list)
    settings_xml_size: int = 0
    has_orders_bin: bool = False
    has_equity_bins: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "version": self.version,
            "result_name": self.result_name,
            "results": [
                {
                    "name": r.name,
                    "is_portfolio": r.is_portfolio,
                    "stats": {
                        "fitness_is": r.stats.fitness_is,
                        "fitness_oos": r.stats.fitness_oos,
                        "fitness_full": r.stats.fitness_full,
                        "raw_fitnesses": r.stats.raw,
                    },
                }
                for r in self.results
            ],
            "has_orders_bin": self.has_orders_bin,
            "has_equity_bins": self.has_equity_bins,
        }


def parse_sqx(path: str | Path) -> SqxFile:
    p = Path(path)
    if not zipfile.is_zipfile(p):
        raise ValueError(f"{p} is not a ZIP — not a valid .sqx file")

    info = SqxFile(path=p, version=None, result_name=None)
    with zipfile.ZipFile(p) as z:
        names = set(z.namelist())
        info.has_orders_bin = "orders.bin" in names
        info.has_equity_bins = any(n.endswith("dailyEquity.bin") for n in names)
        if "version.txt" in names:
            info.version = z.read("version.txt").decode("utf-8", errors="replace").strip()
        if "settings.xml" in names:
            xml_bytes = z.read("settings.xml")
            info.settings_xml_size = len(xml_bytes)
            _populate_from_settings(info, xml_bytes)
    return info


def _populate_from_settings(info: SqxFile, xml_bytes: bytes) -> None:
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return

    info.result_name = root.get("ResultName")

    for result_el in root.iterfind(".//Result"):
        key = result_el.get("resultKey", "")
        is_portfolio = key.lower() == "portfolio"
        stats = SqxStats()
        fit = result_el.find("Fitnesses")
        if fit is not None:
            stats.raw = dict(fit.attrib)
            stats.fitness_is = _maybe_float(fit.get("IS"))
            stats.fitness_oos = _maybe_float(fit.get("OOS"))
            stats.fitness_full = _maybe_float(fit.get("FS"))
        info.results.append(SqxResult(name=key, is_portfolio=is_portfolio, stats=stats))


def _maybe_float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None
