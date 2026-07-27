from __future__ import annotations

import dataclasses as _dc
import logging
import time
import typing as t

from core.utils.time import maybe_normalize_unix_timestamp_seconds
from rpc import deps
from rpc.hashrate import difficulty_to_work, work_to_hashshare_rate
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

# Import block_db utilities at module level for efficiency
try:
    from core.db.block_db import PFX_HIX, _from_u64be
    _HAS_BLOCK_DB_UTILS = True
except Exception:
    _HAS_BLOCK_DB_UTILS = False

# Maximum height to scan when doing linear fallback
MAX_LINEAR_SCAN_HEIGHT = 10000

# Logger for chain methods
_log = logging.getLogger("animica.rpc.chain")

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
        if hasattr(header, "hash") and callable(getattr(header, "hash")):
            return _hex(bytes(header.hash()))
        if isinstance(header, dict) and header.get("hash") is not None:
            value = header.get("hash")
            if isinstance(value, (bytes, bytearray)):
                return _hex(value)
            if isinstance(value, str):
                return value
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


_NETWORK_HASHRATE_TTL_SEC = 10.0
_NETWORK_HASHRATE_CACHE: dict[str, t.Any] = {"at": 0.0, "key": None, "payload": None}


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

    roots = getattr(header, "roots", None)
    if not roots:
        derived_roots = {
            "stateRoot": getattr(header, "stateRoot", None),
            "txsRoot": getattr(header, "txsRoot", None),
            "receiptsRoot": getattr(header, "receiptsRoot", None),
            "proofsRoot": getattr(header, "proofsRoot", None),
            "daRoot": getattr(header, "daRoot", None),
        }
        if isinstance(header, dict):
            derived_roots.update(
                {
                    "stateRoot": header.get("stateRoot"),
                    "txsRoot": header.get("txsRoot"),
                    "receiptsRoot": header.get("receiptsRoot"),
                    "proofsRoot": header.get("proofsRoot"),
                    "daRoot": header.get("daRoot"),
                }
            )
        roots = {k: v for k, v in derived_roots.items() if v is not None}
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


def _header_timestamp(header: t.Any) -> int | None:
    ts = getattr(header, "timestamp", None)
    if isinstance(header, dict):
        ts = header.get("timestamp", ts)
    if ts is None:
        return None
    return maybe_normalize_unix_timestamp_seconds(ts)


def _header_theta_micro(header: t.Any) -> int | None:
    theta = getattr(header, "theta_micro", getattr(header, "thetaMicro", None))
    if isinstance(header, dict):
        theta = header.get("thetaMicro", header.get("theta_micro", theta))
    if theta is None:
        return None
    try:
        return int(theta)
    except (TypeError, ValueError):
        return None


def _get_header_by_height(ctx: t.Any, height: int) -> t.Any | None:
    block_db = getattr(ctx, "block_db", None)
    if block_db is None:
        return None
    getter = getattr(block_db, "get_header_by_height", None)
    if callable(getter):
        return getter(int(height))
    block_getter = getattr(block_db, "get_block_by_height", None)
    if callable(block_getter):
        block = block_getter(int(height))
        if block is None:
            return None
        return getattr(block, "header", None) or getattr(block, "Header", None) or block
    return None


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
    "chain.getChainIdentity",
    desc="Return the full chain identity (chainId + genesis/fork/consensus/protocol).",
    aliases=("chain_getChainIdentity",),
)
def chain_get_chain_identity() -> dict:
    """
    Returns an object:
      { chainId, genesisHash, genesisHeaderHash, genesisBlockHash, forkId, consensusId, protocolVersion }.
    """
    return deps.get_chain_identity()


