"""
Animica • DA • Blob Types

Lightweight, serializable dataclasses used by the Data Availability (DA) layer.

The types here are intentionally minimal and stable across modules so they can be
used by the blob store, commitment helpers, retrieval API, and adapters without
pulling in heavy dependencies.

Key types
---------
- Commitment: canonical commitment to a blob (NMT root + metadata).
- BlobRef:    a compact reference used to look up or fetch a blob.
- BlobMeta:   descriptive metadata stored/indexed alongside the blob.
- Receipt:    a post/acceptance receipt (optionally signed/policy-bound).

Notes
-----
• Namespace IDs are simple integers (unsigned) scoped by policy. Validation of
  ranges should be done at API boundaries; we only do basic sanity checks here.
• All byte-like identifiers are carried as `bytes` internally, with helper
  (de)serializers to/from hex strings prefixed with "0x".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

# Try to import the explicit NamespaceId type; fall back to int for typing-only use.
try:  # pragma: no cover - import convenience
    from ..nmt.namespace import NamespaceId  # type: ignore
except Exception:  # pragma: no cover
    NamespaceId = int  # type: ignore


# --- small local hex helpers (avoid hard dependency on utils at import time) ---- #


def _b2h(b: bytes) -> str:
    return "0x" + b.hex()


def _h2b(s: str) -> bytes:
    s = s.strip()
    if s.startswith(("0x", "0X")):
        s = s[2:]
    return bytes.fromhex(s)


# --------------------------------- Types ----------------------------------- #


@dataclass(frozen=True)
class Commitment:
    """
    Canonical commitment to a blob:

      • namespace      — numeric namespace id of the blob
      • root           — NMT commitment root (e.g., SHA3-256, 32 bytes)
      • size_bytes     — original blob payload size in bytes

    The commitment is what is included/linked in headers as the DA root for
    a bundle of blobs (possibly aggregated).
    """

    namespace: NamespaceId
    root: bytes
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, int) or self.namespace < 0:
            raise ValueError("namespace must be a non-negative integer")
        if not isinstance(self.root, (bytes, bytearray)) or len(self.root) == 0:
            raise ValueError("root must be non-empty bytes")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")

    # --- convenience ---

    @property
    def root_hex(self) -> str:
        return _b2h(self.root)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "namespace": int(self.namespace),
            "root": self.root_hex,
            "sizeBytes": self.size_bytes,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Commitment":
        return Commitment(
            namespace=int(d["namespace"]),
            root=_h2b(d["root"]),
            size_bytes=int(d["sizeBytes"]),
        )


@dataclass(frozen=True)
class BlobRef:
    """
    A compact reference to a blob, sufficient for lookups/fetches.

      • commitment_root — NMT root (same as Commitment.root)
      • namespace       — numeric namespace id (helps fast routing/indexes)
      • size_bytes      — payload size (may be validated against store metadata)

    BlobRef is typically produced by the store/index after a put(), or derived
    from a Commitment when constructing retrieval requests.
    """

    commitment_root: bytes
    namespace: NamespaceId
    size_bytes: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.commitment_root, (bytes, bytearray))
            or len(self.commitment_root) == 0
        ):
            raise ValueError("commitment_root must be non-empty bytes")
        if not isinstance(self.namespace, int) or self.namespace < 0:
            raise ValueError("namespace must be a non-negative integer")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")

    @property
    def root_hex(self) -> str:
        return _b2h(self.commitment_root)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root_hex,
            "namespace": int(self.namespace),
            "sizeBytes": self.size_bytes,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "BlobRef":
        return BlobRef(
            commitment_root=_h2b(d["root"]),
            namespace=int(d["namespace"]),
            size_bytes=int(d["sizeBytes"]),
        )


@dataclass(frozen=True)
class BlobMeta:
    """
    Descriptive metadata stored alongside a blob in the local store/index.

      • namespace    — blob namespace id
      • size_bytes   — payload size in bytes
      • mime         — optional MIME type hint (purely informational)
      • data_shards  — k used during encoding (if available)
      • total_shards — n used during encoding (if available)
      • share_bytes  — size of each share (if available)
    """

    namespace: NamespaceId
    size_bytes: int
    mime: Optional[str] = None
    data_shards: Optional[int] = None
    total_shards: Optional[int] = None
    share_bytes: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, int) or self.namespace < 0:
            raise ValueError("namespace must be a non-negative integer")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if self.data_shards is not None and self.data_shards <= 0:
            raise ValueError("data_shards must be > 0 when provided")
        if self.total_shards is not None and self.total_shards <= 0:
            raise ValueError("total_shards must be > 0 when provided")
        if (
            self.data_shards is not None
            and self.total_shards is not None
            and self.data_shards > self.total_shards
        ):
            raise ValueError("data_shards cannot exceed total_shards")
        if self.share_bytes is not None and self.share_bytes <= 0:
            raise ValueError("share_bytes must be > 0 when provided")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "namespace": int(self.namespace),
            "sizeBytes": self.size_bytes,
            "mime": self.mime,
            "dataShards": self.data_shards,
            "totalShards": self.total_shards,
            "shareBytes": self.share_bytes,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "BlobMeta":
        return BlobMeta(
            namespace=int(d["namespace"]),
            size_bytes=int(d["sizeBytes"]),
            mime=d.get("mime"),
            data_shards=(None if d.get("dataShards") is None else int(d["dataShards"])),
            total_shards=(
                None if d.get("totalShards") is None else int(d["totalShards"])
            ),
            share_bytes=(None if d.get("shareBytes") is None else int(d["shareBytes"])),
        )


@dataclass(frozen=True)
class Receipt:
    """
    Receipt acknowledging the acceptance/storage of a blob.

      • commitment_root  — the committed NMT root
      • namespace        — namespace id of the blob
      • size_bytes       — payload size
      • alg_policy_root  — optional PQ alg-policy Merkle root bound into the receipt
      • signer_address   — optional bech32m address that signed this receipt
      • signature        — optional signature bytes over a domain-separated message

    The exact signing scheme and domain string are enforced at higher layers
    (e.g., adapters/rpc). Here we only carry the bytes and expose helpers for
    (de)serialization.
    """

    commitment_root: bytes
    namespace: NamespaceId
    size_bytes: int
    alg_policy_root: Optional[bytes] = None
    signer_address: Optional[str] = None  # anim1… bech32m
    signature: Optional[bytes] = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.commitment_root, (bytes, bytearray))
            or len(self.commitment_root) == 0
        ):
            raise ValueError("commitment_root must be non-empty bytes")
        if not isinstance(self.namespace, int) or self.namespace < 0:
            raise ValueError("namespace must be a non-negative integer")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")

    @property
    def root_hex(self) -> str:
        return _b2h(self.commitment_root)

    @property
    def policy_root_hex(self) -> Optional[str]:
        return None if self.alg_policy_root is None else _b2h(self.alg_policy_root)

    @property
    def signature_hex(self) -> Optional[str]:
        return None if self.signature is None else _b2h(self.signature)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root_hex,
            "namespace": int(self.namespace),
            "sizeBytes": self.size_bytes,
            "algPolicyRoot": self.policy_root_hex,
            "signer": self.signer_address,
            "signature": self.signature_hex,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Receipt":
        return Receipt(
            commitment_root=_h2b(d["root"]),
            namespace=int(d["namespace"]),
            size_bytes=int(d["sizeBytes"]),
            alg_policy_root=(
                None
                if d.get("algPolicyRoot") in (None, "")
                else _h2b(d["algPolicyRoot"])
            ),
            signer_address=d.get("signer"),
            signature=(
                None if d.get("signature") in (None, "") else _h2b(d["signature"])
            ),
        )


__all__ = [
    "Commitment",
    "BlobRef",
    "BlobMeta",
    "Receipt",
]
