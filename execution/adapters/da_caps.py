"""
execution.adapters.da_caps — blob cost/size checks (stub, DA-ready)

This module provides a *tiny*, execution-layer–friendly adapter for validating
Data Availability (DA) blob attachments and estimating their gas cost. It is
designed to work even when the `da` package is not installed; if present,
defaults are derived from `da.constants`.

Key responsibilities
--------------------
• Construct a `DaCaps` config from ChainParams or sane defaults.
• Validate per-blob and per-transaction DA limits (size, count, namespaces).
• Estimate gas for blobs with a simple base + per-byte model.
• Keep imports optional so execution/ can run without DA enabled.

Typical usage
-------------
    from execution.adapters.da_caps import (
        DaCaps, caps_from_chain_params, check_and_price_blobs
    )

    caps = caps_from_chain_params(params)  # params may be ChainParams or mapping
    result = check_and_price_blobs(
        blobs=[{"namespace": 24, "size": 16384}, {"namespace": 25, "size": 8192}],
        caps=caps,
    )
    print(result.gas_cost, result.total_bytes)

Notes
-----
• This file is intentionally conservative. It enforces upper-bounds and returns
  a deterministic gas estimate; it does *not* talk to the DA store/network.
• Namespace validity is a numeric range check here. If `da` is installed, the
  defaults reflect `da.constants`; otherwise built-in defaults are used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional, Sequence, TypedDict

# ---- Optional DA integration -------------------------------------------------

_DA_AVAILABLE = False
try:  # pragma: no cover - optional import path
    from da import constants as _da_constants  # type: ignore

    _DA_AVAILABLE = True
except Exception:  # pragma: no cover
    _da_constants = None  # type: ignore


# ---- Defaults (used if `da.constants` is unavailable) ------------------------

# These are safe, conservative fallbacks. Real networks should override via
# ChainParams or ensure `da.constants` is available in the runtime.
_DEFAULT_MAX_BLOB_BYTES = 4 * 1024 * 1024  # 4 MiB per blob
_DEFAULT_MAX_BLOBS_PER_TX = 4  # at most 4 blobs per tx
_DEFAULT_MAX_TOTAL_BLOB_BYTES_PER_TX = 8 * 1024 * 1024  # 8 MiB per tx
_DEFAULT_NAMESPACE_MIN = 0
_DEFAULT_NAMESPACE_MAX = (1 << 32) - 1  # 32-bit namespace space
_DEFAULT_BLOB_BASE_GAS = 2000  # flat overhead per blob
_DEFAULT_BLOB_GAS_PER_BYTE = 2  # cost slope per byte


# ---- Errors ------------------------------------------------------------------


class DaCapsError(Exception):
    """Base error for DA caps validation."""


class BlobTooLarge(DaCapsError):
    pass


class ExcessiveBlobCount(DaCapsError):
    pass


class TotalSizeExceeded(DaCapsError):
    pass


class NamespaceOutOfRange(DaCapsError):
    pass


class InvalidBlobSize(DaCapsError):
    pass


class MissingBlobField(DaCapsError):
    pass


# ---- Types -------------------------------------------------------------------


class BlobLike(TypedDict, total=False):
    """
    Minimal shape required by this adapter.
    - namespace: int   (required)
    - size: int        (required, bytes)
    - commitment: bytes|str  (optional; not used for pricing, present for sanity)
    """

    namespace: int
    size: int
    commitment: bytes


@dataclass(frozen=True)
class DaCaps:
    """
    Limits and cost model used by execution for DA blobs.
    """

    max_blob_bytes: int
    max_blobs_per_tx: int
    max_total_blob_bytes_per_tx: int
    namespace_min: int
    namespace_max: int
    base_gas_per_blob: int
    gas_per_byte: int
    da_enabled: bool = True  # feature flag: allow blobs at all


@dataclass(frozen=True)
class BlobCostResult:
    """
    Outcome of `check_and_price_blobs`.
    """

    count: int
    total_bytes: int
    gas_cost: int


# ---- Public API --------------------------------------------------------------


def caps_from_chain_params(params: Optional[Any] = None) -> DaCaps:
    """
    Build `DaCaps` from a ChainParams-like object or mapping.

    Accepted inputs:
      • Dataclass with attribute `da` (mapping-like) or direct fields under `da`.
      • Plain Mapping[str, Any] with a "da" key.
      • None → use defaults (prefer `da.constants` if importable).

    Recognized keys under the `da` section (snake_case or camelCase accepted):
      max_blob_bytes, maxBlobsPerTx, max_total_blob_bytes_per_tx, namespace_min,
      namespace_max, base_gas_per_blob, gas_per_byte, enabled/da_enabled.

    Returns
    -------
    DaCaps
    """
    # Start with defaults (prefer from da.constants if present)
    caps = _defaults_from_da_constants() if _DA_AVAILABLE else _default_caps()

    if params is None:
        return caps

    # Normalize to mapping
    mapping: Mapping[str, Any]
    if isinstance(params, Mapping):
        mapping = params
    else:
        # Try dataclass/obj with attribute access
        mapping = getattr(params, "__dict__", {}) or _object_to_mapping(params)

    da_section = _get_da_section(mapping)
    if not da_section:
        return caps

    # Helper to read with multiple key styles
    def getk(*names: str, default: Optional[int | bool] = None):
        for n in names:
            if n in da_section:
                return da_section[n]
        return default

    max_blob_bytes = int(
        getk("max_blob_bytes", "maxBlobBytes", default=caps.max_blob_bytes)
    )
    max_blobs_per_tx = int(
        getk("max_blobs_per_tx", "maxBlobsPerTx", default=caps.max_blobs_per_tx)
    )
    max_total = int(
        getk(
            "max_total_blob_bytes_per_tx",
            "maxTotalBlobBytesPerTx",
            default=caps.max_total_blob_bytes_per_tx,
        )
    )
    ns_min = int(getk("namespace_min", "namespaceMin", default=caps.namespace_min))
    ns_max = int(getk("namespace_max", "namespaceMax", default=caps.namespace_max))
    base_gas = int(
        getk("base_gas_per_blob", "baseGasPerBlob", default=caps.base_gas_per_blob)
    )
    gas_per_byte = int(getk("gas_per_byte", "gasPerByte", default=caps.gas_per_byte))
    da_enabled = bool(getk("da_enabled", "enabled", "enable", default=caps.da_enabled))

    return DaCaps(
        max_blob_bytes=max_blob_bytes,
        max_blobs_per_tx=max_blobs_per_tx,
        max_total_blob_bytes_per_tx=max_total,
        namespace_min=ns_min,
        namespace_max=ns_max,
        base_gas_per_blob=base_gas,
        gas_per_byte=gas_per_byte,
        da_enabled=da_enabled,
    )


def check_and_price_blobs(blobs: Sequence[BlobLike], caps: DaCaps) -> BlobCostResult:
    """
    Validate a sequence of blob descriptors under `caps` and compute gas.

    Raises
    ------
    DaCapsError subclass on any violation.
    """
    if not caps.da_enabled:
        if blobs:
            raise DaCapsError("DA is disabled by policy; tx contains blobs.")
        return BlobCostResult(count=0, total_bytes=0, gas_cost=0)

    count = len(blobs)
    if count > caps.max_blobs_per_tx:
        raise ExcessiveBlobCount(
            f"{count} blobs > max_blobs_per_tx={caps.max_blobs_per_tx}"
        )

    total = 0
    gas = 0
    for idx, b in enumerate(blobs):
        _validate_blob_shape(b, idx)
        ns = int(b["namespace"])
        size = int(b["size"])

        if size < 0:
            raise InvalidBlobSize(f"blob[{idx}].size is negative ({size})")
        if size > caps.max_blob_bytes:
            raise BlobTooLarge(
                f"blob[{idx}].size={size} > max_blob_bytes={caps.max_blob_bytes}"
            )
        if ns < caps.namespace_min or ns > caps.namespace_max:
            raise NamespaceOutOfRange(
                f"blob[{idx}].namespace={ns} outside "
                f"[{caps.namespace_min}, {caps.namespace_max}]"
            )

        total += size
        gas += caps.base_gas_per_blob + size * caps.gas_per_byte

    if total > caps.max_total_blob_bytes_per_tx:
        raise TotalSizeExceeded(
            f"total blob bytes {total} > max_total_blob_bytes_per_tx={caps.max_total_blob_bytes_per_tx}"
        )

    return BlobCostResult(count=count, total_bytes=total, gas_cost=gas)


def estimate_gas_for_sizes(sizes: Sequence[int], caps: DaCaps) -> int:
    """
    Convenience: estimate gas given raw sizes (assumes namespaces already ok).
    Still enforces count/size/total limits.
    """
    blobs: list[BlobLike] = [
        {"namespace": caps.namespace_min, "size": int(s)} for s in sizes
    ]
    return check_and_price_blobs(blobs, caps).gas_cost


def da_is_available() -> bool:
    """
    Return True if the optional `da` package was importable at module import time.
    """
    return _DA_AVAILABLE


# ---- Internals ---------------------------------------------------------------


def _defaults_from_da_constants() -> DaCaps:
    # Extract with fallbacks to internal defaults
    def _get(attr: str, default: int) -> int:
        return int(getattr(_da_constants, attr, default))  # type: ignore[attr-defined]

    max_blob = _get("MAX_BLOB_BYTES", _DEFAULT_MAX_BLOB_BYTES)
    max_blobs = _get("MAX_BLOBS_PER_TX", _DEFAULT_MAX_BLOBS_PER_TX)
    max_total = _get(
        "MAX_TOTAL_BLOB_BYTES_PER_TX", _DEFAULT_MAX_TOTAL_BLOB_BYTES_PER_TX
    )
    ns_min = _get("NAMESPACE_MIN", _DEFAULT_NAMESPACE_MIN)
    ns_max = _get("NAMESPACE_MAX", _DEFAULT_NAMESPACE_MAX)
    base = _get("BLOB_BASE_GAS", _DEFAULT_BLOB_BASE_GAS)
    slope = _get("BLOB_GAS_PER_BYTE", _DEFAULT_BLOB_GAS_PER_BYTE)

    return DaCaps(
        max_blob_bytes=max_blob,
        max_blobs_per_tx=max_blobs,
        max_total_blob_bytes_per_tx=max_total,
        namespace_min=ns_min,
        namespace_max=ns_max,
        base_gas_per_blob=base,
        gas_per_byte=slope,
        da_enabled=True,
    )


def _default_caps() -> DaCaps:
    return DaCaps(
        max_blob_bytes=_DEFAULT_MAX_BLOB_BYTES,
        max_blobs_per_tx=_DEFAULT_MAX_BLOBS_PER_TX,
        max_total_blob_bytes_per_tx=_DEFAULT_MAX_TOTAL_BLOB_BYTES_PER_TX,
        namespace_min=_DEFAULT_NAMESPACE_MIN,
        namespace_max=_DEFAULT_NAMESPACE_MAX,
        base_gas_per_blob=_DEFAULT_BLOB_BASE_GAS,
        gas_per_byte=_DEFAULT_BLOB_GAS_PER_BYTE,
        da_enabled=True,
    )


def _get_da_section(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    """
    Try common locations/names for the DA config section inside ChainParams.
    """
    if "da" in mapping and isinstance(mapping["da"], Mapping):
        return mapping["da"]  # type: ignore[return-value]
    # Some structures might nest under 'modules' or 'features'
    for key in ("modules", "features", "capabilities", "params"):
        sub = mapping.get(key)
        if isinstance(sub, Mapping) and "da" in sub and isinstance(sub["da"], Mapping):
            return sub["da"]  # type: ignore[return-value]
    # Dataclass attribute access fallback
    da_attr = getattr(mapping, "da", None)
    if isinstance(da_attr, Mapping):
        return da_attr
    return {}  # empty mapping


def _object_to_mapping(obj: Any) -> Mapping[str, Any]:
    """
    Best-effort conversion of a dataclass/obj to a mapping for config extraction.
    """
    d = {}
    for k in dir(obj):
        if k.startswith("_"):
            continue
        try:
            v = getattr(obj, k)
        except Exception:
            continue
        if isinstance(v, (str, int, bool, dict, list, tuple)):
            d[k] = v
    return d


def _validate_blob_shape(b: BlobLike, idx: int) -> None:
    if "namespace" not in b:
        raise MissingBlobField(f"blob[{idx}] missing 'namespace'")
    if "size" not in b:
        raise MissingBlobField(f"blob[{idx}] missing 'size'")
    try:
        int(b["namespace"])
    except Exception:
        raise NamespaceOutOfRange(f"blob[{idx}].namespace is not an int-like value")
    try:
        int(b["size"])
    except Exception:
        raise InvalidBlobSize(f"blob[{idx}].size is not an int-like value")


__all__ = [
    "DaCaps",
    "BlobLike",
    "BlobCostResult",
    "DaCapsError",
    "BlobTooLarge",
    "ExcessiveBlobCount",
    "TotalSizeExceeded",
    "NamespaceOutOfRange",
    "InvalidBlobSize",
    "MissingBlobField",
    "caps_from_chain_params",
    "check_and_price_blobs",
    "estimate_gas_for_sizes",
    "da_is_available",
]
