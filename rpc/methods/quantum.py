"""
RPC method stubs for Quantum Jobs and Workers

Provides read-only methods for explorer/frontend to list and fetch jobs/results.
These methods return placeholders and should be wired to an indexer that listens to events.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any

# Simple in-memory placeholders (indexer should populate this from events)
_JOBS: Dict[str, Dict[str, Any]] = {}
_RESULTS: Dict[str, List[Dict[str, Any]]] = {}


def explorer_list_quantum_jobs(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """List recent quantum jobs (placeholder)"""
    jobs = list(_JOBS.values())
    return jobs[offset:offset+limit]


def explorer_get_quantum_job(job_id: str) -> Dict[str, Any]:
    """Get job details"""
    return _JOBS.get(job_id, {})


def explorer_list_job_results(job_id: str) -> List[Dict[str, Any]]:
    """List all results submitted for a job"""
    return _RESULTS.get(job_id, [])


def explorer_get_result(job_id: str, worker_id: str) -> Dict[str, Any]:
    """Get a specific result submission"""
    for r in _RESULTS.get(job_id, []):
        if r.get('worker_id') == worker_id:
            return r
    return {}

# Registration helpers for the placeholder indexer

def _index_job(job: Dict[str, Any]):
    _JOBS[job['job_id']] = job


def _index_result(job_id: str, result: Dict[str, Any]):
    _RESULTS.setdefault(job_id, []).append(result)

# Example exports for dispatcher registration (depends on rpc framework)
_METHODS = {
    'explorer_list_quantum_jobs': explorer_list_quantum_jobs,
    'explorer_get_quantum_job': explorer_get_quantum_job,
    'explorer_list_job_results': explorer_list_job_results,
    'explorer_get_result': explorer_get_result,
}
