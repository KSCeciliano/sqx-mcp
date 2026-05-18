"""Unit tests for the filter / promote / merge helpers in tools/databanks.py."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from sq_mcp.tools.databanks import (
    DatabankFilterArgs,
    DatabankPromoteArgs,
    _existing_hashes,
    _passes_filter,
    _resolve_destination_filename,
)


def _make_sqx(path: Path, *, trades_hash: str, trades: int = 100, profit: float = 1000.0, dd: float = 200.0) -> Path:
    """Stamp a minimal .sqx with the supplied Fingerprint."""
    settings = f"""<?xml version="1.0"?>
<ResultsGroup ResultName="x">
  <ResultsMap><Results><Result resultKey="x">
    <Fitnesses IS="0.5" OOS="0.3" FS="0.4"/>
    <ValuesMap><Symbol type="String">BTCUSDT</Symbol><Timeframe type="String">H1</Timeframe></ValuesMap>
  </Result></Results></ResultsMap>
  <SpecialValuesMap><SettingsMap>
    <Fingerprint type="com.strategyquant.tradinglib.results.StrategyFingerprint">
      <Fingerprint strategyName="x" exact="1" trades="{trades}" profit="{profit}"
                   drawdown="{dd}" fitness="0.5" tradesHash="{trades_hash}"/>
    </Fingerprint>
  </SettingsMap></SpecialValuesMap>
</ResultsGroup>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version.txt", "1")
        z.writestr("settings.xml", settings)
    path.write_bytes(buf.getvalue())
    return path


# ---- _passes_filter ---------------------------------------------------------


def _filter_args(**overrides) -> DatabankFilterArgs:
    base = dict(project="test", databank="Results")
    base.update(overrides)
    return DatabankFilterArgs(**base)


def test_passes_filter_no_constraints() -> None:
    m = {"trades": 100, "net_profit": 1000, "drawdown_abs": 200, "profit_to_dd_ratio": 5.0}
    ok, reason = _passes_filter(m, _filter_args())
    assert ok is True
    assert reason is None


def test_passes_filter_min_trades() -> None:
    m = {"trades": 50}
    ok, reason = _passes_filter(m, _filter_args(min_trades=100))
    assert ok is False
    assert reason == "trades<100"


def test_passes_filter_max_drawdown_pct() -> None:
    m = {"drawdown_pct": 25.0}
    ok, reason = _passes_filter(m, _filter_args(max_drawdown_pct=20))
    assert ok is False
    # pydantic coerces int → float for the schema, so the reason embeds 20.0
    assert reason and reason.startswith("drawdown_pct>")
    assert "20" in reason


def test_passes_filter_min_profit_to_dd_ratio() -> None:
    m = {"profit_to_dd_ratio": 1.0}
    ok, reason = _passes_filter(m, _filter_args(min_profit_to_dd_ratio=2.0))
    assert ok is False
    assert reason == "profit_to_dd<2.0"


def test_passes_filter_excludes_when_metric_missing() -> None:
    """If a constraint demands a value but the metric is None, we reject."""
    m = {"trades": None}
    ok, _ = _passes_filter(m, _filter_args(min_trades=10))
    assert ok is False


def test_passes_filter_min_oos_is_ratio_overfit_guard() -> None:
    m = {"oos_is_ratio": 0.3}
    ok, reason = _passes_filter(m, _filter_args(min_oos_is_ratio=0.7))
    assert ok is False
    assert "oos_is_ratio" in reason


def test_passes_filter_exclude_ambiguous() -> None:
    m = {"ambiguous_trades": 3}
    ok, reason = _passes_filter(m, _filter_args(exclude_ambiguous=True))
    assert ok is False
    assert "ambiguous" in reason


def test_passes_filter_passes_all_constraints_together() -> None:
    m = {
        "trades": 250,
        "drawdown_pct": 10.0,
        "profit_to_dd_ratio": 3.0,
        "fitness_oos": 0.6,
        "oos_is_ratio": 0.85,
    }
    ok, _ = _passes_filter(m, _filter_args(
        min_trades=100,
        max_drawdown_pct=20,
        min_profit_to_dd_ratio=2.0,
        min_fitness_oos=0.5,
        min_oos_is_ratio=0.7,
    ))
    assert ok is True


# ---- _existing_hashes -------------------------------------------------------


def test_existing_hashes_collects_from_dir(tmp_path: Path) -> None:
    db = tmp_path / "bank"
    db.mkdir()
    _make_sqx(db / "a.sqx", trades_hash="aaa")
    _make_sqx(db / "b.sqx", trades_hash="bbb")
    # malformed file should be ignored
    (db / "broken.sqx").write_text("not a zip")
    hashes = _existing_hashes(db)
    assert hashes == {"aaa", "bbb"}


def test_existing_hashes_empty_dir(tmp_path: Path) -> None:
    db = tmp_path / "empty"
    db.mkdir()
    assert _existing_hashes(db) == set()


def test_existing_hashes_missing_dir(tmp_path: Path) -> None:
    assert _existing_hashes(tmp_path / "nope") == set()


# ---- _resolve_destination_filename -----------------------------------------


def test_resolve_destination_filename_no_collision(tmp_path: Path) -> None:
    dst = _resolve_destination_filename(tmp_path, "s.sqx", overwrite=False)
    assert dst == tmp_path / "s.sqx"


def test_resolve_destination_filename_overwrite_keeps_basename(tmp_path: Path) -> None:
    (tmp_path / "s.sqx").write_text("exists")
    dst = _resolve_destination_filename(tmp_path, "s.sqx", overwrite=True)
    assert dst == tmp_path / "s.sqx"


def test_resolve_destination_filename_versions_on_collision(tmp_path: Path) -> None:
    (tmp_path / "s.sqx").write_text("exists")
    dst = _resolve_destination_filename(tmp_path, "s.sqx", overwrite=False)
    assert dst == tmp_path / "s_v2.sqx"
    # And subsequent calls walk to _v3 etc.
    dst.write_text("now there")
    dst3 = _resolve_destination_filename(tmp_path, "s.sqx", overwrite=False)
    assert dst3 == tmp_path / "s_v3.sqx"


# ---- DatabankPromoteArgs validation ----------------------------------------


def test_databank_promote_args_rejects_invalid_project() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DatabankPromoteArgs(
            source_project="bad/name",
            source_databank="Results",
            dest_project="good_name",
            dest_databank="Strategies to retest",
        )


def test_databank_promote_args_accepts_minimal() -> None:
    args = DatabankPromoteArgs(
        source_project="Builder",
        source_databank="Results",
        dest_project="Retester",
        dest_databank="Strategies to retest",
    )
    assert args.top_n == 10
    assert args.sort_by == "profit_to_dd_ratio"
    assert args.dedupe_by_hash is True
    assert args.dry_run is False
