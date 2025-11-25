from __future__ import annotations

"""
capabilities.jobs.receipts
--------------------------

Build and validate deterministic **job receipts** for the capabilities queue.

Design goals
- Domain-separated digest (SHA3-512) over a *canonical CBOR* view of the receipt.
- Stable across platforms: we rely on the project's canonical CBOR codec; if it's
  unavailable at import time, we gracefully fall back to a stable JSON encoding.
- Binds all critical fields used for the deterministic task-id derivation
  (chain_id, height, tx_hash, caller, payload).
- Small, dependency-light, and pure-Python.

Schema (conceptual, version 1)
{
  v:            1,                     # schema version
  task_id:      bytes(32),             # hex(sha3_256(...)) ← see jobs.id
  kind:         int,                   # JobKind numeric id
  chain_id:     int,
  height:       int,
  tx_hash:      bytes,                 # raw tx hash bytes
  caller:       bytes,                 # caller address bytes
  payload_hash: bytes(32),             # sha3_256(canonical(payload))
  created_at:   int,                   # epoch seconds
  digest:       bytes(64)              # sha3_512(DOMAIN || cbor(map-without-digest))
}

The digest covers all fields EXCEPT `digest` itself.
"""

from dataclasses import dataclass
from typing import Any, Dict, Tuple
import json
import time
import hashlib

from capabilities.errors import CapError
from capabilities.jobs.types import JobKind
from capabilities.jobs.id import derive_task_id_hex

# --- Canonical bytes encoder (CBOR-first, JSON fallback) ---------------------

try:
    from capabilities.cbor.codec import dumps as _CBOR_DUMPS, loads as _CBOR_LOADS  # type: ignore
except Exception:  # pragma: no cover - exercised indirectly
    _CBOR_DUMPS = None  # type: ignore
    _CBOR_LOADS = None  # type: ignore


def _canon_dumps(obj: Any) -> bytes:
    """Canonical, deterministic bytes for hashing/transport."""
    if _CBOR_DUMPS is not None:
        return _CBOR_DUMPS(obj)
    # Stable JSON fallback (sorted keys, no spaces, UTF-8)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _canon_loads(b: bytes) -> Any:
    if _CBOR_LOADS is not None:
        return _CBOR_LOADS(b)
    return json.loads(b.decode("utf-8"))


# --- Hash helpers ------------------------------------------------------------

def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def _sha3_512(data: bytes) -> bytes:
    return hashlib.sha3_512(data).digest()


# --- Constants ---------------------------------------------------------------

RECEIPT_VERSION = 1
RECEIPT_DOMAIN = b"animica:capabilities/job_receipt:v1"


@dataclass(frozen=True)
class JobReceiptV1:
    task_id: bytes
    kind: JobKind
    chain_id: int
    height: int
    tx_hash: bytes
    caller: bytes
    payload_hash: bytes
    created_at: int
    digest: bytes

    # --- (De)serialization helpers ---

    def to_map_without_digest(self) -> Dict[str, Any]:
        """CBOR/JSON-friendly map excluding `digest` (used for digest computation)."""
        return {
            "v": RECEIPT_VERSION,
            "task_id": self.task_id,
            "kind": int(self.kind.value if hasattr(self.kind, "value") else int(self.kind)),  # enum→int
            "chain_id": int(self.chain_id),
            "height": int(self.height),
            "tx_hash": self.tx_hash,
            "caller": self.caller,
            "payload_hash": self.payload_hash,
            "created_at": int(self.created_at),
        }

    def to_map(self) -> Dict[str, Any]:
        m = self.to_map_without_digest()
        m["digest"] = self.digest
        return m

    @staticmethod
    def from_map(m: Dict[str, Any]) -> "JobReceiptV1":
        if int(m.get("v", 0)) != RECEIPT_VERSION:
            raise CapError(f"unsupported receipt version: {m.get('v')}")
        try:
            kind_val = int(m["kind"])
            kind = JobKind(kind_val) if isinstance(kind_val, int) else JobKind[kind_val]
        except Exception as e:  # noqa: BLE001
            raise CapError(f"invalid JobKind in receipt: {m.get('kind')}") from e
        return JobReceiptV1(
            task_id=bytes(m["task_id"]),
            kind=kind,
            chain_id=int(m["chain_id"]),
            height=int(m["height"]),
            tx_hash=bytes(m["tx_hash"]),
            caller=bytes(m["caller"]),
            payload_hash=bytes(m["payload_hash"]),
            created_at=int(m["created_at"]),
            digest=bytes(m["digest"]),
        )

    def to_cbor(self) -> bytes:
        return _canon_dumps(self.to_map())

    @staticmethod
    def from_cbor(b: bytes) -> "JobReceiptV1":
        return JobReceiptV1.from_map(_canon_loads(b))


