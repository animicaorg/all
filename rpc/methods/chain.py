from __future__ import annotations

import dataclasses as _dc
import typing as t

from rpc import deps
from rpc.methods import method
from rpc.methods.block import (_block_view, _fallback_block,
                               _resolve_block_by_number)

# Optional/loose imports: we compute the head hash using canonical bytes if available.
try:  # preferred, if provided by core
    from core.encoding.canonical import \
        header_sign_bytes as _header_sign_bytes  # type: ignore
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


_ZERO_HASH = "0x" + ("0" * 64)


def _fallback_roots() -> dict[str, str]:
    return {
        "stateRoot": _ZERO_HASH,
        "txsRoot": _ZERO_HASH,
        "receiptsRoot": _ZERO_HASH,
        "proofsRoot": _ZERO_HASH,
        "daRoot": _ZERO_HASH,
    }


def _fallback_header(chain_id: int) -> dict[str, t.Any]:
    return {
        "height": 0,
        "hash": _ZERO_HASH,
        "chainId": chain_id,
        "parentHash": _ZERO_HASH,
        "timestamp": 0,
        "thetaMicro": 0,
        "mixSeed": _ZERO_HASH,
        "nonce": _ZERO_HASH,
        "roots": _fallback_roots(),
    }


def _header_view(
    height: int | None, header: t.Any, chain_id_fallback: int | None = None
) -> dict[str, t.Any]:
    """Project a header dataclass/object into a JSON-friendly view."""
    # Try to read common fields defensively.
    chain_id = getattr(header, "chain_id", getattr(header, "chainId", None))
    theta_micro = getattr(header, "theta_micro", getattr(header, "thetaMicro", None))
    mix_seed = getattr(header, "mix_seed", getattr(header, "mixSeed", None))
    nonce = getattr(header, "nonce", None)

    if isinstance(header, dict):
        chain_id = header.get("chainId", header.get("chain_id", chain_id))
        theta_micro = header.get("thetaMicro", theta_micro)
        mix_seed = header.get("mixSeed", mix_seed)
        nonce = header.get("nonce", nonce)

    roots = getattr(header, "roots", None) or (
        {
            "stateRoot": header.get("stateRoot") if isinstance(header, dict) else None,
            "txsRoot": header.get("txsRoot") if isinstance(header, dict) else None,
            "receiptsRoot": (
                header.get("receiptsRoot") if isinstance(header, dict) else None
            ),
            "proofsRoot": (
                header.get("proofsRoot") if isinstance(header, dict) else None
            ),
            "daRoot": header.get("daRoot") if isinstance(header, dict) else None,
        }
        if isinstance(header, dict)
        else {}
    )
    # Roots may themselves be bytes; map to hex where relevant
    roots_view: dict[str, t.Any] = {}
    if isinstance(roots, dict):
        for k, v in roots.items():
            roots_view[k] = _hex(v) if isinstance(v, (bytes, bytearray)) else v

    height_int = int(height) if height is not None else None

    computed_hash = _compute_header_hash(header)
    if computed_hash is None:
        computed_hash = getattr(header, "hash", None)
        if computed_hash is None and isinstance(header, dict):
            computed_hash = header.get("hash")

    if chain_id is None and chain_id_fallback is not None:
        chain_id = chain_id_fallback

    if not roots_view or any(v is None for v in roots_view.values()):
        roots_view = _fallback_roots()

    hv = {
        "height": height_int,
        "number": height_int,  # alias for clients expecting `number`
        "hash": computed_hash,
        "chainId": int(chain_id) if chain_id is not None else None,
        "thetaMicro": int(theta_micro) if theta_micro is not None else None,
        "mixSeed": (
            _hex(mix_seed) if isinstance(mix_seed, (bytes, bytearray)) else mix_seed
        ),
        "nonce": _hex(nonce) if isinstance(nonce, (bytes, bytearray)) else nonce,
        "roots": roots_view or roots,
    }
    if isinstance(roots_view, dict):
        hv.update(roots_view)
    # Drop Nones for tidiness
    return {k: v for k, v in hv.items() if v is not None}


@method(
    "chain.getParams",
    desc="Return canonical chain/economic/consensus parameters.",
    aliases=("chain_getParams",),
)
def chain_get_params() -> dict:
    """
    Returns the chain parameters loaded by the node (subset of spec/params.yaml).
    """
    params = deps.get_params()  # expected: core.types.params.ChainParams (dataclass)
    return _dataclass_to_dict(params)


@method(
    "chain.getChainId",
    desc="Return the active chainId for this node.",
    aliases=("eth_chainId", "chain_getChainId"),
)
def chain_get_chain_id() -> int:
    """
    Returns the numeric chainId (e.g., 1 for animica mainnet).
    """
    return int(deps.get_chain_id())


@method(
    "chain.getHead",
    desc="Return the current best head (height + header view).",
    aliases=("chain_getHead",),
)
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

    chain_id_val = int(deps.get_chain_id())
    if height is None or header is None:
        h, blk = _resolve_block_by_number(0)
        if blk is None:
            blk = _fallback_block(chain_id_val)
            h = 0
        block_view = _block_view(
            blk,
            h,
            include_txs=False,
            include_receipts=False,
            chain_id_fallback=chain_id_val,
        )
        header_view = block_view.get("header", block_view)
        if isinstance(header_view, dict):
            roots = header_view.get("roots")
            if isinstance(roots, dict):
                merged = dict(header_view)
                merged.update(roots)
                header_view = merged
        return header_view

    view = _header_view(int(height), header, chain_id_fallback=chain_id_val)
    try:
        from rpc.methods import miner as miner_methods

        view["autoMine"] = bool(
            getattr(miner_methods, "auto_mine_enabled", lambda: False)()
        )
    except Exception:
        pass
    return view
