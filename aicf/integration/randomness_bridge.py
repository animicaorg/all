from __future__ import annotations
"""
aicf.integration.randomness_bridge
----------------------------------

Deterministic shuffling utilities for provider assignment using a randomness
beacon. This module bridges AICF's dispatcher/assignment logic to the chain's
randomness source so provider selection is unbiased yet reproducible by all
nodes.

Design goals:
  * Deterministic: no reliance on Python's global RNG; pure hash-based ordering.
  * Domain-separated: seeds are derived with a dedicated personalization tag.
  * Epoch-stable: provider order remains fixed within an epoch window to avoid
    flapping; a new order is derived when the epoch advances.
  * Easy integration: either pass a beacon seed directly or supply a getter
    compatible with your environment (e.g., capabilities.adapters.randomness).

Typical usage (inside assignment loop):

    from aicf.integration.randomness_bridge import shuffle_via_beacon, sample_topk

    order = shuffle_via_beacon(
        candidates=candidate_providers,   # Sequence[ProviderId or Provider object]
        height=current_block_height,
        epoch_blocks=64,                  # or from config
        salt=b"AI",                       # optional per-queue salt (b"AI"/b"QPU"/...)
    )

    picked = sample_topk(order, k=max_assignments)

The ordering is computed by sorting candidates on:
    score(p) = SHA3-256(DS || epoch_seed || salt || provider_key(p))
as an integer, ascending. Collisions are practically impossible; if they
occur, the fallback tie-breaker is the provider key bytes lexicographically.

"""


from hashlib import sha3_256
from typing import Any, Callable, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple, Union, overload

# ----- Domain separation & helpers ------------------------------------------------

_DS = b"animica/aicf/assign/v1"

ProviderLike = Any  # str|bytes|object with `.provider_id` or `.address` or `.id`

# Module-local cache for derived epoch seeds (height-independent once epoch fixed)
_epoch_seed_cache: MutableMapping[Tuple[bytes, int], bytes] = {}


def epoch_from_height(height: int, epoch_blocks: int) -> int:
    if epoch_blocks <= 0:
        raise ValueError("epoch_blocks must be positive")
    if height < 0:
        raise ValueError("height must be non-negative")
    return height // epoch_blocks


def _i64(n: int) -> bytes:
    return int(n).to_bytes(8, "big", signed=False)


def derive_epoch_seed(beacon_seed: bytes, epoch: int) -> bytes:
    """
    Deterministically derive an epoch-scoped seed from a beacon seed.

    seed_e = H( DS || b":epoch:" || epoch_be || beacon_seed )
    """
    key = (beacon_seed, epoch)
    cached = _epoch_seed_cache.get(key)
    if cached is not None:
        return cached
    h = sha3_256()
    h.update(_DS)
    h.update(b":epoch:")
    h.update(_i64(epoch))
    h.update(beacon_seed)
    out = h.digest()
    _epoch_seed_cache[key] = out
    return out


def _to_bytes(x: ProviderLike) -> bytes:
    """
    Extract a stable provider key as bytes from various representations.

    Accepted forms:
      - bytes (used as-is)
      - str (UTF-8 encoded)
      - object with attribute in ['provider_id', 'address', 'id'] (str/bytes)
    """
    if isinstance(x, bytes):
        return x
    if isinstance(x, str):
        return x.encode("utf-8")
    for attr in ("provider_id", "address", "id"):
        if hasattr(x, attr):
            v = getattr(x, attr)
            if isinstance(v, bytes):
                return v
            if isinstance(v, str):
                return v.encode("utf-8")
    # Fallback to repr, last resort
    return repr(x).encode("utf-8")


def _score(seed: bytes, salt: Optional[bytes], key_bytes: bytes) -> int:
    h = sha3_256()
    h.update(_DS)
    h.update(b":score:")
    h.update(seed)
    h.update(salt or b"")
    h.update(key_bytes)
    return int.from_bytes(h.digest(), "big", signed=False)


