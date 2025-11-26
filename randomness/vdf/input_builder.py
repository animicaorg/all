"""
randomness.vdf.input_builder
===========================

Derive the VDF *input seed* deterministically from:
  - the **previous beacon output** (if any),
  - the **current round id**, and
  - the **aggregate reveal** (commit-reveal combiner output).

This module only produces the *input seed bytes* `x_seed` for the VDF.
The Wesolowski verifier/normalizer will map these bytes into the VDF group
(see `randomness.vdf.verifier._normalize_x`), so callers can pass the bytes
directly to the prover/verifier.

Design goals
------------
- **Domain separation**: a dedicated tag and framed inputs.
- **Parameter binding**: we bind to consensus VDF parameters (iterations, and a
  fingerprint of the modulus) so a seed cannot be reused under different params.
- **Deterministic & stable**: no ambient time/IO; purely functional on inputs.

Typical usage
-------------
    from randomness.vdf.input_builder import derive_vdf_input
    from randomness.vdf.verifier import verify_consensus

    x_seed = derive_vdf_input(round_id, prev_beacon, aggregate)
    is_valid = verify_consensus(x_seed, y, pi)

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union, Any

# --- Try to use the shared hashing helpers; fall back to local best-effort ----
try:
    from ..utils.hash import sha3_256 as _sha3_256  # type: ignore
    from ..utils.hash import domain_hash as _domain_hash  # type: ignore
except Exception:  # pragma: no cover - fallback for standalone usage
    import hashlib

    def _sha3_256(*chunks: bytes) -> bytes:
        h = hashlib.sha3_256()
        for c in chunks:
            h.update(c)
        return h.digest()

    def _frame(parts: Tuple[bytes, ...]) -> bytes:
        out = bytearray()
        for p in parts:
            out += len(p).to_bytes(8, "big")
            out += p
        return bytes(out)

    def _domain_hash(tag: bytes, *chunks: bytes) -> bytes:
        # Same framing as elsewhere in the randomness module
        return _sha3_256(b"RAND\x01" + tag, _frame(chunks))


# --- Imports of shared types/params (kept soft to avoid import cycles) --------
try:  # consensus params (iterations, modulus fingerprint)
    from .params import VDFParams, get_params  # type: ignore
except Exception:  # pragma: no cover
    @dataclass(frozen=True)
    class VDFParams:  # minimal stub for type hints
        iterations: int
        modulus_n: int
        backend: str = "rsa"

    def get_params() -> VDFParams:  # type: ignore
        # Obviously not secure; tests may override.
        return VDFParams(iterations=1, modulus_n=65537)


try:  # round/beacon types
    from ..types.core import RoundId, BeaconOut  # type: ignore
except Exception:  # pragma: no cover
    RoundId = int  # type: ignore

    @dataclass(frozen=True)
    class BeaconOut:  # minimal stub
        round_id: int
        output: bytes


# --- Constants ----------------------------------------------------------------
# Prefer the global constants file if present; fall back to local tags.
try:
    from ..constants import DOMAIN_VDF_INPUT  # type: ignore
except Exception:  # pragma: no cover
    DOMAIN_VDF_INPUT = b"vdf_input\x01"


# --- Helpers ------------------------------------------------------------------

def _be_u64(n: int) -> bytes:
    return int(n).to_bytes(8, "big", signed=False)


def _be_nat(n: int) -> bytes:
    if n < 0:
        raise ValueError("expected non-negative integer")
    width = max(1, (n.bit_length() + 7) // 8)
    return n.to_bytes(width, "big")


def _params_fingerprint(p: VDFParams) -> bytes:
    """
    Bind the input to consensus parameters so seeds cannot be reused
    under a different security envelope.
    """
    # We avoid hashing the raw modulus (it can be big); instead hash its digest.
    mod_fp = _sha3_256(_be_nat(p.modulus_n))
    return _domain_hash(b"vdf_params",
                        _be_u64(p.iterations),
                        mod_fp,
                        p.backend.encode("ascii", errors="ignore"))


# --- Public API ----------------------------------------------------------------

def build_input_seed(
    round_id: RoundId,
    aggregate: bytes,
    prev_beacon: Optional[BeaconOut],
    params: Optional[VDFParams] = None,
    extra_salt: bytes = b"",
) -> bytes:
    """
    Build the 32-byte seed for the VDF input.

    Args:
        round_id: Current randomness round identifier.
        aggregate: Output of the commit-reveal aggregator for this round.
        prev_beacon: Previous beacon output (None for genesis).
        params: VDF consensus parameters; if not provided, read from env.
        extra_salt: Optional domain salt for tests or sub-protocols.

    Returns:
        32-byte seed suitable to pass as `x` (bytes) to the VDF prover/verifier.
    """
    p = params or get_params()
    prev_round = 0 if prev_beacon is None else int(prev_beacon.round_id)
    prev_out = b"" if prev_beacon is None else bytes(prev_beacon.output)

    return _domain_hash(
        DOMAIN_VDF_INPUT,
        _be_u64(int(round_id)),
        _be_u64(prev_round),
        prev_out,
        aggregate,
        _params_fingerprint(p),
        extra_salt,
    )


def derive_vdf_input(
    round_id: RoundId,
    prev_beacon: Optional[BeaconOut],
    aggregate: bytes,
    *,
    params: Optional[VDFParams] = None,
    extra_salt: bytes = b"",
) -> bytes:
    """
    Convenience alias for :func:`build_input_seed`.

    Returned bytes are intended to be fed as `x` to the VDF. The consumer
    (prover or verifier) will normalize these bytes into the appropriate
    group element under the consensus modulus.
    """
    return build_input_seed(
        round_id=round_id,
        aggregate=aggregate,
        prev_beacon=prev_beacon,
        params=params,
        extra_salt=extra_salt,
    )


# Backwards-compat alias ------------------------------------------------------
# Older callers and tests import `build_input`; keep it pointing at the seeded
# constructor for clarity.
build_input = build_input_seed


__all__ = [
    "build_input_seed",
    "build_input",
    "derive_vdf_input",
]
