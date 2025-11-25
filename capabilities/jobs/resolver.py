from __future__ import annotations

"""
capabilities.jobs.resolver
--------------------------

Populate the capabilities **result_store** from proofs included in a sealed block.

This module is intentionally thin and defensive:
- It tolerates multiple input shapes (dict-like "proof claims" or rich objects).
- It attempts to use `capabilities.adapters.proofs.map_proofs_to_result_records`
  when available (preferred, canonical mapping).
- When that adapter is unavailable, it falls back to a best-effort coercion that
  derives a deterministic `task_id` and builds a `ResultRecord`.

Idempotency:
- If a `ResultRecord` for a given `task_id` already exists, we skip it.

Typical usage (during block import/finalization):
    resolver = ResultResolver(store, chain_id=1)
    n = resolver.apply_block(
        height=12345,
        proofs=block_proofs,          # iterable from proofs/registry or normalized dicts
        block_hash=header_hash,       # bytes
        timestamp=header_timestamp,   # int (unix seconds)
    )
"""

from dataclasses import asdict
from typing import Iterable, Optional, Any, Dict, List, Tuple, Callable
import logging
import time
import hashlib

from capabilities.errors import CapError
from capabilities.jobs.types import ResultRecord, JobKind
from capabilities.jobs.result_store import ResultStore

# Optional, canonical mapping if the adapter is present
try:
    from capabilities.adapters.proofs import (  # type: ignore
        map_proofs_to_result_records as _map_proofs_to_result_records,
    )
except Exception:  # pragma: no cover - optional dependency
    _map_proofs_to_result_records = None  # type: ignore[assignment]

# Optional, canonical task-id derivation
try:
    from capabilities.jobs.id import derive_task_id as _derive_task_id  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _derive_task_id = None  # type: ignore[assignment]

log = logging.getLogger(__name__)


def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def _bytes_of(x: Any) -> bytes:
    if x is None:
        return b""
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        # accept "0x…" hex or plain; fall back to utf-8
        s = x.strip()
        if s.startswith("0x") or s.startswith("0X"):
            try:
                return bytes.fromhex(s[2:])
            except ValueError:
                pass
        return s.encode("utf-8")
    if isinstance(x, int):
        # minimal big-endian
        if x < 0:
            raise ValueError("negative integers not allowed for byte conversion")
        if x == 0:
            return b"\x00"
        out = []
        while x:
            out.append(x & 0xFF)
            x >>= 8
        return bytes(reversed(out))
    # Try dataclass → dict → string
    try:
        if hasattr(x, "__dataclass_fields__"):
            return _bytes_of(asdict(x))
    except Exception:
        pass
    try:
        return _bytes_of(str(x))
    except Exception:
        return b""


def _fallback_task_id(
    *,
    chain_id: int,
    height: int,
    caller: bytes,
    payload_digest: bytes,
    tx_hash: Optional[bytes] = None,
) -> bytes:
    """
    Deterministic, domain-separated fallback for task_id:
      H("cap.task_id" | chain_id_u32 | height_u64 | caller | tx_hash? | payload_digest)
    """
    prefix = b"cap.task_id\x00"
    ci = chain_id.to_bytes(4, "big", signed=False)
    hi = height.to_bytes(8, "big", signed=False)
    parts = [prefix, ci, hi, caller, tx_hash or b"", payload_digest]
    return _sha3_256(b"".join(parts))


