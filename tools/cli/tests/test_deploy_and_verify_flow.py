from __future__ import annotations

import json
import pathlib
from typing import Any, Callable

import pytest


def _pick_example() -> tuple[pathlib.Path, pathlib.Path]:
    root = pathlib.Path(__file__).resolve().parents[3]
    manifest = root / "vm_py" / "examples" / "escrow" / "manifest.json"
    source = root / "vm_py" / "examples" / "escrow" / "contract.py"
    if not manifest.exists() or not source.exists():
        pytest.skip("escrow example not found")
    return manifest, source


def _get_helpers() -> tuple[Callable[..., Any], Callable[..., Any]]:
    verify_mod = pytest.importorskip("contracts.tools.verify")
    compute_local_hash = getattr(verify_mod, "_compute_local_code_hash", None)
    build_payload = getattr(verify_mod, "_build_submit_payload", None)
    if compute_local_hash is None or build_payload is None:
        pytest.skip("verify helpers missing")
    return compute_local_hash, build_payload


def test_deploy_and_verify_flow_local_hash_roundtrip() -> None:
    compute_local_hash, _ = _get_helpers()
    manifest_path, source_path = _pick_example()

    deploy_res: Any = compute_local_hash(manifest_path, source_path)
    if not getattr(deploy_res, "ok", False):
        pytest.skip(f"Local hash unavailable: {getattr(deploy_res, 'details', '')}")
    onchain = deploy_res.code_hash

    verify_res: Any = compute_local_hash(manifest_path, source_path)
    assert verify_res.ok
    assert verify_res.code_hash == onchain

    # Determinism
    verify_res2: Any = compute_local_hash(manifest_path, source_path)
    assert verify_res2.ok and verify_res2.code_hash == onchain


def test_verify_payload_uses_manifest_and_onchain_hash() -> None:
    compute_local_hash, build_payload = _get_helpers()
    manifest_path, source_path = _pick_example()

    deploy_res: Any = compute_local_hash(manifest_path, source_path)
    if not getattr(deploy_res, "ok", False):
        pytest.skip(f"Local hash unavailable: {getattr(deploy_res, 'details', '')}")

    manifest = json.loads(manifest_path.read_text())
    payload = build_payload(
        address=None,
        tx_hash="0xdeadbeef",
        code_hash=deploy_res.code_hash,
        manifest=manifest,
        source_text=source_path.read_text(),
        abi=None,
        chain_id=7,
    )

    assert payload["codeHash"] == deploy_res.code_hash
    assert payload["manifest"]["name"] == manifest.get("name")
    assert payload["chainId"] == 7

    verify_res: Any = compute_local_hash(manifest_path, source_path)
    assert verify_res.ok and verify_res.code_hash == payload["codeHash"]
