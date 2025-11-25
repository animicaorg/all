"""
randomness.beacon.light_proof
=============================

A compact, codec-agnostic proof object for light clients to verify a beacon
round using only:
  - the previous beacon's *hash* (hash-chain),
  - the current round's VDF input and proof,
  - and the claimed beacon output for this round.

Verification checks:
  1) (optional) If the previous BeaconOut is supplied, its hash must match
     `prev_out_hash` and its round must be `round_id - 1`.
  2) The VDF verifier must accept (vdf_input, vdf_proof) → output.
  3) Structural sanity checks (sizes, monotonic round).

This file is intentionally *storage/codec agnostic*. Use your preferred
CBOR/JSON codec (e.g., msgspec) by serializing `LightProof.to_dict()`.

Domain separation
-----------------
We avoid cross-protocol collisions by hashing with explicit tags:
  - BEACON_OUT_HASH_TAG for hashing minimal beacon outputs,
  - LIGHT_PROOF_COMMIT_TAG for committing to a LightProof (if needed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from randomness.types.core import BeaconOut, VDFInput, VDFProof, RoundId
from randomness.vdf.verifier import verify as vdf_verify
from randomness.utils.hash import sha3_256


# ---- Domain tags (kept local to avoid depending on external constants) ----
BEACON_OUT_HASH_TAG = b"animica:beacon:out-hash:v1"
LIGHT_PROOF_COMMIT_TAG = b"animica:beacon:light-proof:v1"


def _u64be(n: int) -> bytes:
    return int(n).to_bytes(8, "big", signed=False)


def hash_beacon_out_min(beacon: BeaconOut) -> bytes:
    """
    Hash a minimal view of a BeaconOut for chaining:
      H = SHA3-256( tag || round_id_u64 || output_bytes )
    """
    # We only rely on round_id and output for chaining. Other fields are
    # verifiable via the VDF proof carried by the next LightProof.
    if not hasattr(beacon, "output"):
        raise TypeError("BeaconOut missing required field 'output'")
    return sha3_256(BEACON_OUT_HASH_TAG + _u64be(beacon.round_id) + bytes(beacon.output))


@dataclass(frozen=True)
class LightProof:
    """
    Compact proof for a finalized beacon round.

    Fields
    ------
    round_id       : RoundId
        The round this proof finalizes.
    prev_out_hash  : bytes (32)
        SHA3-256 hash of the *previous* BeaconOut, computed by `hash_beacon_out_min`.
    vdf_input      : VDFInput
        Public input/challenge to the VDF for this round.
    vdf_proof      : VDFProof
        Wesolowski (or chosen scheme) proof object.
    output         : bytes
        The beacon output value for `round_id` (post-VDF and any in-round mixing).
    """
    round_id: RoundId
    prev_out_hash: bytes
    vdf_input: VDFInput
    vdf_proof: VDFProof
    output: bytes

    # ------------------------- helpers / checks -------------------------

    def commit(self) -> bytes:
        """
        Commit to this LightProof with an explicit domain tag. This is useful
        for signatures or inclusion in block headers.
        """
        # When committing, serialize VDFInput/Proof via their own canonical
        # byte representations (they should be bytes-like or expose .to_bytes()).
        vi = self._as_bytes(self.vdf_input)
        vp = self._as_bytes(self.vdf_proof)
        return sha3_256(
            LIGHT_PROOF_COMMIT_TAG
            + _u64be(int(self.round_id))
            + bytes(self.prev_out_hash)
            + vi
            + vp
            + bytes(self.output)
        )

    def sanity_check(self) -> None:
        """Raise ValueError on structural issues."""
        if not isinstance(self.round_id, int):
            raise ValueError("round_id must be an integer")
        if len(self.prev_out_hash) != 32:
            raise ValueError("prev_out_hash must be 32 bytes (SHA3-256)")
        if not isinstance(self.output, (bytes, bytearray)):
            raise ValueError("output must be bytes")
        if len(self.output) == 0:
            raise ValueError("output must be non-empty")

    # ------------------------- serialization -------------------------

    def to_dict(self) -> dict:
        """Codec-agnostic dict; nest VDF types as bytes if available."""
        return {
            "round_id": int(self.round_id),
            "prev_out_hash": self.prev_out_hash.hex(),
            "vdf_input": self._as_hex_or_bytes(self.vdf_input),
            "vdf_proof": self._as_hex_or_bytes(self.vdf_proof),
            "output": self.output.hex(),
        }

    @staticmethod
    def _as_bytes(obj) -> bytes:
        if isinstance(obj, (bytes, bytearray)):
            return bytes(obj)
        # Common pattern for our types
        to_bytes = getattr(obj, "to_bytes", None)
        if callable(to_bytes):
            return to_bytes()
        # Fallback: if it has a .bytes attribute
        b = getattr(obj, "bytes", None)
        if isinstance(b, (bytes, bytearray)):
            return bytes(b)
        raise TypeError(f"Object {type(obj).__name__} is not bytes-like")

    @staticmethod
    def _as_hex_or_bytes(obj):
        try:
            b = LightProof._as_bytes(obj)
            return b.hex()
        except Exception:
            # Last resort: assume it's natively serializable (e.g., dataclass)
            return obj

    # ------------------------- construction -------------------------

    @staticmethod
    def from_beacon(
        *,
        current: BeaconOut,
        prev: Optional[BeaconOut],
        vdf_input: VDFInput,
        vdf_proof: VDFProof,
    ) -> "LightProof":
        """
        Build a LightProof from finalized components. `prev` may be None at the
        very beginning of history (genesis+1), in which case prev_out_hash MUST
        be set to the known anchor hash out-of-band.
        """
        if prev is None:
            raise ValueError("prev BeaconOut required (anchor hash must be explicit upstream)")
        prev_hash = hash_beacon_out_min(prev)
        lp = LightProof(
            round_id=current.round_id,
            prev_out_hash=prev_hash,
            vdf_input=vdf_input,
            vdf_proof=vdf_proof,
            output=bytes(current.output),
        )
        lp.sanity_check()
        return lp


# ----------------------------- verification -----------------------------

def verify_light_proof(
    proof: LightProof,
    *,
    prev: Optional[BeaconOut] = None,
) -> bool:
    """
    Verify a single LightProof.

    If `prev` is supplied:
      - checks hash(prev) == proof.prev_out_hash
      - checks prev.round_id == proof.round_id - 1

    Always:
      - runs the VDF verifier: vdf_verify(vdf_input, vdf_proof, output) → True
    """
    # Structural checks
    proof.sanity_check()

    # Optional hash-chain check
    if prev is not None:
        if int(prev.round_id) != int(proof.round_id) - 1:
            return False
        if hash_beacon_out_min(prev) != proof.prev_out_hash:
            return False

    # VDF verification
    if not vdf_verify(proof.vdf_input, proof.vdf_proof, proof.output):
        return False

    return True


def verify_chain_from_anchor(
    *,
    anchor_prev_out_hash: bytes,
    proofs: Iterable[LightProof],
) -> Tuple[bool, Optional[bytes], Optional[int]]:
    """
    Verify a contiguous chain of LightProofs starting from the *known* anchor
    previous-out hash.

    Parameters
    ----------
    anchor_prev_out_hash : bytes
        The expected previous-output hash for the *first* proof in `proofs`.
        (Typically the hash of a trusted checkpoint's BeaconOut.)
    proofs : Iterable[LightProof]
        Light proofs in ascending round order.

    Returns
    -------
    (ok, last_out_hash, last_round)
        ok             : bool, True if all proofs verified
        last_out_hash  : bytes of the newest BeaconOut hash if ok, else None
        last_round     : int round id of the newest proof if ok, else None
    """
    expected_prev_hash = bytes(anchor_prev_out_hash)
    last_hash: Optional[bytes] = None
    last_round: Optional[int] = None

    prev_round: Optional[int] = None

    for p in proofs:
        p.sanity_check()

        # Monotonic rounds
        if prev_round is not None and int(p.round_id) != prev_round + 1:
            return (False, None, None)

        # Check chaining to expected prev hash
        if p.prev_out_hash != expected_prev_hash:
            return (False, None, None)

        # Verify VDF proof
        if not vdf_verify(p.vdf_input, p.vdf_proof, p.output):
            return (False, None, None)

        # Advance chain
        last_hash = sha3_256(BEACON_OUT_HASH_TAG + _u64be(int(p.round_id)) + bytes(p.output))
        expected_prev_hash = last_hash
        prev_round = int(p.round_id)
        last_round = prev_round

    if last_hash is None:
        # Empty iterator: nothing verified, but trivially true with anchor only.
        return (True, anchor_prev_out_hash, None)

    return (True, last_hash, last_round)


__all__ = [
    "LightProof",
    "hash_beacon_out_min",
    "verify_light_proof",
    "verify_chain_from_anchor",
]
