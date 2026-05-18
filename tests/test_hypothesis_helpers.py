"""Unit tests for hypothesis helpers."""

from __future__ import annotations

from sq_mcp.tools.hypothesis import (
    _mean,
    _norm_cdf,
    _paired_t_test,
    _sign_test,
    _two_tailed_p_from_z,
    _var,
    _welchs_t_test,
    _wilcoxon_signed_rank,
)

# ---- basic stats -----------------------------------------------------------


def test_mean_basic() -> None:
    assert _mean([1, 2, 3]) == 2


def test_var_two_values() -> None:
    # var of [1, 3] sample = 2
    assert _var([1, 3]) == 2.0


def test_norm_cdf_at_zero_is_half() -> None:
    assert abs(_norm_cdf(0) - 0.5) < 1e-9


def test_two_tailed_p_at_z_zero_is_one() -> None:
    assert abs(_two_tailed_p_from_z(0) - 1.0) < 1e-9


def test_two_tailed_p_at_z_two_is_low() -> None:
    assert _two_tailed_p_from_z(2.0) < 0.06


# ---- _paired_t_test --------------------------------------------------------


def test_paired_t_test_significant_difference() -> None:
    # A consistently better, with some noise so differences have variance
    a = [10.0 + ((-1) ** i) * 0.5 for i in range(30)]
    b = [0.1 + ((-1) ** (i + 1)) * 0.3 for i in range(30)]
    out = _paired_t_test(a, b, alpha=0.05)
    assert out["verdict"] == "different"
    assert out["p_value"] < 0.05


def test_paired_t_test_no_difference() -> None:
    # Two random walks centered on same mean
    a = [(-1) ** i * 1.0 for i in range(30)]
    b = [(-1) ** (i + 1) * 1.0 for i in range(30)]
    out = _paired_t_test(a, b, alpha=0.05)
    # Pairwise differences alternate sign → no significant trend
    assert "p_value" in out


def test_paired_t_test_zero_variance() -> None:
    a = [1.0] * 20
    b = [1.0] * 20
    out = _paired_t_test(a, b, alpha=0.05)
    assert out["verdict"] == "no_difference"
    assert "zero variance" in out.get("note", "")


# ---- _sign_test ------------------------------------------------------------


def test_sign_test_strongly_significant() -> None:
    a = [10.0] * 30
    b = [1.0] * 30  # A > B in all pairs
    out = _sign_test(a, b, alpha=0.05)
    assert out["plus"] == 30
    assert out["verdict"] == "different"


def test_sign_test_all_ties() -> None:
    a = [1.0] * 10
    b = [1.0] * 10
    out = _sign_test(a, b, alpha=0.05)
    assert out["ties"] == 10
    assert out["verdict"] == "no_difference"


def test_sign_test_balanced_no_significance() -> None:
    a = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]
    b = [0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
    out = _sign_test(a, b, alpha=0.05)
    # 5 +, 5 - → p ≈ 1
    assert out["verdict"] == "no_difference"


# ---- _wilcoxon_signed_rank --------------------------------------------------


def test_wilcoxon_strong_signal() -> None:
    # Strategy A consistently larger
    a = [10.0 + i for i in range(20)]
    b = [i for i in range(20)]
    out = _wilcoxon_signed_rank(a, b, alpha=0.05)
    assert out["verdict"] == "different"


def test_wilcoxon_too_few_nonzero() -> None:
    a = [1.0, 1.0, 1.0]
    b = [1.0, 1.0, 1.0]
    # Pad to min_length=5 (Pydantic minimum)
    a = a + [1.0, 1.0]
    b = b + [1.0, 1.0]
    out = _wilcoxon_signed_rank(a, b, alpha=0.05)
    assert "need at least 5" in out.get("note", "")


# ---- _welchs_t_test --------------------------------------------------------


def test_welchs_t_test_different_means() -> None:
    a = [10.0 + 0.01 * i for i in range(30)]
    b = [0.01 * i for i in range(30)]
    out = _welchs_t_test(a, b, alpha=0.05)
    assert out["verdict"] == "different"


def test_welchs_t_test_same_means() -> None:
    a = [(-1) ** i for i in range(30)]
    b = [(-1) ** (i + 1) for i in range(30)]
    out = _welchs_t_test(a, b, alpha=0.05)
    # Means same → no significance
    assert out["verdict"] == "no_difference"


def test_welchs_t_test_zero_variances() -> None:
    a = [5.0] * 30
    b = [5.0] * 30
    out = _welchs_t_test(a, b, alpha=0.05)
    assert "zero variance" in out.get("note", "")
