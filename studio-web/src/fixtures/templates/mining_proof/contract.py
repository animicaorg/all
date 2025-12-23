"""
Mining proof script template.

Required entrypoints:
- derive_challenge(inputs)
- verify_proof(inputs)
- commit_outputs(outputs)
"""
from stdlib import hash


def derive_challenge(inputs):
    seed = inputs.get("seed", b"")
    return {"challenge": hash.sha3_256(seed)}


def verify_proof(inputs):
    return bool(inputs.get("proof_ok", False))


def commit_outputs(outputs):
    return outputs
