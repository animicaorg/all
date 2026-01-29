from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from proofs.errors import ProofError
from proofs.metrics import ProofMetrics
from proofs.storage import verify_storage_heartbeat

VECTORS = Path(__file__).resolve().parents[1] / "test_vectors" / "storage.json"


def _load_vectors() -> list[dict[str, Any]]:
    assert VECTORS.exists(), f"missing storage vectors: {VECTORS}"
    with VECTORS.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Accept either {"vectors":[...]} or a bare list
    return data["vectors"] if isinstance(data, dict) and "vectors" in data else data


def _as_metrics(x: Any) -> ProofMetrics:
    assert isinstance(
        x, ProofMetrics
    ), f"verify_storage_heartbeat must return ProofMetrics, got {type(x)}"
    return x


# ----------------------------- Vector-driven acceptance -----------------------------


@pytest.mark.parametrize("vec", _load_vectors())
def test_storage_vectors_accept_and_bonus(vec: Dict[str, Any]):
    """
    Cross-check storage heartbeat vectors:
      - heartbeats inside the window are accepted
      - those outside or malformed are rejected (raise ProofError)
      - retrieval tickets, when present, flip the 'retrieval_bonus' flag
    """
    hb = vec["heartbeat"]
    now = vec.get("now")
    expect = vec.get("expect", {})

    if expect.get("ok", True):
        m = _as_metrics(verify_storage_heartbeat(hb, now=now))
        # Basic sanity on QoS scale
        assert 0.0 <= (m.qos or 0.0) <= 2.0
        # Retrieval bonus when declared by the vector
        if "retrieval_bonus" in expect:
            assert bool(m.retrieval_bonus) == bool(expect["retrieval_bonus"])
    else:
        with pytest.raises(ProofError):
            verify_storage_heartbeat(hb, now=now)


# -------------------------- Window boundary behavior --------------------------


def test_storage_window_boundaries_min_mid_max():
    """
    Construct a minimal heartbeat and exercise timing boundaries:
    - exactly at window_start is accepted
    - strictly before window_start is rejected
    - exactly at window_end is rejected (half-open window [start, end))
    - midpoint is accepted
    """
    # A tiny, schema-conforming heartbeat body; fields not used by timing checks
    base = {
        "provider_id": "provider:test",
        "namespace": 24,
        "commitment": "0x" + "ab" * 32,
        "window_start": 1_000,
        "window_end": 2_000,
        # optional fields tolerated by verifier (nonce/sig/etc.) may be absent in tests
    }

    # start → OK
    m1 = _as_metrics(verify_storage_heartbeat(base, now=base["window_start"]))
    assert (m1.qos or 0.0) >= 1.0

    # before start → error
    with pytest.raises(ProofError):
        verify_storage_heartbeat(base, now=base["window_start"] - 1)

    # end boundary is exclusive → error
    with pytest.raises(ProofError):
        verify_storage_heartbeat(base, now=base["window_end"])

    # midpoint → OK
    mid = (base["window_start"] + base["window_end"]) // 2
    m2 = _as_metrics(verify_storage_heartbeat(base, now=mid))
    assert (m2.qos or 0.0) >= 1.0


# -------------------------- Retrieval bonus effect on QoS --------------------------


def test_retrieval_bonus_increases_qos_monotonically():
    """
    If a retrieval ticket is present and valid, the metrics should reflect a strictly higher QoS.
    This does not assume a specific increment, only monotonicity.
    """
    hb = {
        "provider_id": "provider:test",
        "namespace": 42,
        "commitment": "0x" + "cd" * 32,
        "window_start": 10_000,
        "window_end": 11_000,
    }
    mid = (hb["window_start"] + hb["window_end"]) // 2

    # Baseline without retrieval
    m_base = _as_metrics(verify_storage_heartbeat(hb, now=mid))
    q_base = float(m_base.qos or 0.0)

    # With a retrieval ticket (shape is intentionally generic; the verifier
    # should validate content if it understands the field).
    hb_rt = dict(hb)
    hb_rt["retrieval_ticket"] = {
        "request_id": "deadbeef",
        "ticket_hash": "0x" + "ef" * 32,
        "served_ts": mid,
    }
    m_bonus = _as_metrics(verify_storage_heartbeat(hb_rt, now=mid))
    q_bonus = float(m_bonus.qos or 0.0)

    assert getattr(m_bonus, "retrieval_bonus", False) is True
    assert q_bonus > q_base


# -------------------------- Negative/malformed cases --------------------------


def test_negative_bad_namespace_and_commitment_lengths():
    """
    Bad namespaces or malformed commitments should be rejected early with ProofError.
    """
    hb = {
        "provider_id": "provider:test",
        "namespace": -1,  # invalid
        "commitment": "0x1234",  # too short to be a 32-byte digest
        "window_start": 100,
        "window_end": 200,
    }
    with pytest.raises(ProofError):
        verify_storage_heartbeat(hb, now=150)
