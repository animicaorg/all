"""
Randomness ⇄ Core-DB adapter.

Purpose
-------
Persist *pointers* from sealed blocks to the randomness artifacts that were
consumed/produced while sealing those blocks, so other subsystems (explorer,
light-clients, auditors) can navigate:

  block_height  →  randomness round / aggregates / VDF proof digest

Design
------
We intentionally keep this adapter storage-agnostic by coding against a tiny
KV protocol (see `MetaKV` below). The concrete implementation can be backed by
the node's core block DB (e.g., SQLite/Rocks/LMDB). Keys are namespaced under
`b"rand:block:"`.

Stored value is a compact, deterministic JSON blob (stable separators + sorted
keys) with the following fields:

  {
    "height":        <int>,                 # block height
    "block_hash":    "0x...",               # optional (if known at seal)
    "round_id":      <int>,                 # randomness round number
    "commit_count":  <int>,                 # number of valid commit reveals aggregated
    "reveal_count":  <int>,                 # number of accepted reveals
    "vdf_verified":  <bool>,                # did the VDF verify?
    "beacon_digest": "0x...",               # sha3-256 over the BeaconOut encoding
    "vdf_digest":    "0x...",               # sha3-256 over the VDF proof encoding
    "meta":          {...}                  # optional extra pointers (impl-specific)
  }

Only *pointers/digests* are stored here; large objects (full BeaconOut, reveals,
proofs) live in the randomness stores.

Typical use
-----------
During block sealing/finalization:

    adapter = RandomnessCoreDB(kv)
    adapter.link_round_to_block(
        height=H,
        round_id=round_id,
        beacon=beacon_out_bytes,
        vdf_proof=vdf_proof_bytes,
        commit_count=len(commits_ok),
        reveal_count=len(reveals_ok),
        vdf_verified=True,
        block_hash=block_hash_bytes,
        meta={"agg_root": "0x...", "reveal_root": "0x..."}  # optional
    )

Later lookups:

    rec = adapter.get_block_randomness(H)
    if rec and rec["vdf_verified"]:
        ...

Notes
-----
- Idempotent: writing the same pointer for the same height is a no-op by default.
- Safe overwrite can be enabled with `overwrite=True`.
- Encodings are deliberately simple to avoid extra deps.

"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Protocol

logger = logging.getLogger(__name__)


# -----------------------------
# Minimal KV protocol & helpers
# -----------------------------


class MetaKV(Protocol):
    """Tiny KV protocol expected from the core block DB layer."""

    def get(self, key: bytes) -> Optional[bytes]: ...
    def put(self, key: bytes, value: bytes) -> None: ...


def _u64_be(n: int) -> bytes:
    if n < 0:
        raise ValueError("height/round must be non-negative")
    return n.to_bytes(8, "big", signed=False)


def _hex(b: Optional[bytes]) -> Optional[str]:
    return None if b is None else "0x" + b.hex()


def _sha3_256(data: Optional[bytes]) -> Optional[str]:
    if data is None:
        return None
    return "0x" + hashlib.sha3_256(data).hexdigest()


def _key_for_block(height: int) -> bytes:
    return b"rand:block:" + _u64_be(height)


def _dumps_stable(obj: Mapping[str, Any]) -> bytes:
    """Deterministic JSON encoding (stable separators & sorted keys)."""
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _loads_stable(data: bytes) -> Dict[str, Any]:
    return json.loads(data.decode("utf-8"))


# -----------------------------
# Public API
# -----------------------------


@dataclass(frozen=True)
class PointerRecord:
    height: int
    round_id: int
    commit_count: int
    reveal_count: int
    vdf_verified: bool
    beacon_digest: Optional[str]
    vdf_digest: Optional[str]
    block_hash: Optional[str]
    meta: Optional[Mapping[str, Any]] = None

    def to_json_bytes(self) -> bytes:
        payload = {
            "height": self.height,
            "round_id": self.round_id,
            "commit_count": self.commit_count,
            "reveal_count": self.reveal_count,
            "vdf_verified": self.vdf_verified,
            "beacon_digest": self.beacon_digest,
            "vdf_digest": self.vdf_digest,
            "block_hash": self.block_hash,
            "meta": self.meta or {},
        }
        return _dumps_stable(payload)

    @staticmethod
    def from_json_bytes(data: bytes) -> "PointerRecord":
        obj = _loads_stable(data)
        return PointerRecord(
            height=int(obj["height"]),
            round_id=int(obj["round_id"]),
            commit_count=int(obj["commit_count"]),
            reveal_count=int(obj["reveal_count"]),
            vdf_verified=bool(obj["vdf_verified"]),
            beacon_digest=obj.get("beacon_digest"),
            vdf_digest=obj.get("vdf_digest"),
            block_hash=obj.get("block_hash"),
            meta=obj.get("meta") or {},
        )


class RandomnessCoreDB:
    """Adapter that persists block→randomness pointers in the core DB."""

    def __init__(self, kv: MetaKV) -> None:
        self._kv = kv

    def link_round_to_block(
        self,
        *,
        height: int,
        round_id: int,
        beacon: Optional[bytes],
        vdf_proof: Optional[bytes],
        commit_count: int,
        reveal_count: int,
        vdf_verified: bool,
        block_hash: Optional[bytes] = None,
        meta: Optional[Mapping[str, Any]] = None,
        overwrite: bool = False,
    ) -> PointerRecord:
        """
        Persist the pointer record for a sealed block.

        Args:
            height:         Block height being sealed.
            round_id:       Randomness round used/produced at this height.
            beacon:         Canonical BeaconOut encoding (bytes) or None.
            vdf_proof:      Canonical VDF proof encoding (bytes) or None.
            commit_count:   Number of commits aggregated.
            reveal_count:   Number of reveals accepted.
            vdf_verified:   Whether the VDF verified true at seal time.
            block_hash:     Optional block hash (bytes) if known before persist.
            meta:           Optional extra pointers (roots, indices, etc.).
            overwrite:      If False (default), writing over an existing record
                            with different digest is rejected.

        Returns:
            PointerRecord written to the DB.
        """
        key = _key_for_block(height)
        existing = self._kv.get(key)
        new_rec = PointerRecord(
            height=height,
            round_id=round_id,
            commit_count=commit_count,
            reveal_count=reveal_count,
            vdf_verified=vdf_verified,
            beacon_digest=_sha3_256(beacon),
            vdf_digest=_sha3_256(vdf_proof),
            block_hash=_hex(block_hash),
            meta=dict(meta) if meta else None,
        )

        if existing is not None:
            prev = PointerRecord.from_json_bytes(existing)
            if not overwrite:
                # Idempotent accept if identical; otherwise, refuse.
                if prev == new_rec:
                    logger.debug(
                        "Randomness pointer already recorded (idempotent): height=%s",
                        height,
                    )
                    return prev
                raise ValueError(
                    f"randomness pointer already exists at height {height} and differs; "
                    f"pass overwrite=True to replace"
                )
            logger.warning(
                "Overwriting randomness pointer at height=%s (prev=%s, new=%s)",
                height,
                prev,
                new_rec,
            )

        self._kv.put(key, new_rec.to_json_bytes())
        logger.info(
            "Recorded randomness pointer: height=%s round=%s vdf_ok=%s commits=%s reveals=%s",
            height,
            round_id,
            vdf_verified,
            commit_count,
            reveal_count,
        )
        return new_rec

    def get_block_randomness(self, height: int) -> Optional[PointerRecord]:
        """Fetch the randomness pointer for a given block height, if any."""
        data = self._kv.get(_key_for_block(height))
        return None if data is None else PointerRecord.from_json_bytes(data)


# -----------------------------
# In-memory KV (for testing / CLI tools)
# -----------------------------


class _DictKV(MetaKV):
    """Simple in-memory KV useful for unit tests or tooling."""

    def __init__(self) -> None:
        self._d: dict[bytes, bytes] = {}

    def get(self, key: bytes) -> Optional[bytes]:
        return self._d.get(key)

    def put(self, key: bytes, value: bytes) -> None:
        self._d[key] = value


__all__ = [
    "MetaKV",
    "PointerRecord",
    "RandomnessCoreDB",
]
