"""
RPC method stubs for Quantum Jobs and Workers

Provides read-only methods for explorer/frontend to list and fetch jobs/results.
These methods return placeholders and should be wired to an indexer that listens to events.
"""

from __future__ import annotations

from typing import Any, Dict, List
import os
import time

from rpc.methods import method

# Simple in-memory placeholders (indexer should populate this from events)
_JOBS: Dict[str, Dict[str, Any]] = {}
_RESULTS: Dict[str, List[Dict[str, Any]]] = {}


def explorer_list_quantum_jobs(
    limit: int = 50, offset: int = 0
) -> List[Dict[str, Any]]:
    """List recent quantum jobs (placeholder)"""
    jobs = list(_JOBS.values())
    return jobs[offset : offset + limit]


def explorer_get_quantum_job(job_id: str) -> Dict[str, Any]:
    """Get job details"""
    return _JOBS.get(job_id, {})


def explorer_list_job_results(job_id: str) -> List[Dict[str, Any]]:
    """List all results submitted for a job"""
    return _RESULTS.get(job_id, [])


def explorer_get_result(job_id: str, worker_id: str) -> Dict[str, Any]:
    """Get a specific result submission"""
    for r in _RESULTS.get(job_id, []):
        if r.get("worker_id") == worker_id:
            return r
    return {}


# Registration helpers for the placeholder indexer


def _index_job(job: Dict[str, Any]):
    _JOBS[job["job_id"]] = job


def _index_result(job_id: str, result: Dict[str, Any]):
    _RESULTS.setdefault(job_id, []).append(result)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@method("quantum.status", aliases=("quantum_status", "quantum.getStatus", "quantum_getStatus", "quantum.workerStatus"), desc="Get quantum worker status")
def quantum_status(params=None, *_args, **_kwargs) -> Dict[str, Any]:
    if isinstance(params, dict) and "params" in params:
        params = params.get("params")

    supported = _bool_env("ANIMICA_QUANTUM_SUPPORTED", True)
    worker_enabled = _bool_env("ANIMICA_MINER_QUANTUM_WORKER", False)
    cpu = _bool_env("ANIMICA_QUANTUM_CAP_CPU", True)
    gpu = _bool_env("ANIMICA_QUANTUM_CAP_GPU", False)
    qpu = _bool_env("ANIMICA_QUANTUM_CAP_QPU", False)
    accepted = [x.strip() for x in os.getenv("ANIMICA_QUANTUM_ACCEPTED_JOB_TYPES", "").split(",") if x.strip()]

    if not supported:
        return {
            "enabled": False,
            "ok": False,
            "reason": "not_supported",
            "message": "Quantum compute is not supported in this node build.",
            "details": {
                "worker_enabled": False,
                "accepted_job_types": accepted,
                "capabilities": {"cpu": cpu, "gpu": gpu, "qpu": qpu},
                "last_heartbeat": None,
            },
        }

    if not worker_enabled:
        return {
            "enabled": False,
            "ok": False,
            "reason": "disabled",
            "message": "Quantum compute is disabled. Enable via ANIMICA_MINER_QUANTUM_WORKER=1.",
            "details": {
                "worker_enabled": False,
                "accepted_job_types": accepted,
                "capabilities": {"cpu": cpu, "gpu": gpu, "qpu": qpu},
                "last_heartbeat": None,
            },
        }

    backend_available = _bool_env("ANIMICA_QUANTUM_BACKEND_AVAILABLE", True)
    if not backend_available:
        return {
            "enabled": True,
            "ok": False,
            "reason": "service_unavailable",
            "message": "Quantum worker is enabled but backend is unavailable.",
            "details": {
                "worker_enabled": True,
                "accepted_job_types": accepted,
                "capabilities": {"cpu": cpu, "gpu": gpu, "qpu": qpu},
                "last_heartbeat": None,
            },
        }

    return {
        "enabled": True,
        "ok": True,
        "reason": None,
        "message": None,
        "details": {
            "worker_enabled": True,
            "accepted_job_types": accepted,
            "capabilities": {"cpu": cpu, "gpu": gpu, "qpu": qpu},
            "last_heartbeat": int(time.time()),
        },
    }


# Example exports for dispatcher registration (depends on rpc framework)
_METHODS = {
    "explorer_list_quantum_jobs": explorer_list_quantum_jobs,
    "explorer_get_quantum_job": explorer_get_quantum_job,
    "explorer_list_job_results": explorer_list_job_results,
    "explorer_get_result": explorer_get_result,
}


# ---------------------------------------------------------------------------
# Quantum Useful Work (QUW) — hardware-QRNG attested entropy contribution lane.
# Backed by randomness.qrng.service.QuantumWorkService (verifies attestation +
# SP 800-90B health, credits ProofType.QUANTUM, selects beacon entropy).
# ---------------------------------------------------------------------------


def _quw_service():
    from randomness.qrng.service import get_service
    return get_service()


@method(
    "rand.getQuantumChallenge",
    desc="Issue a fresh per-round nonce for a QUW entropy contribution.",
    aliases=("quantum.quw.getChallenge",),
)
def quantum_get_challenge(round_id: int = 0) -> Dict[str, Any]:
    return _quw_service().get_challenge(int(round_id))


@method(
    "rand.contributeQuantumEntropy",
    desc="Submit an attested quantum-entropy contribution for a round (verified + credited).",
    aliases=("quantum.quw.contributeEntropy",),
)
def quantum_contribute_entropy(contribution: Dict[str, Any] = None, **kwargs) -> Dict[str, Any]:
    payload = contribution if isinstance(contribution, dict) else dict(kwargs)
    return _quw_service().contribute(payload)


@method(
    "rand.getQuantumCredits",
    desc="Quantum useful-work credit units earned by an address.",
    aliases=("quantum.quw.getCredits",),
)
def quantum_get_credits(address: str = "") -> Dict[str, Any]:
    return _quw_service().get_credits(str(address))


@method(
    "rand.getQuantumStatus",
    desc="QUW lane status: contributors, attested share, detected local sources.",
    aliases=("quantum.quw.getStatus",),
)
def quantum_get_status() -> Dict[str, Any]:
    st = _quw_service().status()
    try:
        from randomness.qrng import providers as _p
        st["local_sources"] = [s.info().as_dict() for s in _p.detect_sources()]
    except Exception:
        st["local_sources"] = []
    return st
