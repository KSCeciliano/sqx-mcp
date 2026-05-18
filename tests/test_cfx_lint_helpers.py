"""Unit tests for cfx_lint heuristic checks."""

from __future__ import annotations

from sq_mcp.tools.cfx_lint import _CfxAnalysis, _lint_cfx

# ---- no build-like task ---------------------------------------------------


def test_lint_no_build_like_returns_info_only() -> None:
    analysis = _CfxAnalysis(has_build_like=False)
    findings = _lint_cfx(analysis)
    assert len(findings) == 1
    assert findings[0].code == "NO_BUILD_LIKE_TASK"
    assert findings[0].severity == "info"


# ---- money management ----------------------------------------------------


def test_lint_no_active_mm_is_high() -> None:
    analysis = _CfxAnalysis(
        has_build_like=True,
        inspect={"money_management": {"active_method": None, "active_params": None}},
    )
    findings = _lint_cfx(analysis)
    codes = [f.code for f in findings]
    assert "NO_ACTIVE_MM" in codes


def test_lint_fixed_size_too_small() -> None:
    analysis = _CfxAnalysis(
        has_build_like=True,
        inspect={
            "money_management": {
                "active_method": "FixedSize",
                "active_params": {"Size": "0.001"},
            }
        },
    )
    findings = _lint_cfx(analysis)
    codes = [f.code for f in findings]
    assert "FIXED_SIZE_TOO_SMALL" in codes


def test_lint_fixed_size_normal_does_not_flag() -> None:
    analysis = _CfxAnalysis(
        has_build_like=True,
        inspect={
            "money_management": {
                "active_method": "FixedSize",
                "active_params": {"Size": "0.1"},
            }
        },
    )
    findings = _lint_cfx(analysis)
    codes = [f.code for f in findings]
    assert "FIXED_SIZE_TOO_SMALL" not in codes


# ---- OOS holdout ---------------------------------------------------------


def test_lint_no_oos_holdout_when_ratio_100() -> None:
    analysis = _CfxAnalysis(
        has_build_like=True,
        is_ratio=100,
        inspect={
            "money_management": {"active_method": "FixedSize", "active_params": {"Size": "0.1"}}
        },
    )
    findings = _lint_cfx(analysis)
    assert any(f.code == "NO_OOS_HOLDOUT" for f in findings)


def test_lint_oos_50_does_not_flag() -> None:
    analysis = _CfxAnalysis(
        has_build_like=True,
        is_ratio=50,
        inspect={
            "money_management": {"active_method": "FixedSize", "active_params": {"Size": "0.1"}}
        },
    )
    findings = _lint_cfx(analysis)
    assert not any(f.code == "NO_OOS_HOLDOUT" for f in findings)


# ---- GA caps -------------------------------------------------------------


def test_lint_population_too_small_flagged() -> None:
    analysis = _CfxAnalysis(has_build_like=True, population_size=30, inspect={})
    findings = _lint_cfx(analysis)
    assert any(f.code == "POPULATION_TOO_SMALL" for f in findings)


def test_lint_generations_too_few_flagged() -> None:
    analysis = _CfxAnalysis(has_build_like=True, max_generations=10, inspect={})
    findings = _lint_cfx(analysis)
    assert any(f.code == "GENERATIONS_TOO_FEW" for f in findings)


def test_lint_max_strategies_huge_flagged() -> None:
    analysis = _CfxAnalysis(has_build_like=True, max_strategies=20000, inspect={})
    findings = _lint_cfx(analysis)
    assert any(f.code == "MAX_STRATEGIES_HUGE" for f in findings)


# ---- Data range ----------------------------------------------------------


def test_lint_no_data_setup_critical() -> None:
    analysis = _CfxAnalysis(has_build_like=True, inspect={})
    findings = _lint_cfx(analysis)
    assert any(f.code == "NO_DATA_SETUP" for f in findings)


def test_lint_inverted_date_range_critical() -> None:
    analysis = _CfxAnalysis(
        has_build_like=True,
        inspect={
            "data_setups": [
                {"dateFrom": "2024.01.01", "dateTo": "2023.01.01", "testPrecision": "1"}
            ],
        },
    )
    findings = _lint_cfx(analysis)
    codes = [f.code for f in findings]
    assert "INVERTED_DATE_RANGE" in codes


def test_lint_test_precision_zero_flagged() -> None:
    analysis = _CfxAnalysis(
        has_build_like=True,
        inspect={
            "data_setups": [
                {"dateFrom": "2023.01.01", "dateTo": "2024.01.01", "testPrecision": "0"}
            ],
        },
    )
    findings = _lint_cfx(analysis)
    assert any(f.code == "TEST_PRECISION_ZERO" for f in findings)


def test_lint_clean_config_has_no_findings() -> None:
    """A reasonable config with no issues should produce empty findings."""
    analysis = _CfxAnalysis(
        has_build_like=True,
        population_size=100,
        max_generations=100,
        is_ratio=50,
        max_strategies=1000,
        inspect={
            "money_management": {
                "active_method": "RiskFixedBalancePct",
                "active_params": {"RiskedMoney": "1.0"},
            },
            "data_setups": [
                {"dateFrom": "2023.01.01", "dateTo": "2026.01.01", "testPrecision": "1"}
            ],
        },
    )
    findings = _lint_cfx(analysis)
    assert findings == []
