"""
core.pos — Proof-of-Stake block proofs (Animica hybrid PoW/PoS)
===============================================================

From FORK_POS_MINTING onward a block is valid if it carries **either**:

  * ``workType == 0``  — the existing PoW proof (header hash <= theta target), or
  * ``workType == 1``  — a PoS proof: a signature by the slot's stake-weighted
                         leader, carried in ``header.extra``.

Hybrid by design: if the PoS minter misbehaves, PoW still produces blocks, so a
single-node chain cannot be wedged by a bug on one of the two paths.

Proof wire format (CBOR, nested in ``header.extra``)
----------------------------------------------------
``header.extra`` is ALREADY a CBOR map in production — the block template puts
the coinbase payout commitment there (``{"coinbase": bstr(32)}``). The PoS proof
is therefore nested under a ``"pos"`` key of that same map, never replacing it:

    {
      "coinbase": bstr(32),      # pre-existing, preserved verbatim
      "pos": {
        "v":      1,             # proof format version
        "staker": bstr(32),      # canonical account key of the minter
        "slot":   uint,          # slot this block claims
        "scheme": uint,          # signature scheme id (pinned, see below)
        "pk":     bstr,          # minter public key
        "sig":    bstr,          # signature over the sig-less preimage
      },
    }

The signature covers ``Header.signing_preimage(POS_DOMAIN_TAG)`` computed on the
same header with ``extra`` re-encoded with the proof **minus** its ``sig`` key —
so it commits to every consensus field (height, parentHash, timestamp,
stateRoot, txsRoot, thetaMicro, workType) and to every other entry in ``extra``
(the coinbase commitment included), without self-reference.

Why the scheme is pinned
------------------------
An account key is ``sha3_256(pubkey)`` and does **not** commit to the signature
algorithm (verified: ``account_key_from_pubkey(pk, 4099) == ...(pk, 4098)``).
Accepting an arbitrary ``scheme`` would therefore let any scheme that validates
against the same pubkey bytes mint as that staker. ML-DSA-65 is the only live
scheme on mainnet, so the PoS path accepts exactly that one and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional, Tuple

from .encoding.cbor import cbor_dumps, cbor_loads
from .staking import (
    MIN_STAKE_NANM,
    active_stakers,
    is_bootstrap_leader_set,
    leader_for_slot,
    read_stake,
    slot_for_timestamp,
)
from .utils.address_codec import AccountKeyError, account_key_from_pubkey

WORKTYPE_POW: int = 0
WORKTYPE_POS: int = 1

POS_PROOF_VERSION: int = 1
POS_DOMAIN_TAG: bytes = b"animica/pos/block/v1"

# Key under which the proof is nested inside the header's existing `extra` map,
# alongside entries the template already writes (e.g. "coinbase").
POS_EXTRA_KEY: str = "pos"

# ML-DSA-65 (FIPS 204). See "Why the scheme is pinned" above — do not widen this
# without first making the account key commit to the algorithm id.
POS_ALLOWED_SCHEMES: frozenset[int] = frozenset({11})

# A PoS header carries a ~3.3 KB ML-DSA-65 signature; cap `extra` so a malformed
# or hostile header cannot be used to bloat the chain through this field.
POS_EXTRA_MAX_BYTES: int = 8192


class PosProofError(ValueError):
    """Raised when a PoS proof cannot be decoded."""


@dataclass(frozen=True)
class PosProof:
    staker: bytes
    slot: int
    scheme: int
    pubkey: bytes
    signature: bytes = b""

    def to_obj(self, *, include_sig: bool = True) -> dict:
        obj = {
            "v": POS_PROOF_VERSION,
            "staker": bytes(self.staker),
            "slot": int(self.slot),
            "scheme": int(self.scheme),
            "pk": bytes(self.pubkey),
        }
        if include_sig:
            obj["sig"] = bytes(self.signature)
        return obj

    def encode_into(
        self, base_extra: Any = None, *, include_sig: bool = True
    ) -> bytes:
        """
        Re-encode `base_extra` (the header's existing extra: raw CBOR bytes, an
        already-decoded map, or None) with this proof nested under "pos".

        Every other entry — notably the coinbase commitment the block template
        writes — is preserved byte-for-byte in value.
        """
        m = dict(decode_extra_map(base_extra))
        m[POS_EXTRA_KEY] = self.to_obj(include_sig=include_sig)
        return cbor_dumps(m)

    @staticmethod
    def decode(raw: Any) -> "PosProof":
        """Extract the proof from a header's `extra` (bytes or decoded map)."""
        m = decode_extra_map(raw)
        proof_obj = m.get(POS_EXTRA_KEY)
        if proof_obj is None:
            raise PosProofError("header extra carries no 'pos' entry")
        if not isinstance(proof_obj, dict):
            raise PosProofError("'pos' entry is not a CBOR map")
        if int(proof_obj.get("v", 0)) != POS_PROOF_VERSION:
            raise PosProofError(
                f"unsupported pos proof version {proof_obj.get('v')!r}"
            )
        try:
            staker = bytes(proof_obj["staker"])
            slot = int(proof_obj["slot"])
            scheme = int(proof_obj["scheme"])
            pubkey = bytes(proof_obj["pk"])
            signature = bytes(proof_obj.get("sig", b""))
        except (KeyError, TypeError, ValueError) as exc:
            raise PosProofError(f"pos proof missing/bad field: {exc}") from exc
        if len(staker) != 32:
            raise PosProofError(f"staker must be 32 bytes, got {len(staker)}")
        if not pubkey:
            raise PosProofError("pos proof pubkey empty")
        return PosProof(
            staker=staker,
            slot=slot,
            scheme=scheme,
            pubkey=pubkey,
            signature=signature,
        )


