"""Parse .sqx (StrategyQuant strategy + results) files.

A .sqx is a ZIP archive containing:
  settings.xml             — strategy logic, indicators, exits, MM, results
  lastSettings.xml         — last-saved config
  strategy_Portfolio.xml   — portfolio composition (rules tree)
  Results/<key>/dailyEquity.bin   — Java-serialized equity curve
  orders.bin               — Java-serialized trade list (use sqcli -tools orderstocsv)
  optimizationProfile.bin  — optimizer state
  version.txt
  META-INF/MANIFEST.MF

What we extract from settings.xml without invoking the engine:
  * Fitness values (IS, OOS, FS) — under <Result>/<Fitnesses>
  * Strategy fingerprint (trades, net profit, drawdown, tradesHash) —
    under <SpecialValuesMap>/<Fingerprint>
  * Strategy meta (backtest duration, complexity, history window,
    optimization parameters, equity-curve sparklines) — under
    <SpecialValuesMap>/<SettingsMap>
  * Symbol & instrument info (point value, tick size, broker, etc.) —
    under <SymbolsMap>/<SymbolInfo>
  * Per-result metadata (Symbol, Timeframe, ResultName) — under
    each <Result>/<ValuesMap>

Per-trade data (orders) and the raw equity-curve binary still require the
engine for full extraction; use sqcli's `-tools orderstocsv`.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import etree

from sq_mcp._xml import safe_fromstring

# Sparkline format SQ embeds in MEC_FULL_Main / MEC_IS_Main / MEC_OOS_Main.
# Looks like:  {{sparklinesWidget data='{"values":[0,0,1,2,...],"zeroPoint":0}'}}
_SPARK_RE = re.compile(r"data='(\{.*?\})'", re.DOTALL)


@dataclass
class SqxStats:
    """Per-Result fitness metrics. Field names left unchanged for back-compat."""
    fitness_is: float | None = None
    fitness_oos: float | None = None
    fitness_full: float | None = None
    raw: dict[str, str] = field(default_factory=dict)


@dataclass
class SqxFingerprint:
    """SQ's per-strategy summary numbers (one block per file in <SpecialValuesMap>)."""
    strategy_name: str | None = None
    trades: int | None = None
    net_profit: float | None = None
    drawdown: float | None = None
    fitness: float | None = None
    trades_hash: str | None = None
    exact: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class SqxStrategyMeta:
    """Extra strategy-level metadata pulled from SpecialValuesMap."""
    backtest_duration_years: float | None = None
    complexity: int | None = None
    history_from_ms: int | None = None
    history_to_ms: int | None = None
    history_from_iso: str | None = None
    history_to_iso: str | None = None
    last_modified_ms: int | None = None
    last_modified_iso: str | None = None
    ambiguous_trades: int | None = None
    strategy_problems: int | None = None
    total_ticks: int | None = None
    optimization_parameters: dict[str, str] = field(default_factory=dict)
    optimization_parameters_raw: str | None = None
    equity_curve_full: list[float] = field(default_factory=list)
    equity_curve_is: list[float] = field(default_factory=list)
    equity_curve_oos: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class SqxSymbolInfo:
    """Symbol + instrument metadata embedded in the .sqx."""
    symbol_name: str | None = None
    instrument_name: str | None = None
    point_value: float | None = None
    tick_size: float | None = None
    tick_step: float | None = None
    default_spread: float | None = None
    default_slippage: float | None = None
    decimals: int | None = None
    data_type: int | None = None
    broker_id: int | None = None
    exchange: str | None = None
    sector: str | None = None
    country: str | None = None
    description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class SqxResult:
    """One <Result> block. Some .sqx files have only one (the strategy itself);
    Builder/Retester databanks can carry portfolio + per-test results."""
    name: str
    is_portfolio: bool
    stats: SqxStats
    symbol: str | None = None
    timeframe: str | None = None
    initial_capital: float | None = None
    money_management: str | None = None
    settings: dict[str, str] = field(default_factory=dict)


