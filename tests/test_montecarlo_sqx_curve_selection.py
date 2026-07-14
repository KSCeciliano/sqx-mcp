from pathlib import Path

from sq_mcp.tools.montecarlo import _sqx_to_returns

REAL_SQX_CANDIDATES = [
    Path(r"C:/SQX_144_Full/user/projects/Retester/databanks/phase2_samples/NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx"),
    Path(r"/mnt/c/SQX_144_Full/user/projects/Retester/databanks/phase2_samples/NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx"),
]


def test_sqx_to_returns_uses_full_curve_from_real_sqx():
    sqx = next((p for p in REAL_SQX_CANDIDATES if p.exists()), None)
    assert sqx is not None, "expected real SQX sample path to exist on this machine"
    returns, meta = _sqx_to_returns(sqx)
    assert meta["ok"] is True
    assert meta["n_samples"] >= 10
    assert len(returns) == meta["n_samples"]
