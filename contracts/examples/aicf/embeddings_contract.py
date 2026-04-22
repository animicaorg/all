from __future__ import annotations

from stdlib import abi, events, storage

K_OWNER = b"example:embed:owner"


def init(owner: bytes) -> None:
    abi.require(storage.get(K_OWNER) in (None, b""), b"already_initialized")
    storage.set(K_OWNER, bytes(owner))


def request_embeddings(actor: bytes, job_id: bytes, corpus_hash: bytes, budget_anm_nanos: int) -> None:
    abi.require(storage.get(K_OWNER) == bytes(actor), b"not_owner")
    abi.require(int(budget_anm_nanos) > 0, b"bad_budget")

    events.emit(
        b"AICFModelCallRequested",
        {
            "job_id": bytes(job_id),
            "model_id": b"aicf-embed-1",
            "job_type": b"embedding",
            "input_ref_hash": bytes(corpus_hash),
            "output_schema": b"schema://embeddings_artifact",
            "max_budget_anm_nanos": int(budget_anm_nanos),
            "verification_mode": b"QUORUM_MATCH",
            "replication": 3,
            "quorum": 2,
        },
    )
