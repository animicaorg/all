"""
Execution ⇄ Randomness adapter (read-only, contract-facing).

This module exposes a deterministic *beacon read* surface that contracts (via
the VM/runtime bindings) can use to access the chain's randomness beacon.

Design goals
------------
- Read-only and deterministic: given a block height (and optional lookback),
  returns the exact 32-byte beacon digest that every honest node will derive.
- No heavy objects: we return the canonical 32-byte digest (sha3-256 over the
  BeaconOut encoding) rather than large records.
- Domain separation helpers: convenience to derive per-contract pseudo-random
  bytes from the beacon digest using SHAKE-256, with explicit labels.

Data source
-----------
We read pointers stored by `randomness.adapters.core_db.RandomnessCoreDB`
during block sealing. Those pointers include the `beacon_digest` and `round_id`
for each block height.

Typical usage (VM / syscall binding)
------------------------------------
    # In a binding layer with access to the current block height:
    from randomness.adapters.core_db import RandomnessCoreDB
    from randomness.adapters.execution import ExecutionRandomness

    exec_rand = ExecutionRandomness(pointer_source=RandomnessCoreDB(kv))
    digest32 = exec_rand.beacon_digest_at_height(block_height)
    # Or derive N bytes for contract-specific randomness:
    rnd = exec_rand.derive_bytes(block_height, label=b"myContract.op:42", nbytes=64)

Errors
------
- NoBeaconAvailable: no pointer exists at the target height (or its beacon_digest is None)
- ValueError: invalid arguments (negative lookback, height underflow, etc.)

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Mapping
import hashlib
import logging

try:
    # Prefer importing the real pointer types if available.
    from randomness.adapters.core_db import PointerRecord
except Exception:  # pragma: no cover - soft fallback for isolated use
    @dataclass(frozen=True)
    class PointerRecord:  # type: ignore[override]
        height: int
        round_id: int
        commit_count: int
        reveal_count: int
        vdf_verified: bool
        beacon_digest: Optional[str]
        vdf_digest: Optional[str]
        block_hash: Optional[str]
        meta: Optional[Mapping[str, object]] = None  # noqa: UP007


logger = logging.getLogger(__name__)

DOMAIN_BEACON_DERIVE = b"animica.rand.contract.v1"


class PointerSource(Protocol):
    """Protocol satisfied by RandomnessCoreDB (or a test double)."""
    def get_block_randomness(self, height: int) -> Optional[PointerRecord]: ...


class NoBeaconAvailable(Exception):
    """Raised when no beacon digest is available for a requested height."""


def _hex_to_bytes32(hex_s: str) -> bytes:
    if not hex_s.startswith("0x"):
        raise ValueError("expected 0x-prefixed hex string")
    raw = bytes.fromhex(hex_s[2:])
    if len(raw) != 32:
        raise ValueError("beacon digest must be 32 bytes")
    return raw


class ExecutionRandomness:
    """
    Deterministic reader for exposing beacon digests to the execution layer.

    The adapter is intentionally minimal: it returns 32 bytes for a given block
    height (optionally with a lookback). Higher-level bindings may wrap this to
    charge gas or to provide additional convenience methods to contracts.
    """

    def __init__(self, pointer_source: PointerSource) -> None:
        self._src = pointer_source

    # ---------- Core lookup ----------

    def beacon_digest_at_height(self, height: int, lookback: int = 0) -> bytes:
        """
        Return the 32-byte beacon digest anchored to `height - lookback`.

        This is the canonical sha3-256 digest of the BeaconOut encoding recorded
        at sealing time for that block height.

        Raises:
            ValueError: if `lookback` is negative or target height < 0.
            NoBeaconAvailable: if the pointer is missing or has no digest.
        """
        if lookback < 0:
            raise ValueError("lookback must be non-negative")
        target = height - lookback
        if target < 0:
            raise ValueError("target height underflow (height - lookback < 0)")

        rec = self._src.get_block_randomness(target)
        if rec is None or rec.beacon_digest is None:
            raise NoBeaconAvailable(f"no beacon recorded at height {target}")

        return _hex_to_bytes32(rec.beacon_digest)

    def round_id_at_height(self, height: int, lookback: int = 0) -> int:
        """
        Return the randomness round id anchored to `height - lookback`.

        Raises:
            ValueError / NoBeaconAvailable: as in `beacon_digest_at_height`.
        """
        if lookback < 0:
            raise ValueError("lookback must be non-negative")
        target = height - lookback
        if target < 0:
            raise ValueError("target height underflow (height - lookback < 0)")
        rec = self._src.get_block_randomness(target)
        if rec is None:
            raise NoBeaconAvailable(f"no beacon recorded at height {target}")
        return int(rec.round_id)

    # ---------- Derivation helpers (domain-separated) ----------

    def derive_bytes(
        self,
        height: int,
        *,
        lookback: int = 0,
        label: bytes = b"",
        nbytes: int = 32,
    ) -> bytes:
        """
        Derive `nbytes` of pseudo-random bytes from the beacon digest with domain separation.

        We use SHAKE-256 over: DOMAIN || length(label) || label || digest32

        Args:
            height:  Anchor block height.
            lookback:How many blocks back to reference.
            label:   Optional contract-specified label to separate call-sites.
            nbytes:  Number of bytes to return (default 32).

        Returns:
            Bytes of length `nbytes`, deterministically derived.

        Raises:
            NoBeaconAvailable / ValueError: on missing digest or bad args.
        """
        if nbytes <= 0:
            raise ValueError("nbytes must be positive")
        digest = self.beacon_digest_at_height(height, lookback)
        shake = hashlib.shake_256()
        # Domain-separated transcript:
        #   "animica.rand.contract.v1" || u32(len(label)) || label || digest32
        shake.update(DOMAIN_BEACON_DERIVE)
        shake.update(len(label).to_bytes(4, "big"))
        shake.update(label)
        shake.update(digest)
        out = shake.digest(nbytes)
        return out


# ---------------- Convenience top-level helpers ----------------

def get_beacon_digest(
    *,
    block_height: int,
    lookback: int = 0,
    source: PointerSource,
) -> bytes:
    """
    One-shot convenience to fetch the 32-byte beacon digest for a contract.

    Suitable for direct binding from a VM syscall where `block_height` is known.
    """
    return ExecutionRandomness(source).beacon_digest_at_height(block_height, lookback)


def get_beacon_round_id(
    *,
    block_height: int,
    lookback: int = 0,
    source: PointerSource,
) -> int:
    """Fetch the beacon's round id for `block_height - lookback`."""
    return ExecutionRandomness(source).round_id_at_height(block_height, lookback)


def derive_contract_random(
    *,
    block_height: int,
    lookback: int = 0,
    label: bytes = b"",
    nbytes: int = 32,
    source: PointerSource,
) -> bytes:
    """
    Derive `nbytes` of contract-scoped randomness from the beacon digest.

    See `ExecutionRandomness.derive_bytes` for transcript details.
    """
    return ExecutionRandomness(source).derive_bytes(
        block_height, lookback=lookback, label=label, nbytes=nbytes
    )


__all__ = [
    "PointerSource",
    "NoBeaconAvailable",
    "ExecutionRandomness",
    "get_beacon_digest",
    "get_beacon_round_id",
    "derive_contract_random",
]