def _filter_kwargs_for_dataclass(cls: type, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    anns = getattr(cls, "__annotations__", {}) or {}
    return {k: v for k, v in kwargs.items() if k in anns}


class ResultResolver:
    """
    Convert proofs present in a sealed block into **ResultRecord** entries
    and persist them in the provided **ResultStore**.

    Parameters
    ----------
    store : ResultStore
        Backing store (SQLite or memory). Must implement get(task_id) and put(record).
    chain_id : int
        Chain ID for deterministic `task_id` derivation and sanity checks.
    """

    def __init__(self, store: ResultStore, *, chain_id: int) -> None:
        self._store = store
        self._chain_id = int(chain_id)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def apply_block(
        self,
        *,
        height: int,
        proofs: Iterable[Any],
        block_hash: Optional[bytes] = None,
        timestamp: Optional[int] = None,
    ) -> int:
        """
        Map `proofs` → ResultRecord(s) and persist new ones.

        Returns
        -------
        int
            Count of **new** records written.
        """
        if proofs is None:
            return 0

        ts = int(timestamp if timestamp is not None else time.time())
        bh = _bytes_of(block_hash) if block_hash is not None else b""

        records: List[ResultRecord] = []
        used_adapter = False

        # Preferred: use canonical adapter if available
        if _map_proofs_to_result_records is not None:
            try:
                records = _map_proofs_to_result_records(
                    proofs=proofs,
                    chain_id=self._chain_id,
                    height=int(height),
                    block_hash=bh or None,
                    timestamp=ts,
                )
                used_adapter = True
            except Exception as e:  # fall back gracefully
                log.warning("adapter mapping failed; falling back to best-effort coercion", exc_info=e)

        if not used_adapter:
            # Best-effort coercion
            for item in proofs:
                try:
                    rec = self._coerce_to_record(item, height=int(height), block_hash=bh, timestamp=ts)
                except Exception as e:
                    log.error("failed to coerce proof into ResultRecord; skipping", exc_info=e, extra={"proof": str(item)[:512]})
                    continue
                if rec is not None:
                    records.append(rec)

        # Persist, idempotently
        new_count = 0
        for rec in records:
            task_id: bytes = bytes(getattr(rec, "task_id"))
            try:
                # If already present, skip
                existing = self._store.get(task_id)  # type: ignore[arg-type]
                if existing is not None:
                    continue
            except AttributeError:
                # Some stores may expose .exists()
                try:
                    if getattr(self._store, "exists")(task_id):  # type: ignore[misc]
                        continue
                except Exception:
                    # If neither API exists, optimistically attempt put()
                    pass

            # Write
            self._store.put(rec)  # type: ignore[arg-type]
            new_count += 1

        return new_count

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _coerce_to_record(
        self,
        proof: Any,
        *,
        height: int,
        block_hash: bytes,
        timestamp: int,
    ) -> Optional[ResultRecord]:
        """
        Best-effort mapping from an arbitrary "proof" shape to a ResultRecord.

        Supported inputs (examples):
          - dicts with keys:
              {"kind": "AI"|"Quantum",
               "caller": <bytes|hex str>,
               "payload_digest": <bytes|hex str>,
               "units": <int>,
               "task_id": <bytes|hex str> (optional),
               "tx_hash": <bytes|hex str> (optional)}
          - objects exposing matching attributes
        """
        if proof is None:
            return None

        # Extract dictionary-like view
        m: Dict[str, Any]
        if isinstance(proof, dict):
            m = proof
        else:
            m = {}
            for k in ("kind", "caller", "payload_digest", "units", "task_id", "tx_hash", "nullifier"):
                if hasattr(proof, k):
                    m[k] = getattr(proof, k)

        # Required fields (with sane defaults)
        kind_raw = (m.get("kind") or "AI").upper()
        kind = _coerce_kind(kind_raw)

        caller = _bytes_of(m.get("caller"))
        payload_digest = _bytes_of(m.get("payload_digest"))
        units = int(m.get("units") or 0)
        tx_hash = _bytes_of(m.get("tx_hash"))
        nullifier = _bytes_of(m.get("nullifier"))

        # Derive or use provided task_id
        task_id = _bytes_of(m.get("task_id"))
        if not task_id:
            if _derive_task_id is not None:
                try:
                    task_id = _derive_task_id(
                        chain_id=self._chain_id,
                        height=int(height),
                        caller=caller,
                        payload_digest=payload_digest,
                        tx_hash=tx_hash or None,
                    )
                except Exception:
                    task_id = _fallback_task_id(
                        chain_id=self._chain_id,
                        height=int(height),
                        caller=caller,
                        payload_digest=payload_digest,
                        tx_hash=tx_hash or None,
                    )
            else:
                task_id = _fallback_task_id(
                    chain_id=self._chain_id,
                    height=int(height),
                    caller=caller,
                    payload_digest=payload_digest,
                    tx_hash=tx_hash or None,
                )

        # Build ResultRecord with only accepted fields
        kwargs = dict(
            task_id=task_id,
            chain_id=self._chain_id,
            height=int(height),
            caller=caller,
            kind=kind,
            units=units,
            status="COMPLETED",
            payload_digest=payload_digest,
            tx_hash=tx_hash or None,
            nullifier=nullifier or None,
            block_hash=block_hash or None,
            created_at=int(timestamp),
        )
        kwargs = _filter_kwargs_for_dataclass(ResultRecord, kwargs)
        return ResultRecord(**kwargs)  # type: ignore[arg-type]


def _coerce_kind(kind: str) -> JobKind | str:
    """
    Convert a raw kind label into a JobKind enum when available.
    Falls back to the original string if the enum doesn't define it.
    """
    label = (kind or "").upper()
    try:
        return JobKind[label]  # type: ignore[index]
    except Exception:
        # Accept a small set of synonyms
        aliases = {
            "AI": "AI",
            "AICOMPUTE": "AI",
            "ML": "AI",
            "QUANTUM": "QUANTUM",
            "QPU": "QUANTUM",
        }
        norm = aliases.get(label, label)
        try:
            return JobKind[norm]  # type: ignore[index]
        except Exception:
            return norm  # type: ignore[return-value]


__all__ = ["ResultResolver"]