def _scan_for_highest_block() -> t.Tuple[int, t.Any] | None:
    """
    Scan the block database to find the highest block when the head pointer is missing.
    Returns (height, block) or None if no blocks found.
    """
    try:
        ctx = deps.get_ctx()
        block_db = getattr(ctx, "block_db", None)
        if block_db is None:
            _log.warning("block_db not available, cannot scan for highest block")
            return None
        
        # Try to access the KV store directly to scan the height index
        kv = getattr(block_db, "kv", None)
        if kv is not None and hasattr(kv, "iter_prefix") and _HAS_BLOCK_DB_UTILS:
            try:
                max_height = -1
                # Try reverse iteration if supported for better performance
                iter_method = getattr(kv, "iter_prefix_reverse", None)
                if iter_method and callable(iter_method):
                    # If reverse iteration is available, we can get the max quickly
                    for key, _ in iter_method(PFX_HIX):
                        if len(key) >= len(PFX_HIX) + 8:
                            max_height = _from_u64be(key[-8:])
                            break  # First item is the highest
                else:
                    # Fall back to forward iteration
                    for key, _ in kv.iter_prefix(PFX_HIX):
                        if len(key) < len(PFX_HIX) + 8:
                            continue
                        height = _from_u64be(key[-8:])
                        if height > max_height:
                            max_height = height
                
                if max_height >= 0:
                    # Found a block, try to retrieve it
                    h, blk = _resolve_block_by_number(max_height)
                    if blk is not None:
                        _log.info(f"Recovered head at height {max_height} via index scan")
                        return (h, blk)
            except Exception as e:
                _log.warning(f"Index scan failed: {e}, trying exponential search")
        
        # Exponential search strategy: try heights in exponentially decreasing steps
        # This is much faster than linear scan when blocks are sparse
        _log.info(f"Attempting exponential search up to height {MAX_LINEAR_SCAN_HEIGHT}")
        search_heights = [MAX_LINEAR_SCAN_HEIGHT, 1000, 100, 10, 1, 0]
        
        for search_height in search_heights:
            h, blk = _resolve_block_by_number(search_height)
            if blk is not None:
                # Found a block, now scan linearly from here to find the actual max
                _log.info(f"Found block at height {search_height}, scanning forward")
                for height in range(search_height, search_height + 1000):
                    h, blk = _resolve_block_by_number(height)
                    if blk is None:
                        # Previous height was the max
                        if height > search_height:
                            h, blk = _resolve_block_by_number(height - 1)
                            _log.info(f"Recovered head at height {height - 1} via exponential search")
                            return (h, blk)
                        break
                # If we got here, the block at search_height is valid
                _log.info(f"Recovered head at height {search_height} via exponential search")
                return (search_height, blk)
    except Exception as e:
        _log.error(f"Failed to scan for highest block: {e}")
    
    return None


@method(
    "chain.getHead",
    desc="Return the current best head (height + header view).",
    aliases=("chain_getHead",),
)
def chain_get_head() -> dict:
    """
    Returns an object: { height, canonicalHeight, hash, chainId, thetaMicro, mixSeed, nonce, roots }
    The 'hash' field is computed from canonical sign-bytes when available.
    """
    # deps.get_head() may return:
    #   - (height, header)      OR
    #   - (height, header, hsh) OR
    #   - {"height":..., "canonicalHeight":..., "header":...}
    snap = deps.get_head()
    canonical_height = None
    if isinstance(snap, dict):
        height = snap.get("height")
        canonical_height = snap.get("canonicalHeight")
        header = snap.get("header")
    elif isinstance(snap, (list, tuple)) and len(snap) >= 2:
        height, header = snap[0], snap[1]
    else:  # pragma: no cover
        raise RuntimeError("deps.get_head() returned an unexpected shape")

    chain_id_val = int(deps.get_chain_id())
    if height is None or header is None:
        # Try to scan for the highest block instead of falling back to block 0
        scanned = _scan_for_highest_block()
        if scanned is not None:
            h, blk = scanned
        else:
            # Last resort: try block 0
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
    if canonical_height is not None:
        view["canonicalHeight"] = canonical_height
    try:
        from rpc.methods import miner as miner_methods

        view["autoMine"] = bool(
            getattr(miner_methods, "auto_mine_enabled", lambda: False)()
        )
    except Exception:
        pass
    return view


def _network_hashrate_unknown(
    *,
    reason: str,
    window_blocks: int,
    height_end: int | None = None,
    height_start: int | None = None,
) -> dict[str, t.Any]:
    return {
        "hashrate_hsps": None,
        "window_blocks": int(window_blocks),
        "window_seconds": None,
        "height_start": height_start,
        "height_end": height_end,
        "method": "theta_micro_expected_trials",
        "unknown_reason": reason,
    }


