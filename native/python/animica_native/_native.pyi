from __future__ import annotations

"""
Type stubs for the compiled PyO3 extension module ``_animica_native``.

This file documents the Python-visible API surface that the Rust core exposes.
Downstream users can rely on these hints for static type checking (mypy/pyright)
and editor auto-completion, while the actual implementation lives in the
native extension.

The module typically lives at ``animica_native._animica_native`` when installed
as a wheel, but is imported in Python as ``animica_native._native`` via the
package's ``__init__`` loader shim.
"""

from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    TypedDict,
    Union,
)

# ---------------------------------------------------------------------------
# Common aliases
# ---------------------------------------------------------------------------

#: Accepts any Python "readable buffer": ``bytes``, ``bytearray``, or ``memoryview``.
Buffer = Union[bytes, bytearray, memoryview]

#: Namespace identifier used by the Namespaced Merkle Tree (NMT). In practice
#: this is commonly an 8-byte big-endian integer, but the binding accepts both
#: raw bytes and Python ``int`` for convenience.
Namespace = Union[int, bytes]


# ---------------------------------------------------------------------------
# Version / top-level helpers
# ---------------------------------------------------------------------------

def version() -> Tuple[int, int, int]:
    """
    Return semantic version of the native core as ``(major, minor, patch)``.
    """

def version_tuple() -> Tuple[int, int, int]: ...
def version_string() -> str: ...
__version__: str

# Some builds also export a convenience CPU features function at top-level.
def cpu_features() -> "CpuFeatures": ...


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

class _StreamingHash(Protocol):
    """
    Streaming hash protocol used by the concrete hasher implementations below.
    """

    digest_size: int
    block_size: int

    def update(self, data: Buffer, /) -> None: ...
    def digest(self) -> bytes: ...
    def hexdigest(self) -> str: ...
    def reset(self) -> None: ...
    def clone(self) -> "_StreamingHash": ...


class Blake3Hasher(_StreamingHash, Protocol):
    """Streaming BLAKE3 hasher (32-byte digests)."""
    digest_size: int
    block_size: int


class Keccak256Hasher(_StreamingHash, Protocol):
    """Streaming Keccak-256 hasher (32-byte digests)."""
    digest_size: int
    block_size: int


class Sha256Hasher(_StreamingHash, Protocol):
    """Streaming SHA-256 hasher (32-byte digests)."""
    digest_size: int
    block_size: int


class _HashModule(Protocol):
    """
    Namespaced submodule providing fast hashing primitives and streaming APIs.
    """

    # One-shot helpers (bytes in → 32 bytes out)
    def blake3_hash(self, data: Buffer, /) -> bytes: ...
    def keccak256(self, data: Buffer, /) -> bytes: ...
    def sha256(self, data: Buffer, /) -> bytes: ...

    # Streaming constructors
    def Blake3(self) -> Blake3Hasher: ...
    def Keccak256(self) -> Keccak256Hasher: ...
    def Sha256(self) -> Sha256Hasher: ...


# Available as attribute: ``_animica_native.hash``
hash: _HashModule


# ---------------------------------------------------------------------------
# Namespaced Merkle Trees (NMT)
# ---------------------------------------------------------------------------

class NmtProof(TypedDict, total=False):
    """
    Minimal shape for an NMT proof object.

    Notes
    -----
    The exact structure is intentionally flexible to accommodate evolution
    of the native verification logic. Fields present in current builds:

    - ``side_nodes``: Merkle path as a list of sibling node hashes (left→right).
    - ``start`` / ``end``: Range covered by the proof (inclusive / exclusive).
    - ``leaf_index``: Index of the proven leaf within the commitment.
    - ``namespace``: Namespace id for the proven leaf (bytes or int).
    """
    side_nodes: Sequence[bytes]
    start: int
    end: int
    leaf_index: int
    namespace: Namespace


class _NmtModule(Protocol):
    """
    Namespaced Merkle Tree helpers.
    """

    def nmt_root(
        self,
        leaves: Sequence[Tuple[Namespace, Buffer]],
        /,
        *,
        ns_size: int = ...,
    ) -> bytes:
        """
        Compute the NMT root for a list of namespaced leaves.

        Parameters
        ----------
        leaves:
            Sequence of ``(namespace, data)`` pairs. ``namespace`` may be an
            ``int`` or raw bytes (big-endian). ``data`` is a buffer.
        ns_size:
            Namespace size in bytes (typically 8). Defaults to the build’s
            compiled width when omitted.
        """

    def nmt_verify(
        self,
        proof: NmtProof,
        leaf: Tuple[Namespace, Buffer],
        root: Buffer,
        /,
        *,
        ns_size: int = ...,
    ) -> bool:
        """
        Verify a single-leaf NMT inclusion/range proof against ``root``.
        Returns ``True`` if valid.
        """

    # Some builds may also expose a range-opening helper:
    def nmt_open(
        self,
        leaves: Sequence[Tuple[Namespace, Buffer]],
        start: int,
        end: int,
        /,
        *,
        ns_size: int = ...,
    ) -> NmtProof: ...


# Available as attribute: ``_animica_native.nmt`` (may be absent if not built)
nmt: _NmtModule


# ---------------------------------------------------------------------------
# Reed–Solomon (erasure coding)
# ---------------------------------------------------------------------------

class _RsModule(Protocol):
    """
    Erasure coding helpers built atop optimized GF(2^8) implementations.
    """

    def rs_encode(self, data: Buffer, data_shards: int, parity_shards: int, /) -> List[bytes]:
        """
        Split ``data`` into ``data_shards`` and produce ``parity_shards`` parity shards.
        Returns a list of length ``data_shards + parity_shards`` with fixed-size shards.
        """

    def rs_reconstruct(
        self,
        shards: Sequence[Optional[Buffer]],
        data_shards: int,
        parity_shards: int,
        /,
    ) -> bytes:
        """
        Reconstruct the original payload from a set of shards where missing shards
        are provided as ``None``. Returns the original byte payload (padding removed).
        """

    def rs_verify(
        self,
        shards: Sequence[Optional[Buffer]],
        data_shards: int,
        parity_shards: int,
        /,
    ) -> bool:
        """Return ``True`` if the provided shard set passes parity checks."""


# Available as attribute: ``_animica_native.rs`` (may be absent if not built)
rs: _RsModule


# ---------------------------------------------------------------------------
# CPU features
# ---------------------------------------------------------------------------

class CpuFeatures(TypedDict, total=False):
    """
    CPU feature flags exposed by the native runtime detector.
    Keys may vary by platform/architecture.
    """
    x86_avx2: bool
    x86_sha: bool
    arm_neon: bool
    arm_sha3: bool


class _CpuModule(Protocol):
    def features(self) -> CpuFeatures: ...
    def get_features(self) -> CpuFeatures: ...


# Available as attribute: ``_animica_native.cpu`` (may be absent)
cpu: _CpuModule


# ---------------------------------------------------------------------------
# Utils (rarely used directly; primarily for internal plumbing)
# ---------------------------------------------------------------------------

class _UtilsModule(Protocol):
    def cpu_features(self) -> CpuFeatures: ...
    # Additional zero-copy helpers may exist in some builds but are not typed here.


# Available as attribute: ``_animica_native.utils`` (may be absent)
utils: _UtilsModule


# ---------------------------------------------------------------------------
# Fallbacks / internal
# ---------------------------------------------------------------------------

# The native module may expose additional symbols. We intentionally avoid
# over-specifying them here to keep the stub forward-compatible.
# Unknown attributes resolve to ``Any`` for type checkers.
def __getattr__(name: str) -> Any: ...