def decode_extra_map(raw: Any) -> dict:
    """
    Normalize a header's `extra` into a CBOR map.

    Accepts raw bytes, an already-decoded mapping, or None (empty header). An
    `extra` that is present but not a CBOR map is an error on the PoS path —
    there would be nowhere to carry the proof without destroying whatever is
    already in it.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise PosProofError(f"header extra has unexpected type {type(raw).__name__}")
    blob = bytes(raw)
    if not blob:
        return {}
    if len(blob) > POS_EXTRA_MAX_BYTES:
        raise PosProofError(
            f"header extra too large ({len(blob)} > {POS_EXTRA_MAX_BYTES})"
        )
    try:
        m = cbor_loads(blob)
    except Exception as exc:
        raise PosProofError(f"header extra is not CBOR: {exc}") from exc
    if not isinstance(m, dict):
        raise PosProofError("header extra is not a CBOR map")
    return dict(m)


def is_pos_header(header: Any) -> bool:
    return int(getattr(header, "workType", 0) or 0) == WORKTYPE_POS


def pos_preimage(header: Any, proof: PosProof, base_extra: Any = None) -> bytes:
    """
    Bytes the minter signs: the header's canonical signing preimage, with `extra`
    re-encoded carrying this proof *without* its signature.

    `base_extra` defaults to the header's own `extra`, which on the verify side
    already contains the signed proof — re-encoding it sig-less reproduces
    exactly what the minter signed, including every sibling entry such as the
    coinbase commitment.
    """
    if base_extra is None:
        base_extra = getattr(header, "extra", b"")
    unsigned_extra = proof.encode_into(base_extra, include_sig=False)
    stub = replace(header, extra=unsigned_extra)
    return stub.signing_preimage(POS_DOMAIN_TAG)


def verify_pos_header(
    header: Any,
    state: Any,
    *,
    target_block_time_s: float,
    timestamp_tolerance_slots: int = 1,
) -> Optional[str]:
    """
    Validate the PoS proof on `header` against `state`.

    Returns None when the header is a valid PoS block, otherwise a short reason
    string (the caller turns that into a BlockImportError). Every check is a
    deterministic function of the header plus committed state, so all nodes
    reach the same verdict on replay.
    """
    from coretx.crypto import verify_signature

    if not is_pos_header(header):
        return "not a pos header"

    extra = bytes(getattr(header, "extra", b"") or b"")
    try:
        proof = PosProof.decode(extra)
    except PosProofError as exc:
        return f"pos proof undecodable: {exc}"

    if not proof.signature:
        return "pos proof unsigned"

    # 1. Scheme must be the single pinned scheme (see module docstring).
    if proof.scheme not in POS_ALLOWED_SCHEMES:
        return f"pos scheme {proof.scheme} not allowed"

    # 2. The staker field is untrusted: derive the account key from the pubkey
    #    the signature will be checked against, exactly as the tx path does.
    try:
        derived = account_key_from_pubkey(proof.pubkey, None)
    except (AccountKeyError, ValueError, TypeError) as exc:
        return f"pos pubkey not derivable to an account: {exc}"
    if derived != proof.staker:
        return "pos staker does not match its pubkey"

    # 3. The claimed slot must match the header timestamp.
    expected_slot = slot_for_timestamp(
        int(getattr(header, "timestamp", 0) or 0), target_block_time_s
    )
    if abs(int(proof.slot) - int(expected_slot)) > int(timestamp_tolerance_slots):
        return (
            f"pos slot {proof.slot} does not match header timestamp "
            f"(expected ~{expected_slot})"
        )

    # 4. The staker must actually be this slot's leader for this parent.
    stakers = active_stakers(state)
    if not stakers:
        return "pos has no active stakers"
    leader = leader_for_slot(state, bytes(header.parentHash), int(proof.slot))
    if leader is None:
        return "pos leader undetermined"
    if leader != proof.staker:
        return "pos staker is not the leader for this slot"

    # 5. Stake floor, re-read rather than trusted from the selection walk.
    #    Exception: while the leader set is the synthetic bootstrap one there is
    #    by definition no bond to read yet — step 4 already pinned the leader to
    #    the single hardcoded bootstrap validator, which is the whole check.
    if not is_bootstrap_leader_set(state):
        rec = read_stake(state, proof.staker)
        if rec.total < MIN_STAKE_NANM:
            return f"pos staker stake {rec.total} below minimum {MIN_STAKE_NANM}"

    # 6. Finally the signature, over the sig-less preimage.
    res = verify_signature(
        int(proof.scheme), pos_preimage(header, proof), proof.signature, proof.pubkey
    )
    ok = bool(getattr(res, "ok", getattr(res, "valid", res)))
    if not ok:
        reason = getattr(res, "reason", None) or getattr(res, "kind", None) or "invalid"
        return f"pos signature invalid: {reason}"

    return None


def build_pos_extra(
    header: Any,
    *,
    staker: bytes,
    slot: int,
    scheme: int,
    pubkey: bytes,
    sign: Any,
) -> bytes:
    """
    Produce the `extra` bytes for a PoS header, preserving whatever the block
    template already put there (the coinbase commitment in particular).

    `sign` is a callable ``(message: bytes) -> bytes`` so the caller keeps the
    secret key; this module never sees or stores one.
    """
    if int(scheme) not in POS_ALLOWED_SCHEMES:
        raise PosProofError(f"pos scheme {scheme} not allowed")
    # The proof must never be signed over a header that already carries one.
    base_extra = dict(decode_extra_map(getattr(header, "extra", b"")))
    base_extra.pop(POS_EXTRA_KEY, None)
    unsigned = PosProof(
        staker=bytes(staker),
        slot=int(slot),
        scheme=int(scheme),
        pubkey=bytes(pubkey),
    )
    signature = bytes(sign(pos_preimage(header, unsigned, base_extra)))
    return replace(unsigned, signature=signature).encode_into(
        base_extra, include_sig=True
    )


__all__ = [
    "WORKTYPE_POW",
    "WORKTYPE_POS",
    "POS_PROOF_VERSION",
    "POS_DOMAIN_TAG",
    "POS_ALLOWED_SCHEMES",
    "POS_EXTRA_MAX_BYTES",
    "POS_EXTRA_KEY",
    "PosProof",
    "PosProofError",
    "decode_extra_map",
    "is_pos_header",
    "pos_preimage",
    "verify_pos_header",
    "build_pos_extra",
]
