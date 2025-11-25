"""
Logical buckets over a raw byte-oriented KeyValue backend.

This module provides thin, typed helpers for composing namespaced keys used by
the randomness subsystem:

Buckets
-------
- COMMITS:  per-(round, participant) commitments
- REVEALS:  per-(round, participant) reveals
- VDF:      per-round VDF input/proof artifacts
- BEACON:   per-round finalized beacon outputs (and optional light proofs)
- META:     singleton / misc values (current round, params snapshots, etc.)

All values are bytes. Higher layers are responsible for serialization of
structured records (e.g., CommitRecord, RevealRecord, VDFProof, BeaconOut).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from . import KeyValue


# --- Bucket prefix constants (single-byte, domain-separated) -----------------

COMMITS_PREFIX = b"\x01"  # COMMITS:  \x01 | len(round) | round | len(part) | part
REVEALS_PREFIX = b"\x02"  # REVEALS:  \x02 | len(round) | round | len(part) | part
VDF_PREFIX     = b"\x03"  # VDF:      \x03 | tag | len(round) | round
BEACON_PREFIX  = b"\x04"  # BEACON:   \x04 | tag | len(round) | round
META_PREFIX    = b"\x05"  # META:     \x05 | len(key) | key


# --- VDF / BEACON sub-tags ---------------------------------------------------

VDF_IN_TAG    = b"I"   # VDF input
VDF_PROOF_TAG = b"P"   # VDF proof
VDF_AUX_TAG   = b"A"   # optional aux (e.g., calibration data), by round

BEACON_OUT_TAG   = b"O"   # finalized beacon bytes for a round
BEACON_LIGHT_TAG = b"L"   # optional light-client proof for a round


# --- Key composition helpers -------------------------------------------------

def _be_u32(n: int) -> bytes:
    if n < 0 or n > 0xFFFFFFFF:
        raise ValueError("length out of range for u32")
    return n.to_bytes(4, "big")


def _k(prefix: bytes, *parts: bytes) -> bytes:
    """Prefix + 4-byte len for each part to avoid accidental collisions."""
    return prefix + b"".join(_be_u32(len(p)) + p for p in parts)


def _k_tagged(prefix: bytes, tag: bytes, *parts: bytes) -> bytes:
    return prefix + tag + b"".join(_be_u32(len(p)) + p for p in parts)


def _utf8(b: str | bytes) -> bytes:
    return b if isinstance(b, bytes) else b.encode("utf-8")


# --- Public bucket API -------------------------------------------------------

@dataclass(frozen=True)
class Buckets:
    """
    Namespaced view over a byte KV store used by the randomness subsystem.

    Keys are constructed deterministically using:
      key = PREFIX || (TAG?) || concat(u32_be(len(part)) || part for part in parts)

    Notes:
      - `round_id` and `participant` are raw bytes chosen by the caller (e.g.
        RoundId canonical encoding, address bytes, etc).
      - Iteration order is backend-defined; do not depend on it.
    """
    kv: KeyValue

    # --- Commits -------------------------------------------------------------

    def key_commit(self, round_id: bytes, participant: bytes) -> bytes:
        """Key for a participant's commitment in a round."""
        return _k(COMMITS_PREFIX, round_id, participant)

    def put_commit(self, round_id: bytes, participant: bytes, value: bytes) -> None:
        self.kv.put(self.key_commit(round_id, participant), value)

    def get_commit(self, round_id: bytes, participant: bytes) -> Optional[bytes]:
        return self.kv.get(self.key_commit(round_id, participant))

    def del_commit(self, round_id: bytes, participant: bytes) -> None:
        self.kv.delete(self.key_commit(round_id, participant))

    def iter_commits(self, round_id: bytes) -> Iterable[Tuple[bytes, bytes]]:
        """Iterate all (key, value) commit entries for a round."""
        prefix = _k(COMMITS_PREFIX, round_id)
        return self.kv.iter_prefix(prefix)

    # --- Reveals -------------------------------------------------------------

    def key_reveal(self, round_id: bytes, participant: bytes) -> bytes:
        return _k(REVEALS_PREFIX, round_id, participant)

    def put_reveal(self, round_id: bytes, participant: bytes, value: bytes) -> None:
        self.kv.put(self.key_reveal(round_id, participant), value)

    def get_reveal(self, round_id: bytes, participant: bytes) -> Optional[bytes]:
        return self.kv.get(self.key_reveal(round_id, participant))

    def del_reveal(self, round_id: bytes, participant: bytes) -> None:
        self.kv.delete(self.key_reveal(round_id, participant))

    def iter_reveals(self, round_id: bytes) -> Iterable[Tuple[bytes, bytes]]:
        prefix = _k(REVEALS_PREFIX, round_id)
        return self.kv.iter_prefix(prefix)

    # --- VDF artifacts -------------------------------------------------------

    def key_vdf_input(self, round_id: bytes) -> bytes:
        return _k_tagged(VDF_PREFIX, VDF_IN_TAG, round_id)

    def key_vdf_proof(self, round_id: bytes) -> bytes:
        return _k_tagged(VDF_PREFIX, VDF_PROOF_TAG, round_id)

    def key_vdf_aux(self, round_id: bytes) -> bytes:
        return _k_tagged(VDF_PREFIX, VDF_AUX_TAG, round_id)

    def put_vdf_input(self, round_id: bytes, value: bytes) -> None:
        self.kv.put(self.key_vdf_input(round_id), value)

    def get_vdf_input(self, round_id: bytes) -> Optional[bytes]:
        return self.kv.get(self.key_vdf_input(round_id))

    def put_vdf_proof(self, round_id: bytes, value: bytes) -> None:
        self.kv.put(self.key_vdf_proof(round_id), value)

    def get_vdf_proof(self, round_id: bytes) -> Optional[bytes]:
        return self.kv.get(self.key_vdf_proof(round_id))

    def put_vdf_aux(self, round_id: bytes, value: bytes) -> None:
        self.kv.put(self.key_vdf_aux(round_id), value)

    def get_vdf_aux(self, round_id: bytes) -> Optional[bytes]:
        return self.kv.get(self.key_vdf_aux(round_id))

    def del_vdf(self, round_id: bytes) -> None:
        """Delete all VDF artifacts for a round (input/proof/aux)."""
        for k in (self.key_vdf_input(round_id), self.key_vdf_proof(round_id), self.key_vdf_aux(round_id)):
            self.kv.delete(k)

    # --- Beacon outputs / proofs --------------------------------------------

    def key_beacon_out(self, round_id: bytes) -> bytes:
        return _k_tagged(BEACON_PREFIX, BEACON_OUT_TAG, round_id)

    def key_beacon_light(self, round_id: bytes) -> bytes:
        return _k_tagged(BEACON_PREFIX, BEACON_LIGHT_TAG, round_id)

    def put_beacon_out(self, round_id: bytes, value: bytes) -> None:
        self.kv.put(self.key_beacon_out(round_id), value)

    def get_beacon_out(self, round_id: bytes) -> Optional[bytes]:
        return self.kv.get(self.key_beacon_out(round_id))

    def put_beacon_light(self, round_id: bytes, value: bytes) -> None:
        self.kv.put(self.key_beacon_light(round_id), value)

    def get_beacon_light(self, round_id: bytes) -> Optional[bytes]:
        return self.kv.get(self.key_beacon_light(round_id))

    # --- Meta ----------------------------------------------------------------

    def key_meta(self, name: str | bytes) -> bytes:
        """Key for a singleton/meta value. `name` is utf-8 if str."""
        name_b = _utf8(name)
        return _k(META_PREFIX, name_b)

    def put_meta(self, name: str | bytes, value: bytes) -> None:
        self.kv.put(self.key_meta(name), value)

    def get_meta(self, name: str | bytes) -> Optional[bytes]:
        return self.kv.get(self.key_meta(name))

    def del_meta(self, name: str | bytes) -> None:
        self.kv.delete(self.key_meta(name))

    # Convenience meta keys commonly used by beacon logic
    META_CURRENT_ROUND = b"current_round"          # bytes: canonical RoundId encoding
    META_PREV_ROUND    = b"prev_round"             # bytes: canonical RoundId encoding
    META_PARAMS_SNAPSHOT = b"params_snapshot"      # bytes: serialized params
    META_LAST_FINALIZED = b"last_finalized_round"  # bytes: RoundId

    # --- Prefix iteration helpers -------------------------------------------

    def iter_vdf(self) -> Iterable[Tuple[bytes, bytes]]:
        """Iterate all VDF artifacts (any round)."""
        return self.kv.iter_prefix(VDF_PREFIX)

    def iter_beacon(self) -> Iterable[Tuple[bytes, bytes]]:
        """Iterate all beacon artifacts (any round)."""
        return self.kv.iter_prefix(BEACON_PREFIX)

    def iter_meta(self) -> Iterable[Tuple[bytes, bytes]]:
        """Iterate all meta entries."""
        return self.kv.iter_prefix(META_PREFIX)


__all__ = [
    "Buckets",
    "COMMITS_PREFIX",
    "REVEALS_PREFIX",
    "VDF_PREFIX",
    "BEACON_PREFIX",
    "META_PREFIX",
    "VDF_IN_TAG",
    "VDF_PROOF_TAG",
    "VDF_AUX_TAG",
    "BEACON_OUT_TAG",
    "BEACON_LIGHT_TAG",
]