@dataclass
class SqxFile:
    path: Path
    version: str | None
    result_name: str | None
    results: list[SqxResult] = field(default_factory=list)
    settings_xml_size: int = 0
    has_orders_bin: bool = False
    has_equity_bins: bool = False
    # Strategy-level enrichments (one block per file)
    fingerprint: SqxFingerprint | None = None
    meta: SqxStrategyMeta | None = None
    symbol_info: SqxSymbolInfo | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "version": self.version,
            "result_name": self.result_name,
            "results": [
                {
                    "name": r.name,
                    "is_portfolio": r.is_portfolio,
                    "symbol": r.symbol,
                    "timeframe": r.timeframe,
                    "initial_capital": r.initial_capital,
                    "stats": {
                        "fitness_is": r.stats.fitness_is,
                        "fitness_oos": r.stats.fitness_oos,
                        "fitness_full": r.stats.fitness_full,
                        "raw_fitnesses": r.stats.raw,
                    },
                    "settings": r.settings,
                }
                for r in self.results
            ],
            "has_orders_bin": self.has_orders_bin,
            "has_equity_bins": self.has_equity_bins,
            "fingerprint": self.fingerprint.as_dict() if self.fingerprint else None,
            "meta": self.meta.as_dict() if self.meta else None,
            "symbol_info": self.symbol_info.as_dict() if self.symbol_info else None,
        }


def parse_sqx(path: str | Path) -> SqxFile:
    """Parse a .sqx archive. Best-effort: missing/optional blocks return None."""
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
        root = safe_fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return

    info.result_name = root.get("ResultName")

    # ---- per-Result blocks ----
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

        symbol = None
        timeframe = None
        vmap = result_el.find("ValuesMap")
        if vmap is not None:
            sym_el = vmap.find("Symbol")
            tf_el = vmap.find("Timeframe")
            if sym_el is not None and sym_el.text:
                symbol = sym_el.text.strip()
            if tf_el is not None and tf_el.text:
                timeframe = tf_el.text.strip()

        settings: dict[str, str] = {}
        initial_capital = None
        money_management = None
        smap = result_el.find("SettingsMap")
        if smap is not None:
            for el in smap:
                if el.text is None:
                    continue
                settings[el.tag] = el.text
            initial_capital = _maybe_float(settings.get("MoneyManagement.InitialCapital"))
            money_management = settings.get("MoneyManagement.UseFromStrategy")

        info.results.append(
            SqxResult(
                name=key,
                is_portfolio=is_portfolio,
                stats=stats,
                symbol=symbol,
                timeframe=timeframe,
                initial_capital=initial_capital,
                money_management=money_management,
                settings=settings,
            )
        )

    # ---- SymbolInfo / InstrumentInfo ----
    sym_info_el = root.find(".//SymbolsMap/SymbolInfo")
    if sym_info_el is not None:
        instr_el = sym_info_el.find("InstrumentInfo")
        info.symbol_info = _parse_symbol_info(sym_info_el, instr_el)

    # ---- SpecialValuesMap (fingerprint + strategy meta) ----
    sv_el = root.find("SpecialValuesMap/SettingsMap")
    if sv_el is not None:
        info.fingerprint = _parse_fingerprint(sv_el)
        info.meta = _parse_strategy_meta(sv_el)


def _parse_symbol_info(sym_el: etree._Element, instr_el: etree._Element | None) -> SqxSymbolInfo:
    out = SqxSymbolInfo(
        symbol_name=sym_el.get("symbolName"),
        instrument_name=sym_el.get("instrumentName"),
    )
    if instr_el is not None:
        out.point_value = _maybe_float(instr_el.get("pointValue"))
        out.tick_size = _maybe_float(instr_el.get("tickSize"))
        out.tick_step = _maybe_float(instr_el.get("tickStep"))
        out.default_spread = _maybe_float(instr_el.get("defaultSpread"))
        out.default_slippage = _maybe_float(instr_el.get("defaultSlippage"))
        out.decimals = _maybe_int(instr_el.get("decimals"))
        out.data_type = _maybe_int(instr_el.get("dataType"))
        out.broker_id = _maybe_int(instr_el.get("broker"))
        out.exchange = instr_el.get("exchange") or None
        out.sector = instr_el.get("sector") or None
        out.country = instr_el.get("country") or None
        out.description = instr_el.get("description") or None
    return out


