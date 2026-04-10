from __future__ import annotations

"""
aicf.integration.registry_bridge
--------------------------------

Surface read-only provider registry information to deterministic "view"
queries (e.g., contract-visible calls routed through capabilities/runtime).

This module avoids any nondeterministic I/O and only performs in-process,
read-only lookups against the AICF provider registry. Its outputs are
sanitized dictionaries containing stable fields that are safe to expose
to contracts and light clients.

Design notes
------------
* Duck-typed registry: works with any object exposing common methods:
    - get_provider(provider_id) | get(provider_id) -> Provider-like
    - list_providers(**filters) | query_providers(**filters) -> Iterable
    - count_providers(**filters) -> int
  (A convenience `resolve_registry()` is provided to locate a global one.)
* Stable field set: id, caps, stake, status, region, endpoints, metadata.
  Volatile internals (timestamps, health jitter, nonces) are omitted.
* Capability normalization: returns a small, canonical set like ["AI","QPU"].
* Deterministic sorting: list outputs are sorted by provider id unless the
  caller requests a different, deterministic key.

Typical usage
-------------
    reg = resolve_registry()
    info = get_provider_view(reg, some_id)
    all_ai = list_providers_view(reg, capability="AI", limit=128)

Contract-facing encodings are handled upstream (e.g., CBOR/ABI in
capabilities.runtime.abi_bindings). Here we only return plain Python types.
"""


from typing import (Any, Callable, Dict, Iterable, Iterator, List, Mapping,
                    MutableMapping, Optional, Sequence, Tuple, Union)

# -------- Provider id helpers -----------------------------------------------------

ProviderLike = Any
ProviderIdLike = Union[str, bytes]


def _norm_id_bytes(pid: ProviderIdLike) -> bytes:
    if isinstance(pid, bytes):
        return pid
    if isinstance(pid, str):
        s = pid.strip()
        if s.startswith("0x") or s.startswith("0X"):
            try:
                return bytes.fromhex(s[2:])
            except ValueError:
                pass
        # fall back to UTF-8 (e.g., base58/bech32-like ids)
        return s.encode("utf-8")
    raise TypeError(f"Unsupported provider id type: {type(pid)}")


def _extract_provider_id(obj: ProviderLike) -> bytes:
    if isinstance(obj, (bytes, str)):
        return _norm_id_bytes(obj)
    for attr in ("provider_id", "address", "id"):
        if hasattr(obj, attr):
            val = getattr(obj, attr)
            return _norm_id_bytes(val)
    # last resort: deterministic repr
    return repr(obj).encode("utf-8")


def _hex0(b: bytes) -> str:
    return "0x" + b.hex()


# -------- Capability & status normalization --------------------------------------

# Best-effort normalization across a few common representations.
# If your registry uses a different scheme, adapt CAP_MAP or extend _caps_to_list.

_CAP_BITS = {  # bitmask fallback: 1=AI, 2=QPU
    0: "AI",
    1: "QPU",
}


def _caps_from_bitmask(mask: int) -> List[str]:
    out: List[str] = []
    i = 0
    while mask:
        if mask & 1:
            label = _CAP_BITS.get(i)
            if label:
                out.append(label)
        mask >>= 1
        i += 1
    return out


def _caps_to_list(caps: Any) -> List[str]:
    """
    Normalize various capability encodings into a compact list of strings.
    Accepts: int bitmask, set/list/tuple of strings/enums, mapping of {name: bool},
    or objects with boolean attributes 'ai'/'quantum'.
    """
    if caps is None:
        return []
    # int bitmask
    if isinstance(caps, int):
        return _caps_from_bitmask(caps)
    # mapping of flags
    if isinstance(caps, Mapping):
        out = [
            str(k).upper().replace("QUANTUM", "QPU").replace("AI", "AI")
            for k, v in caps.items()
            if v
        ]
        out.sort()
        return out
    # iterable of names/enums
    if isinstance(caps, (list, set, tuple)):
        norm: List[str] = []
        for x in caps:
            name = getattr(x, "name", None) or str(x)
            name = name.upper()
            if name in ("QUANTUM", "QPU"):
                name = "QPU"
            elif name.startswith("AI"):
                name = "AI"
            norm.append(name)
        norm = sorted(set(norm))
        return norm
    # object with boolean attrs
    flags = []
    if hasattr(caps, "ai") and bool(getattr(caps, "ai")):
        flags.append("AI")
    if hasattr(caps, "quantum") and bool(getattr(caps, "quantum")):
        flags.append("QPU")
    if flags:
        flags.sort()
        return flags
    # fallback
    return [str(caps)]


