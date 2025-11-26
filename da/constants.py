"""
Animica DA constants.

Spec-level bounds and canonical defaults for the Data Availability subsystem.
These values are intentionally lightweight (no heavy imports) and safe to
import from anywhere.

- Share sizing & erasure parameters
- Blob size limits
- Namespace width and reserved ranges
- Tiny helpers to validate namespace ids

Note: Runtime/network-specific configuration lives in `da.config`. These
constants define *upper/lower bounds* and *defaults* that other modules can
use for validation and reasonable fallbacks.
"""

from __future__ import annotations

from typing import Tuple

try:
    # Small, dependency-free import; used only for a nicer error type.
    from .errors import NamespaceRangeError  # type: ignore
except Exception:  # pragma: no cover
    class NamespaceRangeError(Exception):  # type: ignore
        pass


# ------------------------------ share & erasure ------------------------------

#: Minimum share size in bytes (must be a multiple of SHARE_SIZE_MULTIPLE).
SHARE_SIZE_MIN: int = 256
#: Canonical multiple for share sizes (enforced by config and encoders).
SHARE_SIZE_MULTIPLE: int = 256
#: Default share size in bytes.
SHARE_SIZE_DEFAULT: int = 4096
#: Conservative upper bound for share size (guard rails).
SHARE_SIZE_MAX: int = 1 * 1024 * 1024  # 1 MiB

#: Default Reed–Solomon data shards (k) and total shards (n).
ERASURE_K_DEFAULT: int = 64
ERASURE_N_DEFAULT: int = 128
#: Guard-rail upper bound for total shards.
ERASURE_N_MAX: int = 1024


# ------------------------------- blob limits --------------------------------

#: Default maximum blob size accepted by the system (bytes, pre-encoding).
MAX_BLOB_BYTES_DEFAULT: int = 8 * 1024 * 1024  # 8 MiB
#: Hard safety cap for blob size (absolute upper bound; config must not exceed).
MAX_BLOB_BYTES_HARD_CAP: int = 64 * 1024 * 1024  # 64 MiB
#: Canonical soft limit imported by blob-handling modules (alias for default).
#: Keeping a dedicated name preserves backwards compatibility with callers
#: that expect ``da.constants.MAX_BLOB_BYTES`` to exist.
MAX_BLOB_BYTES: int = MAX_BLOB_BYTES_DEFAULT


# ------------------------------ namespaces ----------------------------------

#: Default namespace id width in bytes (1..8 supported by encoders).
NAMESPACE_ID_BYTES_DEFAULT: int = 2

#: Reserved low range (inclusive) for protocol/system namespaces at 2-byte width.
RESERVED_LOW_2B: Tuple[int, int] = (0x0000, 0x00FF)
#: Reserved high range (inclusive) for future/reserved at 2-byte width.
RESERVED_HIGH_2B: Tuple[int, int] = (0xFF00, 0xFFFF)
#: Default user namespace (first non-reserved id) at 2-byte width.
DEFAULT_USER_NAMESPACE_2B: int = 0x0100


def namespace_max_id(id_bytes: int) -> int:
    """Maximum representable namespace id for the given byte width."""
    if not (1 <= id_bytes <= 8):
        raise NamespaceRangeError("id_bytes must be between 1 and 8")
    return (1 << (8 * id_bytes)) - 1


def is_in_range(x: int, span: Tuple[int, int]) -> bool:
    """Return True if x is within the inclusive span (lo, hi)."""
    lo, hi = span
    return lo <= x <= hi


def is_reserved_namespace(ns: int, *, id_bytes: int = NAMESPACE_ID_BYTES_DEFAULT) -> bool:
    """
    Whether a namespace id falls into the reserved spans for the given width.

    Today only 2-byte ranges are defined. Wider widths are currently treated as
    having no reserved spans (subject to future spec updates).
    """
    if id_bytes == 2:
        return is_in_range(ns, RESERVED_LOW_2B) or is_in_range(ns, RESERVED_HIGH_2B)
    # For widths other than 2 bytes, we haven't defined reserved spans yet.
    return False


def assert_namespace_allowed(ns: int, *, id_bytes: int = NAMESPACE_ID_BYTES_DEFAULT) -> None:
    """
    Validate a namespace id against width and reserved ranges.

    Raises:
        NamespaceRangeError if out of bounds or reserved.
    """
    max_id = namespace_max_id(id_bytes)
    if not (0 <= ns <= max_id):
        raise NamespaceRangeError(f"namespace {ns} out of bounds for {id_bytes}-byte ids (max {max_id})")
    if is_reserved_namespace(ns, id_bytes=id_bytes):
        raise NamespaceRangeError(f"namespace {hex(ns)} is reserved for {id_bytes}-byte ids")


__all__ = [
    # share/erasure
    "SHARE_SIZE_MIN",
    "SHARE_SIZE_MULTIPLE",
    "SHARE_SIZE_DEFAULT",
    "SHARE_SIZE_MAX",
    "ERASURE_K_DEFAULT",
    "ERASURE_N_DEFAULT",
    "ERASURE_N_MAX",
    # blobs
    "MAX_BLOB_BYTES",
    "MAX_BLOB_BYTES_DEFAULT",
    "MAX_BLOB_BYTES_HARD_CAP",
    # namespaces
    "NAMESPACE_ID_BYTES_DEFAULT",
    "RESERVED_LOW_2B",
    "RESERVED_HIGH_2B",
    "DEFAULT_USER_NAMESPACE_2B",
    "namespace_max_id",
    "is_in_range",
    "is_reserved_namespace",
    "assert_namespace_allowed",
]