# --- Builders & validators ---------------------------------------------------

def compute_payload_hash(payload: Any) -> bytes:
    """
    Compute sha3-256 over canonical serialization of a job payload.
    """
    return _sha3_256(_canon_dumps(payload))


def compute_receipt_digest(map_without_digest: Dict[str, Any]) -> bytes:
    """
    Compute sha3-512 over DOMAIN || canonical(map-without-digest).
    """
    return _sha3_512(RECEIPT_DOMAIN + _canon_dumps(map_without_digest))


def build_receipt(
    *,
    kind: JobKind,
    chain_id: int,
    height: int,
    tx_hash: bytes,
    caller: bytes,
    payload: Any,
    created_at: int | None = None,
) -> JobReceiptV1:
    """
    Build a versioned, domain-separated job receipt and compute its digest.

    Task-id is derived deterministically from the same tuple used elsewhere:
    (chain_id, height, tx_hash, caller, payload)
    """
    if not isinstance(tx_hash, (bytes, bytearray)) or len(tx_hash) == 0:
        raise CapError("tx_hash must be non-empty bytes")
    if not isinstance(caller, (bytes, bytearray)) or len(caller) == 0:
        raise CapError("caller must be non-empty bytes")

    p_hash = compute_payload_hash(payload)
    task_id_hex = derive_task_id_hex(
        chain_id=chain_id,
        height=height,
        tx_hash=bytes(tx_hash),
        caller=bytes(caller),
        payload=payload,
    )
    created = int(created_at if created_at is not None else time.time())

    tmp = JobReceiptV1(
        task_id=bytes.fromhex(task_id_hex),
        kind=kind,
        chain_id=int(chain_id),
        height=int(height),
        tx_hash=bytes(tx_hash),
        caller=bytes(caller),
        payload_hash=p_hash,
        created_at=created,
        digest=b"",  # placeholder
    )
    digest = compute_receipt_digest(tmp.to_map_without_digest())
    return JobReceiptV1(**{**tmp.__dict__, "digest": digest})  # type: ignore[arg-type]


def validate_receipt(
    receipt: JobReceiptV1,
    *,
    expect_payload: Any | None = None,
    expect_chain_id: int | None = None,
    expect_height: int | None = None,
    expect_tx_hash: bytes | None = None,
    expect_caller: bytes | None = None,
) -> Tuple[bool, str]:
    """
    Validate a receipt's digest and (optionally) its bound fields.

    Returns (ok, reason). Raises on fatal malformation.
    """
    # Recompute digest
    recomputed = compute_receipt_digest(receipt.to_map_without_digest())
    if recomputed != receipt.digest:
        return (False, "digest_mismatch")

    # Optional payload check
    if expect_payload is not None:
        if compute_payload_hash(expect_payload) != receipt.payload_hash:
            return (False, "payload_hash_mismatch")

    if expect_chain_id is not None and int(expect_chain_id) != receipt.chain_id:
        return (False, "chain_id_mismatch")

    if expect_height is not None and int(expect_height) != receipt.height:
        return (False, "height_mismatch")

    if expect_tx_hash is not None and bytes(expect_tx_hash) != receipt.tx_hash:
        return (False, "tx_hash_mismatch")

    if expect_caller is not None and bytes(expect_caller) != receipt.caller:
        return (False, "caller_mismatch")

    # Deterministic task-id consistency check
    task_id_hex = derive_task_id_hex(
        chain_id=receipt.chain_id,
        height=receipt.height,
        tx_hash=receipt.tx_hash,
        caller=receipt.caller,
        payload=_canon_loads(_canon_dumps(expect_payload)) if expect_payload is not None else None
        or {},  # when unknown, check only when payload provided
    )
    # If payload was provided, we can compare; otherwise skip this strict check.
    if expect_payload is not None and bytes.fromhex(task_id_hex) != receipt.task_id:
        return (False, "task_id_mismatch")

    return (True, "ok")


# --- Module exports ----------------------------------------------------------

__all__ = [
    "JobReceiptV1",
    "RECEIPT_VERSION",
    "RECEIPT_DOMAIN",
    "compute_payload_hash",
    "compute_receipt_digest",
    "build_receipt",
    "validate_receipt",
]