def _status_to_str(status: Any) -> str:
    # Try enum-like .name, else str()
    name = getattr(status, "name", None)
    if isinstance(name, str):
        return name.upper()
    s = str(status).strip()
    # common forms like "ProviderStatus.ACTIVE"
    if "." in s:
        s = s.split(".")[-1]
    return s.upper()


# -------- Sanitization ------------------------------------------------------------


def build_provider_view(provider: ProviderLike) -> Dict[str, Any]:
    """
    Convert an internal provider object into a stable, contract-safe dict.

    Returned fields:
      id: hex string (0x…)
      caps: list[str] e.g. ["AI","QPU"]
      stake: int (base units)
      status: str e.g. "ACTIVE" | "JAILED" | "COOLDOWN" | "INACTIVE"
      region: Optional[str]
      endpoints: Mapping[str, str] (advertised; purely informational)
      metadata: Optional[Mapping[str, Any]] (sanitized, if present)

    Volatile fields (timestamps, heartbeats, ephemeral scores) are not included.
    """
    pid_hex = _hex0(_extract_provider_id(provider))

    # Pull common attributes defensively
    caps = getattr(provider, "caps", None) or getattr(provider, "capabilities", None)
    stake = (
        getattr(provider, "stake", None)
        or getattr(provider, "effective_stake", None)
        or 0
    )
    status = getattr(provider, "status", None) or "UNKNOWN"
    region = getattr(provider, "region", None)
    endpoints = getattr(provider, "endpoints", None) or {}
    meta = getattr(provider, "metadata", None) or {}

    # Normalize simple shapes
    caps_list = _caps_to_list(caps)
    status_str = _status_to_str(status)

    # Ensure deterministic endpoint mapping of str->str
    if isinstance(endpoints, Mapping):
        ep: Dict[str, str] = {str(k): str(v) for k, v in endpoints.items()}
        # sort keys deterministically when encoding upstream
    else:
        ep = {}

    # Metadata: keep only JSON-friendly, small scalar entries if mapping-like
    if isinstance(meta, Mapping):
        sanitized_meta: Dict[str, Any] = {}
        for k, v in meta.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                sanitized_meta[str(k)] = v
        meta_out: Optional[Dict[str, Any]] = sanitized_meta or None
    else:
        meta_out = None

    # Stake: force int
    try:
        stake_int = int(stake)
    except Exception:
        stake_int = 0

    return {
        "id": pid_hex,
        "caps": caps_list,
        "stake": stake_int,
        "status": status_str,
        "region": None if region is None else str(region),
        "endpoints": ep,
        "metadata": meta_out,
    }


# -------- Registry discovery & wrappers ------------------------------------------


def resolve_registry() -> Any:
    """
    Attempt to locate a process-global provider registry.

    Discovery order:
      1) aicf.registry.registry.REGISTRY (common singleton pattern)
      2) aicf.registry.registry.get_registry() callable
      3) aicf.registry.registry.ProviderRegistry() default ctor

    Returns:
      A registry-like object or raises RuntimeError if none can be constructed.
    """
    try:
        from aicf.registry import registry as _reg  # type: ignore
    except Exception as e:
        raise RuntimeError("aicf.registry.registry module not available") from e

    # 1) Global singleton
    if hasattr(_reg, "REGISTRY"):
        inst = getattr(_reg, "REGISTRY")
        if inst is not None:
            return inst
    # 2) Factory getter
    if hasattr(_reg, "get_registry"):
        gr = getattr(_reg, "get_registry")
        try:
            inst = gr()  # type: ignore[misc]
            if inst is not None:
                return inst
        except TypeError:
            pass
    # 3) Default constructor
    for cls_name in ("ProviderRegistry", "Registry"):
        if hasattr(_reg, cls_name):
            cls = getattr(_reg, cls_name)
            try:
                return cls()  # type: ignore[call-arg]
            except Exception as e:
                raise RuntimeError(
                    f"Failed to construct {_reg.__name__}.{cls_name}"
                ) from e

    raise RuntimeError("Could not resolve a provider registry instance")


