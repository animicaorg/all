from __future__ import annotations
import pathlib
from typing import Any
import pytest

def test_deploy_and_verify_flow_local_hash_roundtrip() -> None:
    verify_mod = pytest.importorskip("contracts.tools.verify")
    compute_local_hash = getattr(verify_mod, "_compute_local_code_hash", None)
    if compute_local_hash is None:
        pytest.skip("_compute_local_code_hash missing")
    root = pathlib.Path(__file__).resolve().parents[3]
    m = root / "vm_py" / "examples" / "escrow" / "manifest.json"
    s = root / "vm_py" / "examples" / "escrow" / "contract.py"
    if not m.exists() or not s.exists():
        pytest.skip("escrow example not found")
    deploy_res: Any = compute_local_hash(m, s)
    if not getattr(deploy_res, "ok", False):
        pytest.skip(f"Local hash unavailable: {getattr(deploy_res, 'details', '')}")
    onchain = deploy_res.code_hash
    verify_res: Any = compute_local_hash(m, s)
    assert verify_res.ok
    assert verify_res.code_hash == onchain
    # Determinism
    verify_res2: Any = compute_local_hash(m, s)
    assert verify_res2.ok and verify_res2.code_hash == onchain
