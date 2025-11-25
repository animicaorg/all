import json
import os
from pathlib import Path

import pytest

from zk.tests import fixture_path, configure_test_logging
from zk.integration.omni_hooks import zk_verify

configure_test_logging()

"""
Test: PLONK+KZG (BN254) verification for a small Poseidon-based demo circuit.

Looks for fixtures in either:
  - $ZK_PLONK_POSEIDON_DIR (env override), or
  - zk/tests/fixtures/plonk_poseidon/

Expected files:
  - proof.json  (PlonkJS-style proof; ideally includes "publicSignals")
  - vk.json     (PlonkJS-style verifying key)

If proof.json does not include "publicSignals", the test will read public.json
from the same directory as the ordered public inputs array.

The test constructs a canonical ProofEnvelope and asserts verify == True.
"""


def _fixture_dir() -> Path:
    env = os.getenv("ZK_PLONK_POSEIDON_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # default under repo
    return fixture_path("plonk_poseidon")


def _load_proof_vk() -> tuple[dict, dict, list[str]]:
    base = _fixture_dir()
    proof_p = base / "proof.json"
    vk_p = base / "vk.json"

    if not proof_p.exists() or not vk_p.exists():
        pytest.skip(
            f"Missing fixtures: {proof_p if not proof_p.exists() else ''} "
            f"{vk_p if not vk_p.exists() else ''}. "
            "Provide plonk_poseidon/proof.json and vk.json or set $ZK_PLONK_POSEIDON_DIR."
        )

    with proof_p.open("r", encoding="utf-8") as fh:
        proof = json.load(fh)
    with vk_p.open("r", encoding="utf-8") as fh:
        vk = json.load(fh)

    # Prefer embedded publicSignals if present; else try separate file.
    public_inputs: list[str] = []
    if isinstance(proof, dict) and "publicSignals" in proof:
        public_inputs = proof["publicSignals"]
    else:
        pub_p = base / "public.json"
        if pub_p.exists():
            with pub_p.open("r", encoding="utf-8") as fh:
                public_inputs = json.load(fh)
        else:
            pytest.skip(
                "No public inputs found: proof.json lacks 'publicSignals' and no public.json present."
            )
    if not isinstance(public_inputs, list):
        raise TypeError("publicSignals/public.json must be a list of field elements (hex or decimal strings).")

    return proof, vk, public_inputs


@pytest.mark.slow
def test_plonk_poseidon_verify_ok():
    proof, vk, public_inputs = _load_proof_vk()

    envelope = {
        "kind": "plonk_kzg_bn254",
        "vk_format": "plonkjs",
        # In production we'd prefer vk_ref pinned in the registry; embed VK here for the test.
        "vk": vk,
        "proof": proof,
        "public_inputs": public_inputs,
        "meta": {"circuit_id": "poseidon_demo_plonk_kzg_bn254@test"},
    }

    res = zk_verify(envelope)
    assert isinstance(res, dict), "zk_verify should return a dict-like result"
    assert res.get("ok") is True, f"Verification failed: {res}"
    units = res.get("units")
    assert isinstance(units, int) and units > 0, f"Unexpected metering units: {units}"


@pytest.mark.slow
def test_plonk_poseidon_meter_only():
    proof, vk, public_inputs = _load_proof_vk()

    envelope = {
        "kind": "plonk_kzg_bn254",
        "vk_format": "plonkjs",
        "vk": vk,
        "proof": proof,
        "public_inputs": public_inputs,
        "meta": {"circuit_id": "poseidon_demo_plonk_kzg_bn254@test"},
    }

    res = zk_verify(envelope, meter_only=True)
    assert isinstance(res, dict)
    # Meter-only still returns a units count but does not perform crypto checks.
    units = res.get("units")
    assert isinstance(units, int) and units > 0