def _parse_fingerprint(svm: etree._Element) -> SqxFingerprint | None:
    wrap = svm.find("Fingerprint")
    if wrap is None:
        return None
    inner = wrap.find("Fingerprint")
    if inner is None:
        return None
    return SqxFingerprint(
        strategy_name=inner.get("strategyName"),
        trades=_maybe_int(inner.get("trades")),
        net_profit=_maybe_float(inner.get("profit")),
        drawdown=_maybe_float(inner.get("drawdown")),
        fitness=_maybe_float(inner.get("fitness")),
        trades_hash=inner.get("tradesHash"),
        exact=inner.get("exact"),
    )


def _parse_strategy_meta(svm: etree._Element) -> SqxStrategyMeta:
    """Pull every plain key→value entry under SpecialValuesMap/SettingsMap.

    SQ types its values: type='Double' / 'Long' / 'Integer' / 'String' /
    'Boolean'. We parse accordingly and ignore complex types we don't need.
    """
    text_kv: dict[str, tuple[str | None, str | None]] = {}
    for el in svm:
        tag = etree.QName(el.tag).localname
        text_kv[tag] = (el.get("type"), el.text)

    def _get_long(key: str) -> int | None:
        t, v = text_kv.get(key, (None, None))
        return _maybe_int(v) if v else None

    def _get_double(key: str) -> float | None:
        t, v = text_kv.get(key, (None, None))
        return _maybe_float(v) if v else None

    def _get_int(key: str) -> int | None:
        return _get_long(key)

    def _get_str(key: str) -> str | None:
        t, v = text_kv.get(key, (None, None))
        return v if v else None

    meta = SqxStrategyMeta(
        backtest_duration_years=_get_double("BacktestDuration"),
        complexity=_get_int("Complexity"),
        history_from_ms=_get_long("HistoryFrom"),
        history_to_ms=_get_long("HistoryTo"),
        last_modified_ms=_get_long("DateLastModified") or _get_long("LastModified"),
        ambiguous_trades=_get_int("AmbiguousTrades"),
        strategy_problems=_get_int("StrategyProblems"),
        total_ticks=_get_long("TotalTicks"),
        optimization_parameters_raw=_get_str("OptimizationParameters"),
    )
    meta.history_from_iso = _epoch_ms_to_iso(meta.history_from_ms)
    meta.history_to_iso = _epoch_ms_to_iso(meta.history_to_ms)
    meta.last_modified_iso = _epoch_ms_to_iso(meta.last_modified_ms)
    if meta.optimization_parameters_raw:
        meta.optimization_parameters = _parse_kv_list(meta.optimization_parameters_raw)

    meta.equity_curve_full = _parse_mec_sparkline(_get_str("MEC_FULL_Main"))
    meta.equity_curve_is = _parse_mec_sparkline(_get_str("MEC_IS_Main"))
    meta.equity_curve_oos = _parse_mec_sparkline(_get_str("MEC_OOS_Main"))
    return meta


def _parse_mec_sparkline(s: str | None) -> list[float]:
    """Pull the values list out of SQ's MEC sparkline embed string."""
    if not s:
        return []
    m = _SPARK_RE.search(s)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
        values = data.get("values", [])
        return [float(v) for v in values if isinstance(v, (int, float))]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _parse_kv_list(s: str) -> dict[str, str]:
    """Parse SQ's `key1=val1,key2=val2,...` strings (allows trailing comma)."""
    out: dict[str, str] = {}
    for token in s.split(","):
        token = token.strip()
        if not token or "=" not in token:
            continue
        k, _, v = token.partition("=")
        out[k.strip()] = v.strip()
    return out


def _epoch_ms_to_iso(ms: int | None) -> str | None:
    if ms is None or ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _maybe_float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _maybe_int(s: str | None) -> int | None:
    if s is None:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        # Allow strings like "10.0" → int(10)
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return None


