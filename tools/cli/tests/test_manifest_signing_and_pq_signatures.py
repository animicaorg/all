from __future__ import annotations

import importlib
from dataclasses import replace

import pytest

from vm_py.runtime.manifest_provenance import compute_manifest_hash_for_provenance


def _enable_fake_dilithium(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    import pq.py.algs.dilithium3 as dilithium3
    import pq.py.sign as pq_sign
    import pq.py.verify as pq_verify

    importlib.reload(dilithium3)
    importlib.reload(pq_sign)
    importlib.reload(pq_verify)
    return dilithium3, pq_sign, pq_verify


def test_manifest_signature_roundtrip_and_domain_enforcement(monkeypatch: pytest.MonkeyPatch) -> None:
    dilithium3, pq_sign, pq_verify = _enable_fake_dilithium(monkeypatch)

    sk, pk = dilithium3.keypair(seed=b"manifest-signer")
    manifest = {
        "name": "demo",
        "version": "0.0.1",
        "entry": "contract.py",
        "metadata": {"author": "tester"},
    }
    manifest_hash = compute_manifest_hash_for_provenance(manifest).encode("utf-8")

    sig = pq_sign.sign_detached(manifest_hash, "dilithium3", sk, domain="contract")

    assert sig.domain == "contract"
    assert sig.alg_name == "dilithium3"

    assert pq_verify.verify_detached(manifest_hash, sig, pk)

    with pytest.raises(ValueError):
        pq_verify.verify_detached(manifest_hash, sig, pk, domain="generic", strict_domain=True)

    bad_alg_sig = replace(sig, alg_id=0x42)
    with pytest.raises(ValueError):
        pq_verify.verify_detached(manifest_hash, bad_alg_sig, pk)
