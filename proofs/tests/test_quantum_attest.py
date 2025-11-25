from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# Under test
from proofs.quantum_attest.provider_cert import (
    ProviderCert,
    parse_provider_cert,
    compute_provider_id,
)
from proofs.quantum_attest.traps import (
    trap_ratio,
    wilson_lower_bound,
    meets_threshold,
)
from proofs.quantum_attest.benchmarks import units_from_circuit


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load_fixture_json(name: str) -> dict[str, Any]:
    p = FIXTURES / name
    assert p.exists(), f"missing fixture: {p}"
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


# -------------------- Provider certificate --------------------

def test_provider_cert_parse_and_shape():
    """
    Parses the sample QPU provider certificate and validates basic shape/fields.
    """
    raw = _load_fixture_json("qpu_provider_cert.json")
    cert = parse_provider_cert(raw)
    assert isinstance(cert, ProviderCert)

    # Name & domains
    assert cert.name and isinstance(cert.name, str)
    assert isinstance(cert.domains, list) and all(isinstance(d, str) for d in cert.domains)
    # Algorithm for identity key and key material presence
    assert cert.algo in {"ed25519", "dilithium3", "sphincs-shake-128s"}
    assert isinstance(cert.pubkey_bytes, (bytes, bytearray)) and len(cert.pubkey_bytes) >= 16
    # Validity window is sane
    assert cert.not_before < cert.not_after

    # Root details present (may be a friendly name + fingerprint string in the fixture)
    assert isinstance(cert.root_name, str) and cert.root_name
    assert isinstance(cert.root_fingerprint, str) and len(cert.root_fingerprint) >= 8  # e.g., "SHA256:…"


def test_provider_cert_provider_id_stability():
    """
    Provider ID should be stable and derived from canonical fields (prefix 'qpu:' recommended).
    """
    raw = _load_fixture_json("qpu_provider_cert.json")
    cert = parse_provider_cert(raw)
    pid = compute_provider_id(cert)
    assert isinstance(pid, str) and len(pid) > 8
    # If the fixture embeds a provider_id it must match the computed one.
    fixture_pid = raw.get("provider_id")
    if fixture_pid:
        assert fixture_pid == pid
    # Friendly convention check
    assert pid.startswith("qpu:")


# -------------------- Trap-circuit verification math --------------------

@pytest.mark.parametrize(
    ("passes", "total", "thr", "expect"),
    [
        (980, 1000, 0.95, True),   # clearly good
        (950, 1000, 0.95, True),   # good
        (920, 1000, 0.95, False),  # not enough
        (880, 1000, 0.95, False),  # clearly bad
        (96, 100, 0.90, True),     # small sample, good
    ],
)
def test_trap_threshold_decision(passes: int, total: int, thr: float, expect: bool):
    """
    The decision boundary should behave sensibly at common thresholds using
    Wilson lower confidence bounds internally.
    """
    # Basic sanity of ratio ∈ [0,1]
    r = trap_ratio(passes, total)
    assert 0.0 <= r <= 1.0

    # LCB monotone and within [0,1]
    lcb = wilson_lower_bound(passes, total)
    assert 0.0 <= lcb <= 1.0

    # Threshold decision
    assert meets_threshold(passes, total, threshold=thr) is expect


def test_trap_lcb_increases_with_sample_size_for_same_ratio():
    """
    With the same observed ratio, the Wilson LCB should increase with more samples.
    """
    r1_p, r1_t = 90, 100
    r2_p, r2_t = 900, 1000
    assert abs(r1_p / r1_t - r2_p / r2_t) < 1e-12
    l1 = wilson_lower_bound(r1_p, r1_t)
    l2 = wilson_lower_bound(r2_p, r2_t)
    assert l2 > l1, (l1, l2)


# -------------------- Benchmark scaling (depth×width×shots → units) --------------------

def test_units_from_circuit_monotonicity_and_reasonable_scale():
    """
    Reference scaling should increase with depth, width, and shots, and be roughly linear in shots.
    """
    u1 = units_from_circuit(depth=10, width=8, shots=100)
    u2 = units_from_circuit(depth=20, width=8, shots=100)   # deeper
    u3 = units_from_circuit(depth=10, width=16, shots=100)  # wider
    u4 = units_from_circuit(depth=10, width=8, shots=1000)  # more shots

    for u in (u1, u2, u3, u4):
        assert u > 0.0

    assert u2 > u1
    assert u3 > u1
    assert u4 > u1
    # Rough proportionality in shots (allow 5% tolerance)
    assert 0.95 <= (u4 / u1) / 10.0 <= 1.05
