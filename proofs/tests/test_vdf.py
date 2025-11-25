from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from proofs.errors import ProofError
from proofs.metrics import ProofMetrics
from proofs.vdf import verify_vdf, estimate_seconds


VECTORS = Path(__file__).resolve().parents[1] / "test_vectors" / "vdf.json"


def _load_vectors() -> List[Dict[str, Any]]:
    assert VECTORS.exists(), f"missing VDF vectors: {VECTORS}"
    with VECTORS.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Accept either {"vectors":[...]} or a bare list
    return data["vectors"] if isinstance(data, dict) and "vectors" in data else data


def _as_metrics(x: Any) -> ProofMetrics:
    assert isinstance(x, ProofMetrics), f"verify_vdf must return ProofMetrics, got {type(x)}"
    return x


# ----------------------------- Vector-driven correctness -----------------------------

@pytest.mark.parametrize("vec", _load_vectors())
def test_vdf_vectors(vec: Dict[str, Any]):
    """
    Each vector contains:
      {
        "name": "...",
        "proof": { "N": "...", "x": "...", "y": "...", "pi": "...", "iterations": int, ... },
        "expect": { "ok": bool, "min_seconds": float? }
      }
    For ok==True we require verify_vdf to succeed and return sane metrics.
    For ok==False we require it to raise ProofError.
    """
    proof: Dict[str, Any] = vec["proof"]
    expect: Dict[str, Any] = vec.get("expect", {})
    if expect.get("ok", True):
        m = _as_metrics(verify_vdf(proof))
        # seconds-equivalent should be finite and non-negative
        assert m.vdf_seconds is not None and m.vdf_seconds >= 0.0
        # optional floor bound, if vector provides one
        if "min_seconds" in expect:
            assert m.vdf_seconds >= float(expect["min_seconds"]) - 1e-9
    else:
        with pytest.raises(ProofError):
            verify_vdf(proof)


# ----------------------------- Tamper detection --------------------------------------

def test_vdf_tamper_changes_rejected():
    """
    Take a known-good vector and flip a bit in y; verifier must reject.
    """
    ok_vecs = [v for v in _load_vectors() if v.get("expect", {}).get("ok", True)]
    if not ok_vecs:
        pytest.skip("no positive VDF vectors available")
    proof = dict(ok_vecs[0]["proof"])
    y = proof.get("y")
    if isinstance(y, str) and y.startswith("0x") and len(y) > 4:
        # flip the last nibble
        bad_last = "0" if y[-1].lower() != "0" else "1"
        proof["y"] = y[:-1] + bad_last
    else:
        # fallback: tweak iterations to desync relation
        proof["iterations"] = int(proof["iterations"]) + 1
    with pytest.raises(ProofError):
        verify_vdf(proof)


# ----------------------------- Estimation properties ---------------------------------

def test_estimate_seconds_monotonic_in_iterations():
    """
    estimate_seconds should be strictly increasing with iterations (for fixed modulus size).
    """
    bits = 1024
    s1 = estimate_seconds(modulus_bits=bits, iterations=10_000)
    s2 = estimate_seconds(modulus_bits=bits, iterations=20_000)
    s3 = estimate_seconds(modulus_bits=bits, iterations=40_000)
    assert s1 < s2 < s3
    # Doubling iterations should (approximately) double time within a tolerance band.
    ratio = s3 / s2
    assert 1.7 <= ratio <= 2.3


def test_estimate_seconds_monotonic_in_modulus_bits():
    """
    For the same iteration count, larger moduli should not estimate faster than smaller ones.
    """
    iters = 20_000
    small = estimate_seconds(modulus_bits=512, iterations=iters)
    large = estimate_seconds(modulus_bits=2048, iterations=iters)
    assert large >= small


# ----------------------------- Input validation --------------------------------------

def test_zero_or_negative_iterations_rejected():
    """
    A proof with zero/negative iterations must be rejected by the verifier.
    """
    ok_vecs = [v for v in _load_vectors() if v.get("expect", {}).get("ok", True)]
    if not ok_vecs:
        pytest.skip("no positive VDF vectors available")
    base = dict(ok_vecs[0]["proof"])

    for bad in (0, -1):
        bad_proof = dict(base)
        bad_proof["iterations"] = bad
        with pytest.raises(ProofError):
            verify_vdf(bad_proof)


def test_malformed_hex_inputs_rejected():
    """
    Non-hex or wrong-length N/x/y/pi should be rejected early.
    """
    ok_vecs = [v for v in _load_vectors() if v.get("expect", {}).get("ok", True)]
    if not ok_vecs:
        pytest.skip("no positive VDF vectors available")
    base = dict(ok_vecs[0]["proof"])

    # Corrupt N
    p1 = dict(base); p1["N"] = "not-hex"
    with pytest.raises(ProofError):
        verify_vdf(p1)

    # Truncated x
    p2 = dict(base); p2["x"] = "0x1234"
    with pytest.raises(ProofError):
        verify_vdf(p2)

    # Truncated pi
    p3 = dict(base); p3["pi"] = "0xdead"
    with pytest.raises(ProofError):
        verify_vdf(p3)