# ---- derived metrics --------------------------------------------------------


def derive_metrics(info: SqxFile) -> dict[str, Any]:
    """Compute portfolio-level derived metrics from a parsed .sqx.

    All values may be None if their inputs are missing. The intent is to give
    a single dict with the numbers that matter for filtering / ranking, without
    each caller having to dig through the nested dataclasses.
    """
    fp = info.fingerprint
    meta = info.meta
    main_result = _select_main_result(info)
    out: dict[str, Any] = {
        "strategy_name": fp.strategy_name if fp else (info.result_name or None),
        "symbol": main_result.symbol if main_result else None,
        "timeframe": main_result.timeframe if main_result else None,
        "trades": fp.trades if fp else None,
        "net_profit": fp.net_profit if fp else None,
        "drawdown_abs": fp.drawdown if fp else None,
        "fitness_strategy": fp.fitness if fp else None,
        "fitness_is": main_result.stats.fitness_is if main_result else None,
        "fitness_oos": main_result.stats.fitness_oos if main_result else None,
        "fitness_full": main_result.stats.fitness_full if main_result else None,
        "trades_hash": fp.trades_hash if fp else None,
    }

    out["initial_capital"] = main_result.initial_capital if main_result else None

    history_years: float | None = None
    if meta is not None:
        out["sq_backtest_duration_metric"] = meta.backtest_duration_years
        out["complexity"] = meta.complexity
        out["history_from_iso"] = meta.history_from_iso
        out["history_to_iso"] = meta.history_to_iso
        out["optimization_parameters_count"] = len(meta.optimization_parameters)
        out["ambiguous_trades"] = meta.ambiguous_trades
        out["strategy_problems"] = meta.strategy_problems
        # Real history window length, derived from HistoryFrom/HistoryTo. SQ's
        # `BacktestDuration` is a runtime metric (NOT the date span), so we
        # compute years from the actual date attributes.
        if (
            meta.history_from_ms
            and meta.history_to_ms
            and meta.history_to_ms > meta.history_from_ms
        ):
            history_years = (
                (meta.history_to_ms - meta.history_from_ms) / 1000.0 / 86400.0 / 365.2425
            )
            out["history_years"] = round(history_years, 4)
        if meta.equity_curve_full:
            out["equity_curve_points"] = len(meta.equity_curve_full)
            out["equity_curve_first"] = meta.equity_curve_full[0]
            out["equity_curve_last"] = meta.equity_curve_full[-1]
            out["equity_curve_max"] = max(meta.equity_curve_full)
            out["equity_curve_min"] = min(meta.equity_curve_full)
    else:
        out["complexity"] = None

    # ratios — only set when inputs are usable
    profit = out["net_profit"]
    dd = out["drawdown_abs"]
    capital = out["initial_capital"]
    trades = out["trades"]

    out["return_pct"] = (
        round((profit / capital) * 100.0, 4)
        if profit is not None and capital and capital > 0
        else None
    )
    out["drawdown_pct"] = (
        round((dd / capital) * 100.0, 4)
        if dd is not None and capital and capital > 0
        else None
    )
    out["profit_to_dd_ratio"] = (
        round(profit / dd, 4) if profit is not None and dd and dd > 0 else None
    )
    out["avg_trade"] = (
        round(profit / trades, 4) if profit is not None and trades and trades > 0 else None
    )
    out["trades_per_year"] = (
        round(trades / history_years, 2)
        if trades is not None and history_years and history_years > 0
        else None
    )

    is_fit = out["fitness_is"]
    oos_fit = out["fitness_oos"]
    out["oos_is_ratio"] = (
        round(oos_fit / is_fit, 4)
        if is_fit and oos_fit is not None and is_fit > 0
        else None
    )

    return out


def _select_main_result(info: SqxFile) -> SqxResult | None:
    """Pick the canonical Result block for portfolio-level metrics."""
    if not info.results:
        return None
    # Prefer non-portfolio (the strategy itself); fall back to first
    for r in info.results:
        if not r.is_portfolio:
            return r
    return info.results[0]
