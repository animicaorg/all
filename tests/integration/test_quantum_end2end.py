"""Integration-style tests for the quantum RPC/indexer flow.

These tests operate on the in-repo in-memory indexer in `rpc.methods.quantum` and the
mock PQ signer. They do not execute VM contracts; they validate the explorer RPC
shapes and indexing pipeline.
"""

from __future__ import annotations

import json

import pytest

import rpc.methods.quantum as qmod
from tools.quantum import mock_pq


def test_index_job_and_result_cycle(tmp_path):
    # Create a fake job
    job = {
        "job_id": "job123",
        "owner": "addr_owner",
        "program_id": "QAOA_MAXCUT_V1",
        "input_commitment": "commitment1",
        "backend_type": "simulator",
        "shots": 100,
        "payment_max": "1000000000000000000",
        "deadline_block": 999999,
    }

    # Index job
    qmod._index_job(job)

    jobs = qmod.explorer_list_quantum_jobs()
    assert any(j.get("job_id") == "job123" for j in jobs)

    # Create a mock worker key and sign a simple canonical payload
    sk = mock_pq.gen_key()
    from tools.quantum.canonical import canonical_bytes

    payload = canonical_bytes(
        {"job_id": job["job_id"], "result_summary": {"best": "0101"}}
    )
    sig = mock_pq.sign(payload, sk)

    result = {
        "job_id": job["job_id"],
        "worker_id": "worker1",
        "result_data": {"best": "0101"},
        "result_commitment": "res_commit_1",
        "metadata": {"device": "sim_mock"},
        "worker_signature": sig,
    }

    # Index result
    qmod._index_result(job["job_id"], result)

    results = qmod.explorer_list_job_results(job["job_id"])
    assert len(results) == 1
    r = results[0]
    assert r["worker_id"] == "worker1"
    assert r["result_data"]["best"] == "0101"

    # Verify signature with the same sk (mock verification)
    ok = mock_pq.verify(
        canonical_bytes({"job_id": job["job_id"], "result_summary": {"best": "0101"}}),
        sk,
        r["worker_signature"],
    )
    assert ok


if __name__ == "__main__":
    pytest.main([__file__])