@method(
    "chain.getNetworkHashrate",
    desc="Estimate network hashrate over a recent window.",
    aliases=("chain_getNetworkHashrate",),
)
def chain_get_network_hashrate(window_blocks: int | None = None) -> dict[str, t.Any]:
    window_blocks = int(window_blocks or 120)
    if window_blocks <= 0:
        raise ValueError("window_blocks must be > 0")

    head = deps.get_head()
    height_end = head.get("height") if isinstance(head, dict) else None
    if height_end is None:
        return _network_hashrate_unknown(
            reason="no_head", window_blocks=window_blocks, height_end=None
        )
    height_end = int(height_end)
    if height_end < window_blocks - 1:
        return _network_hashrate_unknown(
            reason="insufficient_blocks",
            window_blocks=window_blocks,
            height_end=height_end,
        )

    # Key on the window only, NOT on height_end. Including the head height meant
    # every new block invalidated the cache, so the 10s TTL never applied: each
    # caller after a block (node.getStatus calls this unconditionally) re-walked
    # `window_blocks` headers — ~240 SQLite reads + 120 CBOR decodes — on the
    # asyncio event loop, adding multi-second RPC stalls. A hashrate estimate over
    # a 120-block window is statistical and barely moves block-to-block, so
    # serving it for up to _NETWORK_HASHRATE_TTL_SEC is correct; the payload still
    # reports the height range it was computed over.
    cache_key = (window_blocks,)
    now = time.time()
    if (
        _NETWORK_HASHRATE_CACHE["payload"] is not None
        and _NETWORK_HASHRATE_CACHE["key"] == cache_key
        and (now - float(_NETWORK_HASHRATE_CACHE["at"] or 0.0)) < _NETWORK_HASHRATE_TTL_SEC
    ):
        return t.cast(dict[str, t.Any], _NETWORK_HASHRATE_CACHE["payload"])

    height_start = max(0, height_end - window_blocks + 1)
    ctx = deps.get_ctx()
    total_work = 0.0
    ts_start = None
    ts_end = None
    for height in range(height_start, height_end + 1):
        header = _get_header_by_height(ctx, height)
        if header is None:
            payload = _network_hashrate_unknown(
                reason="missing_header",
                window_blocks=window_blocks,
                height_end=height_end,
                height_start=height_start,
            )
            _NETWORK_HASHRATE_CACHE.update({"at": now, "key": cache_key, "payload": payload})
            return payload
        ts = _header_timestamp(header)
        if ts is None:
            payload = _network_hashrate_unknown(
                reason="missing_timestamp",
                window_blocks=window_blocks,
                height_end=height_end,
                height_start=height_start,
            )
            _NETWORK_HASHRATE_CACHE.update({"at": now, "key": cache_key, "payload": payload})
            return payload
        theta_micro = _header_theta_micro(header)
        if theta_micro is None:
            payload = _network_hashrate_unknown(
                reason="no_difficulty",
                window_blocks=window_blocks,
                height_end=height_end,
                height_start=height_start,
            )
            _NETWORK_HASHRATE_CACHE.update({"at": now, "key": cache_key, "payload": payload})
            return payload
        total_work += difficulty_to_work(theta_micro)
        if ts_start is None:
            ts_start = ts
        ts_end = ts

    if ts_start is None or ts_end is None:
        payload = _network_hashrate_unknown(
            reason="missing_timestamp",
            window_blocks=window_blocks,
            height_end=height_end,
            height_start=height_start,
        )
        _NETWORK_HASHRATE_CACHE.update({"at": now, "key": cache_key, "payload": payload})
        return payload

    dt = max(1.0, float(ts_end - ts_start))
    trials_per_sec = total_work / dt
    payload = {
        "hashrate_hsps": work_to_hashshare_rate(trials_per_sec),
        "window_blocks": int(window_blocks),
        "window_seconds": dt,
        "height_start": height_start,
        "height_end": height_end,
        "method": "theta_micro_expected_trials",
    }
    _NETWORK_HASHRATE_CACHE.update({"at": now, "key": cache_key, "payload": payload})
    return payload


@method(
    "chain.getForks",
    desc="Return fork-choice tips (top competing branches).",
    aliases=("chain_getForks",),
)
def chain_get_forks(limit: int | None = None) -> dict:
    ctx = deps.get_ctx()
    try:
        from core.chain.block_import import fork_choice_snapshot
    except Exception as exc:  # pragma: no cover
        return {"tips": [], "error": str(exc)}
    snap = fork_choice_snapshot(
        ctx.block_db,
        ctx.tx_index,
        genesis_path=getattr(ctx.cfg, "genesis_path", None),
        limit=int(limit or 5),
    )
    return {
        "head": chain_get_head(),
        "tips": snap.get("tips", []),
        **({"error": snap["error"]} if "error" in snap else {}),
    }


@method(
    "chain.getCheckpoints",
    desc="Return built-in checkpoints for the requested chain.",
    aliases=("chain_getCheckpoints",),
)
def chain_get_checkpoints(chain_id: int | None = None) -> dict:
    """
    Returns a list of built-in checkpoints suitable for bootstrap syncing.
    """
    requested_chain_id = int(chain_id) if chain_id is not None else int(deps.get_chain_id())

    from p2p.checkpoints import builtin

    checkpoints = builtin.get_builtin_checkpoints(requested_chain_id)
    return {
        "chainId": requested_chain_id,
        "checkpoints": [
            {"height": checkpoint.height, "hash": checkpoint.hash}
            for checkpoint in checkpoints
        ],
        "source": "builtin",
    }


@method(
    "chain.getBlockByHeight",
    desc="Get a block by height (alias for getBlockByNumber). Params: (height, includeTxObjects: bool=false, includeReceipts: bool=false)",
    aliases=("block_getBlockByHeight",),
)
def chain_get_block_by_height(
    height: t.Union[int, str],
    includeTxObjects: bool = False,
    includeReceipts: bool = False,
    includeTx: bool | None = None,
) -> t.Optional[dict]:
    """
    Alias for chain.getBlockByNumber that explicitly accepts height parameter.
    Accepts int, hex string (0x…), decimal string, or 'latest'/'earliest'.
    Returns a JSON object or null if not found.
    """
    # Import here to avoid circular dependency
    from rpc.methods.block import chain_get_block_by_number
    
    return chain_get_block_by_number(
        height,
        includeTxObjects=includeTxObjects,
        includeReceipts=includeReceipts,
        includeTx=includeTx,
    )
