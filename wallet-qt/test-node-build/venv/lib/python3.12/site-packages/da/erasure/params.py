"""
Animica • DA • Erasure Coding — Parameters

Defines the canonical erasure-coding profile used by the DA layer:
  • (k, n) Reed–Solomon settings (data shards, total shards)
  • shard/“share” payload size (bytes)
  • padding & sizing helpers for deterministic chunking/stripes

Design notes
------------
• Encoding happens in fixed-width *stripes*. Each stripe consists of:
    - k data shards (payload-only slices), each of size `share_bytes`
    - (n - k) parity shards produced by RS over the data shards
  Total shards per stripe = n.

• A blob is partitioned across an integer number of stripes. The final stripe
  is right-padded with zeros up to `k * share_bytes`. The exact *blob length*
  is carried separately in the envelope; the padded bytes have no semantic
  meaning and are not exposed to callers.

• “Share” vs “Shard”: At the NMT layer a “share” is a *leaf* (namespace || len || data).
  At the erasure layer a “shard” refers to the fixed-size payload slice used by RS.
  Encoding/decoding glue takes care of wrapping shards into NMT shares.

This module is intentionally dependency-light and pure; math & validation only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

# Utility --------------------------------------------------------------------


def _ceil_div(a: int, b: int) -> int:
    if b <= 0:
        raise ValueError("b must be positive")
    if a < 0:
        raise ValueError("a must be non-negative")
    return (a + b - 1) // b


# Model ----------------------------------------------------------------------


@dataclass(frozen=True)
class ErasureParams:
    """
    Erasure coding profile.

    Args:
        data_shards: k — number of data shards per stripe (k >= 1)
        total_shards: n — total shards per stripe (n > k), so parity = n - k
        share_bytes: size in bytes of each shard payload (power-of-two recommended)

    Derived:
        parity_shards = total_shards - data_shards
        rate = data_shards / total_shards   (useful for capacity planning)
        stripe_payload_bytes = data_shards * share_bytes
    """

    data_shards: int
    total_shards: int
    share_bytes: int

    # ---- Validation --------------------------------------------------------

    def __post_init__(self) -> None:
        if self.data_shards <= 0:
            raise ValueError("data_shards (k) must be >= 1")
        if self.total_shards <= self.data_shards:
            raise ValueError("total_shards (n) must be > data_shards (k)")
        if self.share_bytes <= 0:
            raise ValueError("share_bytes must be >= 1")
        # Light sanity checks to avoid extreme/unintended settings.
        if self.data_shards > 1024 or self.total_shards > 2048:
            raise ValueError("unreasonably large shard counts; check configuration")
        # A power-of-two share size is strongly encouraged for alignment.
        # Not mandatory, but warn via comment:
        # if (self.share_bytes & (self.share_bytes - 1)) != 0: ...

    # ---- Derived properties ------------------------------------------------

    @property
    def parity_shards(self) -> int:
        return self.total_shards - self.data_shards

    @property
    def rate(self) -> float:
        return self.data_shards / float(self.total_shards)

    @property
    def stripe_payload_bytes(self) -> int:
        """Usable payload per stripe before RS parity is added."""
        return self.data_shards * self.share_bytes

    # ---- Sizing helpers ----------------------------------------------------

    def stripes_for_blob(self, blob_bytes: int) -> int:
        """
        Number of stripes required to carry `blob_bytes` of payload.
        Returns 0 for blob_bytes == 0 (empty blob occupies zero stripes).
        """
        if blob_bytes < 0:
            raise ValueError("blob_bytes must be non-negative")
        if blob_bytes == 0:
            return 0
        return _ceil_div(blob_bytes, self.stripe_payload_bytes)

    def padded_payload_bytes(self, blob_bytes: int) -> int:
        """
        Blob payload size after right-padding to a whole number of stripes.
        """
        return self.stripes_for_blob(blob_bytes) * self.stripe_payload_bytes

    def shards_per_blob(self, blob_bytes: int) -> Tuple[int, int, int]:
        """
        Returns (data_shards_total, parity_shards_total, total_shards_total)
        for a blob of size `blob_bytes` after striping.
        """
        stripes = self.stripes_for_blob(blob_bytes)
        data_total = stripes * self.data_shards
        parity_total = stripes * self.parity_shards
        return data_total, parity_total, data_total + parity_total

    def max_payload_bytes_for_stripes(self, stripes: int) -> int:
        """Maximum payload storable in `stripes` stripes (no partial stripe)."""
        if stripes < 0:
            raise ValueError("stripes must be non-negative")
        return stripes * self.stripe_payload_bytes

    def to_dict(self) -> Dict[str, int]:
        return {
            "data_shards": self.data_shards,
            "total_shards": self.total_shards,
            "parity_shards": self.parity_shards,
            "share_bytes": self.share_bytes,
        }


# Canonical defaults ----------------------------------------------------------

# Rationale:
# • k=32, n=48 (parity=16) gives a 2/3 rate, tolerating up to 16 shard losses per stripe.
# • share_bytes=4096 (4 KiB) aligns with OS pages and keeps small blobs compact:
#     - 4 KiB “small blob” fits in a single shard (still encoded across k for uniformity).
DEFAULT_PARAMS = ErasureParams(
    data_shards=32,
    total_shards=48,
    share_bytes=4096,  # 4 KiB payload per shard
)

__all__ = [
    "ErasureParams",
    "DEFAULT_PARAMS",
]
