import copy
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("py_ecc")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from zk.verifiers.groth16_bn254 import verify_groth16


def _load_groth16_fixture():
    base = Path("zk/circuits/groth16/embedding")
    proof_path = base / "proof.json"
    vk_path = base / "vk.json"
    public_path = base / "public.json"

    if not proof_path.exists() or not vk_path.exists() or not public_path.exists():
        pytest.skip(
            "Groth16 embedding fixtures are missing in zk/circuits/groth16/embedding"
        )

    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    vk = json.loads(vk_path.read_text(encoding="utf-8"))
    public_inputs = json.loads(public_path.read_text(encoding="utf-8"))
    return proof, vk, public_inputs


def test_groth16_vector_accepts_valid_proof():
    proof, vk, public_inputs = _load_groth16_fixture()

    assert verify_groth16(vk, proof, public_inputs) is True


def test_groth16_vector_rejects_invalid_proof():
    proof, vk, public_inputs = _load_groth16_fixture()
    corrupted = copy.deepcopy(proof)
    if isinstance(corrupted, dict) and isinstance(corrupted.get("proof"), dict):
        inner = corrupted["proof"]
        if isinstance(inner.get("pi_a"), list) and inner["pi_a"]:
            inner["pi_a"][0] = "0"

    assert verify_groth16(vk, corrupted, public_inputs) is False