def _stable_unique(seq: Sequence[ProviderLike]) -> List[ProviderLike]:
    """Stable de-duplication by key bytes, preserving first occurrence."""
    seen = set()
    out: List[ProviderLike] = []
    for item in seq:
        kb = _to_bytes(item)
        if kb in seen:
            continue
        seen.add(kb)
        out.append(item)
    return out


def shuffle_with_seed(
    *,
    candidates: Sequence[ProviderLike],
    seed: bytes,
    salt: Optional[Union[bytes, str]] = None,
    dedup: bool = True,
) -> List[ProviderLike]:
    """
    Deterministically permute candidates using the given seed.

    Args:
      candidates: providers or identifiers
      seed: epoch or session seed (bytes)
      salt: optional per-queue salt to produce distinct permutations
      dedup: if True, remove duplicate provider IDs stably

    Returns:
      New list with a deterministic permutation of the input.
    """
    if isinstance(salt, str):
        salt_b = salt.encode("utf-8")
    else:
        salt_b = salt

    base = _stable_unique(candidates) if dedup else list(candidates)

    decorated = [(_score(seed, salt_b, _to_bytes(p)), _to_bytes(p), p) for p in base]
    decorated.sort(key=lambda t: (t[0], t[1]))  # score asc, then key bytes asc (tie-break)
    return [p for _, __, p in decorated]


def sample_topk(permuted: Sequence[ProviderLike], k: int) -> List[ProviderLike]:
    """Take the first k (or all if k >= len)."""
    if k <= 0:
        return []
    if k >= len(permuted):
        return list(permuted)
    return list(permuted[:k])


# ----- Beacon integration ---------------------------------------------------------

# We lazily import an optional beacon adapter if available.
# Expected callable names (first match wins):
#   - get_beacon_seed(height: int) -> bytes
#   - read_beacon(height: int) -> bytes
#   - get_seed_for_height(height: int) -> bytes
def _default_beacon_getter(height: int) -> bytes:
    try:  # pragma: no cover - exercised in integration tests
        from capabilities.adapters import randomness as _rnd  # type: ignore
        for name in ("get_beacon_seed", "read_beacon", "get_seed_for_height"):
            if hasattr(_rnd, name):
                return getattr(_rnd, name)(height)  # type: ignore[misc]
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "No beacon adapter found. Provide a beacon_getter or pass a seed explicitly."
        ) from e
    raise RuntimeError(
        "Beacon adapter loaded but no known getter was found "
        "(tried get_beacon_seed/read_beacon/get_seed_for_height)."
    )


def shuffle_via_beacon(
    *,
    candidates: Sequence[ProviderLike],
    height: int,
    epoch_blocks: int = 64,
    salt: Optional[Union[bytes, str]] = None,
    beacon_getter: Optional[Callable[[int], bytes]] = None,
    dedup: bool = True,
) -> List[ProviderLike]:
    """
    Derive an epoch-scoped seed from the beacon and return a deterministic
    permutation of providers for the given block height.

    Args:
      candidates: providers/ids to order
      height: current block height
      epoch_blocks: number of blocks per assignment epoch
      salt: optional per-queue salt (e.g., b"AI" vs b"QPU")
      beacon_getter: optional function height -> beacon_seed bytes
      dedup: stable de-duplication (default True)

    Returns:
      Deterministically ordered list of candidates for this epoch.
    """
    getter = beacon_getter or _default_beacon_getter
    beacon_seed = getter(height)
    ep = epoch_from_height(height, epoch_blocks)
    ep_seed = derive_epoch_seed(beacon_seed, ep)
    return shuffle_with_seed(candidates=candidates, seed=ep_seed, salt=salt, dedup=dedup)


__all__ = [
    "epoch_from_height",
    "derive_epoch_seed",
    "shuffle_with_seed",
    "shuffle_via_beacon",
    "sample_topk",
]
