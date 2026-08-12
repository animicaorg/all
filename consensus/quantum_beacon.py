"""FORK_QUANTUM_BEACON (9.5.0) — binding the canonical chain to attested quantum randomness.

Animica already produces an attested hardware-QRNG randomness beacon, but only as a
reward lane: nothing in consensus commits to it, so the chain does not actually *depend*
on quantum-sourced randomness. This module is what lets a block commit to it, verifiably.

THE DESIGN IS DORMANT ON PURPOSE, and that is the safety argument rather than a caveat.
A block that carries no beacon commitment is valid at every height, forever. Only a block
that DOES commit one must commit a correct one. So there is no liveness dependency on
beacon availability to fail open — a QRNG outage cannot halt or even slow the chain,
because there is nothing for it to be unavailable *to*. The rule may sit activated and
completely inert indefinitely, which is the expected state for a good while.

WHAT IS CHECKED, when and only when a commitment is present:

  1. Recomputation. value == H(BEACON_DOMAIN || round || prev || aggregate) — the same
     construction as randomness.qrng.public.build_quantum_beacon. This is what makes the
     commitment unforgeable from the block's own bytes, with no external lookup.
  2. Continuity. The committed `prev` must equal the beacon value committed by the
     nearest ancestor that carried one. Recomputation alone would let each block invent
     an unrelated beacon; the prev-chain is what makes the CANONICAL CHAIN — rather than
     an individual block — commit to the randomness.

Everything here is a pure function of (commitment, ancestor commitment, height). No
clocks, no network, no node-local state, so two honest nodes always agree.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from core.network_params import FORK_QUANTUM_BEACON, is_fork_active
from core.utils.hash import sha3_256

# Must match randomness.qrng.public._BEACON_DOMAIN. Duplicated deliberately: consensus
# must not import a non-consensus package at validation time, and a domain string that
# silently changed underneath would alter validity. If the two ever diverge, the
# recomputation check fails closed for committed blocks rather than accepting junk.
BEACON_DOMAIN = b"animica/qrng/beacon/v1"

# The key a block uses to carry the commitment inside its CBOR `extra` map, alongside
# the existing `coinbase` and `instant_block` keys.
EXTRA_KEY = "beacon"

_HASH_LEN = 32


class BeaconError(ValueError):
    """A present beacon commitment is malformed or does not verify."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def beacon_binding_active(height: int, *, chain_id: int = 1) -> bool:
    return bool(is_fork_active(FORK_QUANTUM_BEACON, int(height), chain_id=int(chain_id)))


def compute_beacon_value(round_id: int, prev: bytes, aggregate: bytes) -> bytes:
    """H(DOMAIN || round || prev || aggregate) — the beacon construction."""
    return sha3_256(
        BEACON_DOMAIN + int(round_id).to_bytes(8, "big") + bytes(prev) + bytes(aggregate)
    )


def extract_commitment(extra: Any) -> Optional[Mapping[str, Any]]:
    """The beacon commitment carried by a header's `extra`, or None.

    Returns None for every block that does not carry one — which is every block today.
    A malformed `extra` is treated as "no commitment" rather than an error, because
    `extra` is shared with other features and this rule must never reject a block over
    a field it does not own.
    """
    if not extra:
        return None
    raw = extra
    if isinstance(extra, (bytes, bytearray)):
        try:
            import cbor2

            raw = cbor2.loads(bytes(extra))
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(raw, Mapping):
        return None
    got = raw.get(EXTRA_KEY)
    return got if isinstance(got, Mapping) else None


def _field_bytes(rec: Mapping[str, Any], key: str) -> bytes:
    v = rec.get(key)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, str):
        try:
            return bytes.fromhex(v[2:] if v.startswith("0x") else v)
        except ValueError as exc:
            raise BeaconError(f"beacon.{key} is not hex", code="BEACON_MALFORMED") from exc
    raise BeaconError(f"beacon.{key} missing or wrong type", code="BEACON_MALFORMED")


def check_commitment(
    commitment: Mapping[str, Any],
    *,
    parent_commitment: Optional[Mapping[str, Any]] = None,
) -> bytes:
    """Verify a present commitment and return its beacon value.

    Raises BeaconError on anything malformed or inconsistent. Called ONLY when a
    commitment is present, so a chain with no beacons never reaches this code.
    """
    try:
        round_id = int(commitment.get("round"))
    except (TypeError, ValueError) as exc:
        raise BeaconError("beacon.round missing or not an integer",
                          code="BEACON_MALFORMED") from exc
    if round_id < 0:
        raise BeaconError("beacon.round must be >= 0", code="BEACON_MALFORMED")

    value = _field_bytes(commitment, "value")
    prev = _field_bytes(commitment, "prev")
    aggregate = _field_bytes(commitment, "aggregate")
    for name, b in (("value", value), ("prev", prev)):
        if len(b) != _HASH_LEN:
            raise BeaconError(f"beacon.{name} must be {_HASH_LEN} bytes",
                              code="BEACON_MALFORMED")

    # 1. Recomputation — unforgeable from the block's own bytes.
    expected = compute_beacon_value(round_id, prev, aggregate)
    if expected != value:
        raise BeaconError("beacon.value does not match H(domain||round||prev||aggregate)",
                          code="BEACON_VALUE_MISMATCH")

    # 2. Continuity — this is what binds the CHAIN rather than a single block.
    if parent_commitment is not None:
        parent_value = _field_bytes(parent_commitment, "value")
        if prev != parent_value:
            raise BeaconError(
                "beacon.prev does not chain to the nearest ancestor commitment",
                code="BEACON_CHAIN_BREAK",
            )
        try:
            parent_round = int(parent_commitment.get("round"))
        except (TypeError, ValueError):
            parent_round = -1
        if parent_round >= 0 and round_id <= parent_round:
            raise BeaconError("beacon.round must strictly increase",
                              code="BEACON_ROUND_REGRESSION")
    return value


def validate_header_beacon(
    extra: Any,
    height: int,
    *,
    parent_extra: Any = None,
    chain_id: int = 1,
) -> Optional[bytes]:
    """Consensus entry point. Returns the committed beacon value, or None.

    None means "this block commits no beacon", which is ALWAYS valid — at every height,
    including after activation. This is the property that makes a QRNG outage unable to
    affect the chain: there is no path here that can reject a block for the absence of a
    beacon.

    Below the activation height a commitment is ignored entirely, so history is untouched
    and a block that happened to carry a malformed one stays valid forever.
    """
    commitment = extract_commitment(extra)
    if commitment is None:
        return None
    if not beacon_binding_active(height, chain_id=chain_id):
        return None
    return check_commitment(commitment, parent_commitment=extract_commitment(parent_extra))
