"""
capabilities.jobs
=================

Typed interfaces and glue for the off-chain job lifecycle used by Animica's
capabilities system (AI / Quantum and related helpers). This package provides:

- **types**: `JobKind`, `JobRequest`, `JobReceipt`, `ResultRecord`
- **id**: `derive_task_id(...)` — deterministic task-id function
- **queue**: `JobQueue`, `QueueConfig`, `QueueItem` — durable FIFO with idempotency
- **receipts**: `build_receipt(...)` — domain-separated enqueue receipts
- **result_store**: `ResultStore` — persistent result KV
- **index**: `JobIndex` — secondary indexes (by caller, by height)
- **resolver**: `ResultResolver` — fold on-chain proofs -> results
- **attest_bridge**: `normalize_attestation_bundle(...)` — map evidence -> verifier inputs

The submodules are imported lazily/defensively so this package can be imported
early during bring-up without requiring every dependency to be present yet.
"""

from __future__ import annotations

from typing import Any, Tuple

# ---- Optional, defensive re-exports ------------------------------------------------

try:  # types, dataclasses
    from .types import JobKind, JobReceipt, JobRequest, ResultRecord
except Exception:  # pragma: no cover
    JobKind = JobRequest = JobReceipt = ResultRecord = None  # type: ignore[assignment]

try:  # deterministic id
    from .id import derive_task_id
except Exception:  # pragma: no cover

    def derive_task_id(*_a: Any, **_k: Any) -> bytes:  # type: ignore[override]
        raise NotImplementedError("capabilities.jobs.id is not available in this build")


try:  # queue
    from .queue import JobQueue, QueueConfig, QueueItem
except Exception:  # pragma: no cover
    JobQueue = QueueConfig = QueueItem = None  # type: ignore[assignment]

try:  # receipts
    from .receipts import build_receipt
except Exception:  # pragma: no cover

    def build_receipt(*_a: Any, **_k: Any) -> dict:  # type: ignore[override]
        raise NotImplementedError(
            "capabilities.jobs.receipts is not available in this build"
        )


try:  # results store
    from .result_store import ResultStore
except Exception:  # pragma: no cover
    ResultStore = None  # type: ignore[assignment]

try:  # secondary indexes
    from .index import JobIndex
except Exception:  # pragma: no cover
    JobIndex = None  # type: ignore[assignment]

try:  # resolver (proofs -> results)
    from .resolver import ResultResolver
except Exception:  # pragma: no cover
    ResultResolver = None  # type: ignore[assignment]

try:  # attestation normalization bridge
    from .attest_bridge import normalize_attestation_bundle
except Exception:  # pragma: no cover

    def normalize_attestation_bundle(*_a: Any, **_k: Any) -> dict:  # type: ignore[override]
        raise NotImplementedError(
            "capabilities.jobs.attest_bridge is not available in this build"
        )


# ---- Convenience bootstrap ----------------------------------------------------------


def bootstrap_sqlite(path: str = "capabilities_jobs.db") -> Tuple[Any, Any, Any]:
    """
    Open a minimal, co-located job stack backed by SQLite files.

    Returns:
        (queue, result_store, index)

    Notes:
        - This helper assumes each component exposes a classmethod `open_sqlite(path)`.
        - If a component is not available yet, a clear NotImplementedError is raised.
    """
    if JobQueue is None or ResultStore is None or JobIndex is None:
        missing = [
            name
            for name, ref in [
                ("JobQueue", JobQueue),
                ("ResultStore", ResultStore),
                ("JobIndex", JobIndex),
            ]
            if ref is None
        ]
        raise NotImplementedError(f"Missing components: {', '.join(missing)}")

    q = JobQueue.open_sqlite(path)  # type: ignore[attr-defined]
    rs = ResultStore.open_sqlite(path)  # type: ignore[attr-defined]
    ix = JobIndex.open_sqlite(path)  # type: ignore[attr-defined]
    return q, rs, ix


__all__ = [
    # types
    "JobKind",
    "JobRequest",
    "JobReceipt",
    "ResultRecord",
    # id
    "derive_task_id",
    # queue
    "JobQueue",
    "QueueConfig",
    "QueueItem",
    # receipts
    "build_receipt",
    # stores / indexes / resolver
    "ResultStore",
    "JobIndex",
    "ResultResolver",
    # attest bridge
    "normalize_attestation_bundle",
    # helpers
    "bootstrap_sqlite",
]
