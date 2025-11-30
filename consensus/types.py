"""
Consensus type aliases and canonical identifiers.

This module defines:
- Stable numeric IDs for proof kinds (used on-wire, in proofs/, and in DA/gossip).
- Lightweight typed aliases for fixed-point / scaled integers used across
  PoIES scoring and difficulty scheduling.

Scaling conventions
-------------------
We avoid floats in consensus-critical code. All real-valued quantities are
encoded as scaled integers with explicit units:

- MicroNat        : 1e-6 * natural-log units  (µ-nats). PoIES ψ and Θ live here.
- PercentMilli    : 1e-3 * percent            (permille-of-percent, i.e., 1e-5 absolute).
- PPM             : parts-per-million (1e-6 absolute).
- Milli           : generic 1e-3 scale (used where noted).

Aliases
-------
- Psi             : alias of MicroNat; contribution from a proof (non-negative).
- ThetaMicro      : alias of MicroNat; acceptance threshold (difficulty-like).
- GammaMicro      : alias of MicroNat; total-Γ cap for a block (sum of ψ caps).

These names are imported throughout consensus/*. Keep this module import-light.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, NewType

# -------------------------
# Fixed-point / scaled ints
# -------------------------

MicroNat = NewType("MicroNat", int)  # µ-nats (1e-6 * natural logs)
Psi = MicroNat  # ψ contribution (non-negative)
ThetaMicro = MicroNat  # Θ micro-threshold
GammaMicro = MicroNat  # Γ cap (sum cap) in µ-nats

PercentMilli = NewType("PercentMilli", int)  # permille-of-percent (1e-5 absolute)
PPM = NewType("PPM", int)  # parts per million (1e-6 absolute)
Milli = NewType("Milli", int)  # generic 1e-3 units (context-specific)

Height = NewType("Height", int)  # block height
Epoch = NewType("Epoch", int)  # retarget or accounting epoch
TypeId = NewType("TypeId", int)  # stable proof type id (wire/CBOR field)

MICRO_SCALE: int = 1_000_000  # µ scale
PPM_SCALE: int = 1_000_000
MILLI_SCALE: int = 1_000

# -------------------------
# Proof kinds (stable IDs)
# -------------------------


class ProofType(IntEnum):
    """
    Canonical proof-type identifiers.

    These IDs are consensus-critical. They must match:
      - proofs/types.py envelopes (type_id field)
      - proofs/registry.py dispatch tables
      - p2p/gossip topic validators for shares
      - spec/schemas/proof_envelope.cddl enumerations

    Do not renumber. Only append at the end when introducing new kinds.
    """

    HASHSHARE = 1  # Useful PoW-like hash share (u-draw) bound to header template
    HASH = 1  # Alias expected by legacy callers/tests
    HASH_SHARE = 1  # Alias with underscore for parity with proofs module
    AI = 2  # AI work proof (TEE attestation + redundancy + traps)
    QUANTUM = 3  # Quantum work proof (provider attest + trap-circuit outcomes)
    STORAGE = 4  # Storage heartbeat / PoSt-style availability proof
    VDF = 5  # Verifiable delay function (bonus / beacon tie-in)


# Bidirectional name ↔ id helpers (stable, lowercase keys)
_PT_BY_NAME: Dict[str, ProofType] = {
    "hashshare": ProofType.HASHSHARE,
    "hash_share": ProofType.HASH_SHARE,
    "hash": ProofType.HASH,
    "ai": ProofType.AI,
    "quantum": ProofType.QUANTUM,
    "storage": ProofType.STORAGE,
    "vdf": ProofType.VDF,
}
_NAME_BY_PT: Dict[ProofType, str] = {v: k for k, v in _PT_BY_NAME.items()}


def proof_type_from_name(name: str) -> ProofType:
    """
    Parse a human/text name into a ProofType.

    Accepts case-insensitive strings. Raises ValueError on unknown names.
    """
    try:
        return _PT_BY_NAME[name.strip().lower()]
    except KeyError as e:  # pragma: no cover - trivial branch
        raise ValueError(f"unknown proof type name: {name!r}") from e


def proof_type_name(pt: ProofType) -> str:
    """Return the canonical lowercase name for a ProofType."""
    return _NAME_BY_PT[ProofType(pt)]


# ---------------------------------------------------------------------------
# Legacy enums
# ---------------------------------------------------------------------------


class ProofKind(IntEnum):
    """
    Legacy/compatibility alias expected by older callers and tests.

    The members intentionally mirror :class:`ProofType` so the enum can be used
    interchangeably in comparisons and lookups.
    """

    HASH = ProofType.HASH
    AI = ProofType.AI
    QUANTUM = ProofType.QUANTUM
    STORAGE = ProofType.STORAGE
    VDF = ProofType.VDF


# -------------------------
# Roots & identifiers
# -------------------------

Hex32 = NewType("Hex32", str)  # '0x' + 64 hex chars
Hex64 = NewType("Hex64", str)  # '0x' + 128 hex chars (when needed)


@dataclass(frozen=True)
class PolicyRoots:
    """
    Merkle roots that bind policy into consensus objects (headers, etc.).
    - poies_policy_root : binds ψ mapping knobs, caps, escort rules, Γ.
    - alg_policy_root   : binds PQ algorithm policy (sig/KEM enablement & thresholds).
    """

    poies_policy_root: Hex32
    alg_policy_root: Hex32


__all__ = [
    # scales & aliases
    "MicroNat",
    "Psi",
    "ThetaMicro",
    "GammaMicro",
    "PercentMilli",
    "PPM",
    "Milli",
    "MICRO_SCALE",
    "PPM_SCALE",
    "MILLI_SCALE",
    # ids & helpers
    "TypeId",
    "Height",
    "Epoch",
    "ProofType",
    "ProofKind",
    "proof_type_from_name",
    "proof_type_name",
    # roots
    "Hex32",
    "Hex64",
    "PolicyRoots",
]
