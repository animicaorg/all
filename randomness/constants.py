"""
Randomness module constants.

This module centralizes:
- Domain separation tags for commit/reveal/VDF/mix steps
- Default security parameter k (bits) used by failure-probability targets
- Default VDF iteration count (kept in sync with config defaults)
- Buffer/record size guidelines for local I/O and QRNG fetches

These values are intentionally conservative. Networks may override
operational knobs via `randomness.config.RandomnessConfig`, but code that
needs stable compile-time defaults can import from here.
"""

from __future__ import annotations

# -----------------------------
# Domain separation (bytes tags)
# -----------------------------
# Keep these stable; changing them would invalidate historical transcripts.
DOMAIN_PREFIX: bytes = b"animica.rand."

# Commit/reveal phases
DOMAIN_COMMIT: bytes      = DOMAIN_PREFIX + b"commit.v1"
DOMAIN_REVEAL: bytes      = DOMAIN_PREFIX + b"reveal.v1"

# VDF derivation
DOMAIN_VDF_INPUT: bytes   = DOMAIN_PREFIX + b"vdf.input.v1"
DOMAIN_VDF_OUTPUT: bytes  = DOMAIN_PREFIX + b"vdf.output.v1"

# Final beacon mixing (post VDF, optional QRNG mix)
DOMAIN_BEACON_MIX: bytes  = DOMAIN_PREFIX + b"mix.v1"

# Stable round/anchor labeling (for schedulers/light clients)
DOMAIN_ROUND_ID: bytes    = DOMAIN_PREFIX + b"round.id.v1"

# Hash function identifiers used alongside the domains (documentation aid)
HASH_FN_COMMIT_REVEAL: str = "sha3_256"
HASH_FN_VDF_TRANSCRIPT: str = "sha3_256"
HASH_FN_BEACON: str = "sha3_256"

# -----------------------------
# Security parameters
# -----------------------------
# Target security level for beacon failure probability (≈ 2^-k).
# This k is used in analyses (e.g., soundness margins, adversarial success).
SECURITY_PARAMETER_K: int = 128  # bits

# -----------------------------
# VDF defaults (mirror config)
# -----------------------------
# Default iterations for Wesolowski VDF (time hardness). Must match the
# default in randomness.config.VDFParams.iterations.
DEFAULT_VDF_ITERATIONS: int = 1 << 26  # ~67M squarings; tune per network

# Recommended RSA modulus size for the VDF group (bits)
VDF_MIN_MODULUS_BITS: int = 1024
VDF_RECOMMENDED_MODULUS_BITS: int = 2048

# -----------------------------
# Buffer sizes / record limits
# -----------------------------
# Chunk sizes used for local file I/O; keep power-of-two for efficiency.
FILE_IO_CHUNK_SIZE: int = 64 * 1024     # 64 KiB
# Chunk sizes for network-bound reads (QRNG, remote stores)
NET_IO_CHUNK_SIZE: int = 32 * 1024      # 32 KiB

# Upper bounds for small payloads used in the randomness pipeline.
# These are guard-rails, not protocol limits.
QRNG_FETCH_MAX_BYTES: int = 4096        # max bytes pulled per QRNG request
BEACON_RECORD_MAX_BYTES: int = 4096     # max serialized beacon record kept hot
TRANSCRIPT_MAX_BYTES: int = 8192        # defensive cap for transcript blobs

__all__ = [
    # Domains
    "DOMAIN_PREFIX",
    "DOMAIN_COMMIT",
    "DOMAIN_REVEAL",
    "DOMAIN_VDF_INPUT",
    "DOMAIN_VDF_OUTPUT",
    "DOMAIN_BEACON_MIX",
    "DOMAIN_ROUND_ID",
    # Hash names
    "HASH_FN_COMMIT_REVEAL",
    "HASH_FN_VDF_TRANSCRIPT",
    "HASH_FN_BEACON",
    # Security / VDF
    "SECURITY_PARAMETER_K",
    "DEFAULT_VDF_ITERATIONS",
    "VDF_MIN_MODULUS_BITS",
    "VDF_RECOMMENDED_MODULUS_BITS",
    # Buffers / sizes
    "FILE_IO_CHUNK_SIZE",
    "NET_IO_CHUNK_SIZE",
    "QRNG_FETCH_MAX_BYTES",
    "BEACON_RECORD_MAX_BYTES",
    "TRANSCRIPT_MAX_BYTES",
]
