import json
import os
from pathlib import Path

import pytest

from zk.integration.omni_hooks import zk_verify
from zk.tests import configure_test_logging, fixture_path

configure_test_logging()

"""
Test: STARK (FRI) verification for a toy Merkle-membership AIR.

Looks for fixtures in either:
  - $ZK_STARK_MERKLE_DIR (env override), or
  - zk/tests/fixtures/stark_merkle/

Expected files:
  - proof.json  (structured FRI proof; see zk/docs/FORMATS.md)
  - vk.json     (optional; minimal VK will be synthesized from proof if absent)
  - public.json (optional if proof.json already includes "public_inputs")

The test constructs a canonical ProofEnvelope and asserts verify == True.
"""


def _fixture_dir() -> Path:
    env = os.getenv("ZK_STARK_MERKLE_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # default under repo
    return fixture_path("stark_merkle")


def _load_proof_vk() -> tuple[dict, dict, list[str]]:
    base = _fixture_dir()
    proof_p = base / "proof.json"
    if not proof_p.exists():
        pytest.skip(
            f"Missing fixtures: {proof_p}. "
            "Provide stark_merkle/proof.json or set $ZK_STARK_MERKLE_DIR."
        )

    with proof_p.open("r", encoding="utf-8") as fh:
        proof = json.load(fh)

    # Try to load an explicit VK, else build a minimal one from fri_params.
    vk_p = base / "vk.json"
    if vk_p.exists():
        with vk_p.open("r", encoding="utf-8") as fh:
            vk = json.load(fh)
    else:
        fri = proof.get("fri_params", {}) if isinstance(proof, dict) else {}
        # Derive sane defaults from proof if possible.
        log_n = fri.get("log_n") or (
            int(fri["n"]).bit_length() - 1
            if isinstance(fri.get("n"), int) and fri["n"] > 0
            else 16
        )
        num_q = fri.get("num_rounds") or len(proof.get("queries", [])) or 1
        vk = {
            "air": "merkle_membership_v1",
            "field": "bn254_fr",
            "hash": fri.get("hash", "keccak"),
            "domain_log2": int(log_n),
            "num_queries": int(num_q),
        }

    # Public inputs can live in the proof or a separate file.
    public_inputs: list[str] = []
    if isinstance(proof, dict) and "public_inputs" in proof:
        public_inputs = proof["public_inputs"]
    else:
        pub_p = base / "public.json"
        if pub_p.exists():
            with pub_p.open("r", encoding="utf-8") as fh:
                public_inputs = json.load(fh)
        else:
            pytest.skip(
                "No public inputs found: proof.json lacks 'public_inputs' and no public.json present."
            )
    if not isinstance(public_inputs, list):
        raise TypeError(
            "public inputs must be a list of field elements (hex or decimal strings)."
        )

    return proof, vk, public_inputs


@pytest.mark.slow
def test_stark_merkle_verify_ok():
    proof, vk, public_inputs = _load_proof_vk()

    envelope = {
        "kind": "stark_fri_merkle",
        "vk_format": "fri",
        # For this toy demo we embed a minimal VK if a file isn't present.
        "vk": vk,
        "proof": proof,
        "public_inputs": public_inputs,
        "meta": {"circuit_id": "merkle_membership_stark_demo@test"},
    }

    res = zk_verify(envelope)
    assert isinstance(res, dict), "zk_verify should return a dict-like result"
    assert res.get("ok") is True, f"Verification failed: {res}"
    units = res.get("units")
    assert isinstance(units, int) and units > 0, f"Unexpected metering units: {units}"


@pytest.mark.slow
def test_stark_merkle_meter_only():
    proof, vk, public_inputs = _load_proof_vk()

    envelope = {
        "kind": "stark_fri_merkle",
        "vk_format": "fri",
        "vk": vk,
        "proof": proof,
        "public_inputs": public_inputs,
        "meta": {"circuit_id": "merkle_membership_stark_demo@test"},
    }

    res = zk_verify(envelope, meter_only=True)
    assert isinstance(res, dict)
    # Meter-only still returns a units count but does not perform crypto checks.
    units = res.get("units")
    assert isinstance(units, int) and units > 0
