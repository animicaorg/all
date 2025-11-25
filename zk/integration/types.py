"""
zk.integration.types
====================

Typed Zero-Knowledge proof envelope & verifying-key records using **msgspec**.

This module defines:
- `ProofEnvelope`: a canonical, transport-friendly container describing a proof,
  its verifier `kind`, public inputs, and either an embedded VK or a reference
  to a cached VK entry.
- `VkRecord`: the normalized entry stored in `zk/registry/vk_cache.json`.
- `SignatureRecord`: optional signature metadata for VK entries.
- Hashing helpers that **exactly** match the logic in:
    - zk/registry/update_vk.py
    - zk/registry/list_circuits.py

Conventions
-----------
- `kind` enumerates the verifier implementation (e.g., "groth16_bn254",
  "plonk_kzg_bn254", "stark_fri_merkle").
- `vk_ref` refers to a key in `vk_cache.json` (often a circuit id like
  "counter_groth16_bn254@1"). If provided, `vk` MAY be omitted.
- `public_inputs` are field elements or byte-like values expressed as hex
  strings (preferred), decimal strings, integers, or raw bytes. Verifiers
  can normalize them downstream.

Notes
-----
- We intentionally keep `proof` and `vk` schemas open (`dict[str, Any]`) to
  accommodate SnarkJS/PlonkJS/STARK toolchains without tight coupling here.
- All hashing/canonicalization uses JSON with sorted keys and compact separators
  to stay stable across Python versions.

"""

from __future__ import annotations

from enum import Enum
from hashlib import sha3_256
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import msgspec

__all__ = [
    "__version__",
    "Kind",
    "PublicValue",
    "PublicInputs",
    "ProofEnvelope",
    "SignatureRecord",
    "VkRecord",
    "canonical_json_bytes",
    "sha3_256_hex",
    "compute_vk_hash",
]

__version__ = "0.1.0"


# -----------------------------------------------------------------------------
# Basic enums / aliases
# -----------------------------------------------------------------------------

class Kind(str, Enum):
    """Verifier kinds supported by the registry."""
    GROTH16_BN254 = "groth16_bn254"
    PLONK_KZG_BN254 = "plonk_kzg_bn254"
    STARK_FRI_MERKLE = "stark_fri_merkle"


PublicValue = Union[str, int, bytes]
PublicInputs = List[PublicValue]


# -----------------------------------------------------------------------------
# Records
# -----------------------------------------------------------------------------

class ProofEnvelope(msgspec.Struct, frozen=True):
    """
    Canonical proof envelope.

    Fields:
        kind: Verifier kind (see `Kind`), as a string to allow forward-compat.
        proof: Toolchain-specific proof object (e.g., SnarkJS, PlonkJS, FRI/STARK).
        public_inputs: Values exposed to the verifier/transcript (field elems or bytes).
        vk: Embedded verifying key (optional; schema depends on `vk_format`).
        vk_format: Format hint ("snarkjs", "plonkjs", "fri_params", ...), optional.
        vk_ref: Reference key into vk_cache.json (e.g., "counter_groth16_bn254@1").
        meta: Optional free-form metadata (e.g., circuit_id, version, provenance).

    At least one of (`vk`, `vk_ref`) **should** be provided for reproducibility.
    """
    kind: str
    proof: Dict[str, Any]
    public_inputs: Optional[PublicInputs] = None
    vk: Optional[Dict[str, Any]] = None
    vk_format: Optional[str] = None
    vk_ref: Optional[str] = None
    meta: Dict[str, Any] = msgspec.field(default_factory=dict)

    def require_vk_material(self) -> None:
        """Raise if neither `vk` nor `vk_ref` is present."""
        if self.vk is None and (self.vk_ref is None or self.vk_ref == ""):
            raise ValueError("ProofEnvelope requires `vk` or `vk_ref` for verification.")

    def with_public_inputs_hex(self) -> "ProofEnvelope":
        """
        Return a copy with `public_inputs` normalized to hex strings when possible.
        Integers become hex without 0x prefix; bytes become hex; strings are kept.
        """
        if self.public_inputs is None:
            return self
        norm: PublicInputs = []
        for v in self.public_inputs:
            if isinstance(v, int):
                norm.append(format(v, "x"))
            elif isinstance(v, (bytes, bytearray)):
                norm.append(bytes(v).hex())
            else:
                norm.append(v)
        return msgspec.structs.replace(self, public_inputs=norm)  # type: ignore


class SignatureRecord(msgspec.Struct, frozen=True, omit_defaults=True):
    """
    Optional signature metadata stored alongside a VK record.

    Fields:
        alg: "ed25519" or "hmac-sha3-256".
        key_id: Free-form label identifying the signer key.
        signature: Hex-encoded signature over payload(circuit_id, kind, vk_format, vk_hash).
    """
    alg: str
    key_id: str
    signature: str


class VkRecord(msgspec.Struct, frozen=True, omit_defaults=True):
    """
    Normalized Verifying Key cache record (matches zk/registry/vk_cache.json).

    Fields:
        kind: Verifier kind (see `Kind`).
        vk_format: Schema for `vk` ("snarkjs", "plonkjs", "fri_params", ...).
        vk: Structured verifying key (varies by format) — optional for STARK toy.
        fri_params: Alternative/extra params for FRI/STARK toy verifiers.
        vk_hash: Canonical hash computed by `compute_vk_hash` (prefixed "sha3-256:").
        meta: Optional metadata ({circuit, desc, public_inputs, ...}).
        sig: Optional `SignatureRecord` for authenticity/integrity.

    Hash binding
    ------------
    The canonical hash includes **only**:
      - kind
      - vk_format
      - vk
      - fri_params
    """
    kind: str
    vk_format: str
    vk: Optional[Dict[str, Any]] = None
    fri_params: Optional[Dict[str, Any]] = None
    vk_hash: str = ""
    meta: Dict[str, Any] = msgspec.field(default_factory=dict)
    sig: Optional[SignatureRecord] = None


# -----------------------------------------------------------------------------
# Hashing helpers (bit-for-bit compatible with registry tools)
# -----------------------------------------------------------------------------

def canonical_json_bytes(obj: Any) -> bytes:
    """
    Deterministic JSON bytes: sorted keys, compact separators, UTF-8.

    We avoid importing `json` at module import time to keep startup fast;
    msgspec's JSON is also available but we purposely mirror the registry tools'
    behavior (which use Python's stdlib `json`) to ensure bit-for-bit matches.
    """
    import json  # local import to minimize global overhead
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha3_256_hex(data: bytes) -> str:
    return sha3_256(data).hexdigest()


def compute_vk_hash(vk_record: VkRecord) -> str:
    """
    Compute canonical hash over the VK *material*:

        payload = {
          "kind": vk_record.kind,
          "vk_format": vk_record.vk_format,
          "vk": vk_record.vk,
          "fri_params": vk_record.fri_params,
        }

    Returns:
        "sha3-256:<hex>"
    """
    payload = {
        "kind": vk_record.kind,
        "vk_format": vk_record.vk_format,
        "vk": vk_record.vk,
        "fri_params": vk_record.fri_params,
    }
    return f"sha3-256:{sha3_256_hex(canonical_json_bytes(payload))}"
