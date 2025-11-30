import json
import os
from pathlib import Path

import pytest

from zk.integration.omni_hooks import zk_verify
from zk.tests import configure_test_logging, fixture_path, read_json

configure_test_logging()

"""
Test: Groth16 (BN254) verification for an "embedding threshold" style circuit.

Looks for fixtures in either:
  - $ZK_GROTH16_EMBED_DIR (env override), or
  - zk/tests/fixtures/groth16_embedding/

Expected files:
  - proof.json  (SnarkJS-style Groth16 proof; ideally includes "publicSignals")
  - vk.json     (SnarkJS-style verification key)

The test constructs a canonical ProofEnvelope and asserts verify == True.
"""


def _fixture_dir() -> Path:
    env = os.getenv("ZK_GROTH16_EMBED_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # default under repo
    return fixture_path("groth16_embedding")


def _load_proof_vk() -> tuple[dict, dict, list[str]]:
    base = _fixture_dir()
    proof_p = base / "proof.json"
    vk_p = base / "vk.json"

    if not proof_p.exists() or not vk_p.exists():
        pytest.skip(
            f"Missing fixtures: {proof_p if not proof_p.exists() else ''} "
            f"{vk_p if not vk_p.exists() else ''}. "
            "Provide groth16_embedding/proof.json and vk.json or set $ZK_GROTH16_EMBED_DIR."
        )

    with proof_p.open("r", encoding="utf-8") as fh:
        proof = json.load(fh)
    with vk_p.open("r", encoding="utf-8") as fh:
        vk = json.load(fh)

    # Prefer embedded publicSignals if present; else try separate file.
    pub_inputs: list[str] = []
    if isinstance(proof, dict) and "publicSignals" in proof:
        pub_inputs = proof["publicSignals"]
    else:
        pub_p = base / "public.json"
        if pub_p.exists():
            with pub_p.open("r", encoding="utf-8") as fh:
                pub_inputs = json.load(fh)
        else:
            pytest.skip(
                "No public inputs found: proof.json lacks 'publicSignals' and no public.json present."
            )
    if not isinstance(pub_inputs, list):
        raise TypeError(
            "publicSignals/public.json must be a list of field elements (hex or decimal strings)."
        )

    return proof, vk, pub_inputs


@pytest.mark.slow
def test_groth16_embedding_verify_ok():
    proof, vk, public_inputs = _load_proof_vk()

    envelope = {
        "kind": "groth16_bn254",
        "vk_format": "snarkjs",
        # In production you'd prefer vk_ref pinned in the registry; the test embeds the VK directly.
        "vk": vk,
        "proof": proof,
        "public_inputs": public_inputs,
        "meta": {"circuit_id": "embedding_threshold_groth16_bn254@test"},
    }

    res = zk_verify(envelope)
    assert isinstance(res, dict), "zk_verify should return a dict-like result"
    assert res.get("ok") is True, f"Verification failed: {res}"
    units = res.get("units")
    assert isinstance(units, int) and units > 0, f"Unexpected metering units: {units}"


@pytest.mark.slow
def test_groth16_embedding_meter_only():
    proof, vk, public_inputs = _load_proof_vk()

    envelope = {
        "kind": "groth16_bn254",
        "vk_format": "snarkjs",
        "vk": vk,
        "proof": proof,
        "public_inputs": public_inputs,
        "meta": {"circuit_id": "embedding_threshold_groth16_bn254@test"},
    }

    res = zk_verify(envelope, meter_only=True)
    assert isinstance(res, dict)
    # Meter-only still returns a units count but does not perform crypto checks.
    units = res.get("units")
    assert isinstance(units, int) and units > 0
