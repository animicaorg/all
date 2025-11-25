from __future__ import annotations

import dataclasses as _dc
import typing as t

from rpc.methods import method
from rpc import deps

# Optional/loose imports: we compute the head hash using canonical bytes if available.
try:  # preferred, if provided by core
    from core.encoding.canonical import header_sign_bytes as _header_sign_bytes  # type: ignore
except Exception:  # fallback: CBOR
    _header_sign_bytes = None  # type: ignore
    try:
        from core.encoding.cbor import dumps as _cbor_dumps  # type: ignore
    except Exception:  # pragma: no cover - will raise if absolutely nothing available
        _cbor_dumps = None  # type: ignore

try:
    from core.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:  # pragma: no cover
    # very small local fallback to keep the RPC usable in dev; prefer core.utils.hash
    import hashlib

    def _sha3_256(b: bytes) -> bytes:  # type: ignore
        return hashlib.sha3_256(b).digest()


def _dataclass_to_dict(obj: t.Any) -> t.Any:
    """Safely turn dataclasses into plain dicts (recursively)."""
    if _dc.is_dataclass(obj):
        return {k: _dataclass_to_dict(v) for k, v in _dc.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_dataclass_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj


def _hex(b: bytes | None) -> str | None:
    return None if b is None else "0x" + b.hex()


def _compute_header_hash(header: t.Any) -> str | None:
    """
    Compute a stable hash for the header using canonical sign-bytes if available.
    This is an RPC view hash; consensus-critical hashing should live in core.
    """
    try:
        if _header_sign_bytes is not None:
            data = _header_sign_bytes(header)
        elif _cbor_dumps is not None:
            # Last-resort: CBOR encode the dataclass dict (order should be canonical in our encoder)
            data = _cbor_dumps(_dataclass_to_dict(header))
        else:  # pragma: no cover
            return None
        return _hex(_sha3_256(data))
    except Exception:
        return None


def _header_view(height: int, header: t.Any) -> dict[str, t.Any]:
    """Project a header dataclass/object into a JSON-friendly view."""
    # Try to read common fields defensively.
    chain_id = getattr(header, "chain_id", getattr(header, "chainId", None))
    theta_micro = getattr(header, "theta_micro", getattr(header, "thetaMicro", None))
    mix_seed = getattr(header, "mix_seed", getattr(header, "mixSeed", None))
    nonce = getattr(header, "nonce", None)

    roots = getattr(header, "roots", None) or {}
    # Roots may themselves be bytes; map to hex where relevant
    roots_view: dict[str, t.Any] = {}
    if isinstance(roots, dict):
        for k, v in roots.items():
            roots_view[k] = _hex(v) if isinstance(v, (bytes, bytearray)) else v

    hv = {
        "height": int(height),
        "hash": _compute_header_hash(header),
        "chainId": int(chain_id) if chain_id is not None else None,
        "thetaMicro": int(theta_micro) if theta_micro is not None else None,
        "mixSeed": _hex(mix_seed) if isinstance(mix_seed, (bytes, bytearray)) else mix_seed,
        "nonce": _hex(nonce) if isinstance(nonce, (bytes, bytearray)) else nonce,
        "roots": roots_view or roots,
    }
    # Drop Nones for tidiness
    return {k: v for k, v in hv.items() if v is not None}


@method("chain.getParams", desc="Return canonical chain/economic/consensus parameters.")
def chain_get_params() -> dict:
    """
    Returns the chain parameters loaded by the node (subset of spec/params.yaml).
    """
    params = deps.get_params()  # expected: core.types.params.ChainParams (dataclass)
    return _dataclass_to_dict(params)


@method("chain.getChainId", desc="Return the active chainId for this node.")
def chain_get_chain_id() -> int:
    """
    Returns the numeric chainId (e.g., 1 for animica mainnet).
    """
    return int(deps.get_chain_id())


@method("chain.getHead", desc="Return the current best head (height + header view).")
def chain_get_head() -> dict:
    """
    Returns an object: { height, hash, chainId, thetaMicro, mixSeed, nonce, roots }
    The 'hash' field is computed from canonical sign-bytes when available.
    """
    # deps.get_head() may return:
    #   - (height, header)      OR
    #   - (height, header, hsh) OR
    #   - {"height":..., "header":...}
    snap = deps.get_head()
    if isinstance(snap, dict):
        height = snap.get("height")
        header = snap.get("header")
    elif isinstance(snap, (list, tuple)) and len(snap) >= 2:
        height, header = snap[0], snap[1]
    else:  # pragma: no cover
        raise RuntimeError("deps.get_head() returned an unexpected shape")

    return _header_view(int(height), header)