def _call_maybe(obj: Any, names: Sequence[str], *args: Any, **kwargs: Any) -> Any:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)(*args, **kwargs)
    raise AttributeError(f"{obj!r} does not implement any of {names}")


# Public, deterministic views


def get_provider_view(
    registry: Any, provider_id: ProviderIdLike
) -> Optional[Dict[str, Any]]:
    """
    Fetch and sanitize a single provider record by id. Returns None if unknown.
    """
    pid_bytes = _norm_id_bytes(provider_id)
    prov = _call_maybe(registry, ("get_provider", "get"), pid_bytes)
    if prov is None:
        return None
    return build_provider_view(prov)


def list_providers_view(
    registry: Any,
    *,
    capability: Optional[str] = None,  # "AI" or "QPU"
    status: Optional[str] = None,  # e.g. "ACTIVE"
    region: Optional[str] = None,
    limit: int = 256,
    offset: int = 0,
    sort_key: str = "id",  # deterministic: "id" | "stake" | "region"
) -> List[Dict[str, Any]]:
    """
    List (a sanitized view of) providers matching simple filters.

    Sorting is deterministic and purely by the chosen key (no randomization).
    """
    filters: Dict[str, Any] = {}
    if capability:
        filters["capability"] = capability.upper().replace("QUANTUM", "QPU")
    if status:
        filters["status"] = status.upper()
    if region:
        filters["region"] = str(region)

    # Prefer list/query methods; fall back to iterating all providers if necessary.
    try:
        iterable: Iterable[Any] = _call_maybe(
            registry, ("list_providers", "query_providers"), **filters
        )
    except AttributeError:
        # Try a generic "all" then filter locally
        iterable = _call_maybe(registry, ("all", "all_providers", "iter_providers"))

        def _matches(p: Any) -> bool:
            if (
                capability
                and "AI" in _caps_to_list(getattr(p, "caps", None))
                and capability == "QPU"
            ):
                pass  # covered below
            caps_ok = (capability is None) or (
                capability in _caps_to_list(getattr(p, "caps", None))
            )
            st_ok = (status is None) or (
                _status_to_str(getattr(p, "status", None)) == status
            )
            reg_ok = (region is None) or (str(getattr(p, "region", None)) == region)
            return caps_ok and st_ok and reg_ok

        iterable = (p for p in iterable if _matches(p))  # type: ignore[assignment]

    views = [build_provider_view(p) for p in iterable]

    # Deterministic sorting
    if sort_key == "id":
        views.sort(key=lambda v: v["id"])
    elif sort_key == "stake":
        views.sort(key=lambda v: (int(v.get("stake", 0)), v["id"]), reverse=True)
    elif sort_key == "region":
        views.sort(key=lambda v: (v.get("region") or "", v["id"]))
    else:
        # unknown key: stable id sort
        views.sort(key=lambda v: v["id"])

    # Pagination (deterministic slicing)
    if offset < 0:
        offset = 0
    if limit <= 0:
        return []
    return views[offset : offset + limit]


def count_providers(
    registry: Any,
    *,
    capability: Optional[str] = None,
    status: Optional[str] = None,
    region: Optional[str] = None,
) -> int:
    """
    Count providers that match the same filters used by list_providers_view.
    """
    filters: Dict[str, Any] = {}
    if capability:
        filters["capability"] = capability.upper().replace("QUANTUM", "QPU")
    if status:
        filters["status"] = status.upper()
    if region:
        filters["region"] = str(region)

    try:
        return int(_call_maybe(registry, ("count_providers", "count"), **filters))
    except AttributeError:
        # Fall back to listing and counting
        return len(
            list_providers_view(
                registry,
                capability=capability,
                status=status,
                region=region,
                limit=10_000_000,
            )
        )


__all__ = [
    "build_provider_view",
    "get_provider_view",
    "list_providers_view",
    "count_providers",
    "resolve_registry",
]
