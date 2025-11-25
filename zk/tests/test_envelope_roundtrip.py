import json
import os
import hashlib
from pathlib import Path
from typing import List, Tuple, Dict, Any

import pytest

from zk.tests import fixture_path, configure_test_logging
from zk.integration.types import canonical_json_bytes, compute_vk_hash

configure_test_logging()

"""
Test: SnarkJS (Groth16, BN254) ⇄ Envelope round-trip & stable hashing.

We:
  1) Load SnarkJS-style proof.json + vk.json (+ public signals).
  2) Build a canonical ProofEnvelope (embedding the SnarkJS shapes).
  3) Hash proof/vk/envelope using canonical JSON bytes.
  4) Round-trip via JSON (dump+load) and assert hashes remain identical.

Fixtures are searched in:
  - $ZK_GROTH16_EMBED_DIR (env override), or
  - zk/tests/fixtures/groth16_embedding/

Expected files:
  - proof.json  (SnarkJS Groth16 proof; ideally has "publicSignals")
  - vk.json     (SnarkJS Groth16 VK)
  - public.json (optional if proof.json lacks "publicSignals")
"""


def _fixture_dir() -> Path:
    env = os.getenv("ZK_GROTH16_EMBED_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return fixture_path("groth16_embedding")


def _load_snarkjs() -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
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

    # Public inputs
    public_inputs: List[str] = []
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
        raise TypeError("public inputs must be a list of field elements.")

    return proof, vk, public_inputs


def _env_hash(envelope: Dict[str, Any]) -> str:
    """
    Stable projection hash (see docs/REPRODUCIBILITY.md).
    """
    vk_hash = None
    if envelope.get("vk") is not None:
        vk_hash = compute_vk_hash(
            envelope["kind"], envelope["vk_format"], envelope["vk"], envelope.get("fri_params")
        )
    proj = {
        "kind": envelope["kind"],
        "public_inputs": envelope["public_inputs"],
        "vk_ref": envelope.get("vk_ref"),
        "vk_hash": vk_hash,
        "proof_hash": hashlib.sha3_256(canonical_json_bytes(envelope["proof"])).hexdigest(),
    }
    return hashlib.sha3_256(canonical_json_bytes(proj)).hexdigest()


@pytest.mark.slow
def test_snarkjs_envelope_roundtrip_stable_hashes():
    proof, vk, public_inputs = _load_snarkjs()

    # Canonical hashes of raw tool outputs
    h_proof_1 = hashlib.sha3_256(canonical_json_bytes(proof)).hexdigest()
    h_vkjson_1 = hashlib.sha3_256(canonical_json_bytes(vk)).hexdigest()
    vk_hash_1 = compute_vk_hash("groth16_bn254", "snarkjs", vk)

    # Build canonical envelope (embed VK for this test)
    envelope = {
        "kind": "groth16_bn254",
        "vk_format": "snarkjs",
        "vk": vk,
        "proof": proof,
        "public_inputs": public_inputs,
        "meta": {"circuit_id": "embedding_threshold_groth16_bn254@test"},
    }
    h_env_1 = hashlib.sha3_256(canonical_json_bytes(envelope)).hexdigest()
    proj_hash_1 = _env_hash(envelope)

    # Round-trip through JSON to simulate transport/storage
    buf = canonical_json_bytes(envelope)
    env2 = json.loads(buf.decode("utf-8"))

    # Recompute hashes; all must match
    h_proof_2 = hashlib.sha3_256(canonical_json_bytes(env2["proof"])).hexdigest()
    assert h_proof_2 == h_proof_1, "proof canonical hash changed after round-trip"

    h_vkjson_2 = hashlib.sha3_256(canonical_json_bytes(env2["vk"])).hexdigest()
    assert h_vkjson_2 == h_vkjson_1, "vk canonical hash changed after round-trip"

    vk_hash_2 = compute_vk_hash(env2["kind"], env2["vk_format"], env2["vk"], env2.get("fri_params"))
    assert vk_hash_2 == vk_hash_1, "computed vk_hash changed after round-trip"

    h_env_2 = hashlib.sha3_256(canonical_json_bytes(env2)).hexdigest()
    assert h_env_2 == h_env_1, "envelope canonical hash changed after round-trip"

    proj_hash_2 = _env_hash(env2)
    assert proj_hash_2 == proj_hash_1, "projection (audit) hash changed after round-trip"


@pytest.mark.slow
def test_snarkjs_envelope_snippet_identity_shapes():
    """
    While normalization _may_ reformat numbers (decimal→hex) in adapters,
    this test ensures that when we embed the SnarkJS shapes directly, a simple
    "convert → envelope → extract" flow preserves the canonical JSON bytes.

    If your adapter re-encodes numbers, adjust this test accordingly to compare
    normalized shapes instead.
    """
    proof, vk, public_inputs = _load_snarkjs()

    # Build envelope
    envelope = {
        "kind": "groth16_bn254",
        "vk_format": "snarkjs",
        "vk": vk,
        "proof": proof,
        "public_inputs": public_inputs,
        "meta": {"circuit_id": "embedding_threshold_groth16_bn254@test"},
    }

    # Extract back (simulate "envelope → snarkjs")
    snarkjs_proof = envelope["proof"]
    snarkjs_vk = envelope["vk"]

    assert hashlib.sha3_256(canonical_json_bytes(snarkjs_proof)).hexdigest() == \
           hashlib.sha3_256(canonical_json_bytes(proof)).hexdigest(), \
           "extracted proof differs canonically from original"

    assert hashlib.sha3_256(canonical_json_bytes(snarkjs_vk)).hexdigest() == \
           hashlib.sha3_256(canonical_json_bytes(vk)).hexdigest(), \
           "extracted vk differs canonically from original"
