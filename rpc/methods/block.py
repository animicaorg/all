from __future__ import annotations

import dataclasses as _dc
import typing as t

from rpc.methods import method
from rpc import deps

# Prefer canonical encoders from core; fall back to CBOR if needed.
try:
    from core.encoding.canonical import (
        header_sign_bytes as _header_sign_bytes,  # type: ignore
        tx_sign_bytes as _tx_sign_bytes,          # type: ignore
    )
except Exception:  # pragma: no cover
    _header_sign_bytes = None  # type: ignore
    _tx_sign_bytes = None      # type: ignore
    try:
        from core.encoding.cbor import dumps as _cbor_dumps  # type: ignore
    except Exception:  # pragma: no cover
        _cbor_dumps = None  # type: ignore

try:
    from core.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib

    def _sha3_256(b: bytes) -> bytes:  # type: ignore
        return hashlib.sha3_256(b).digest()


# -----------------------
# Helpers
# -----------------------

def _dcd(obj: t.Any) -> t.Any:
    """Dataclass → dict (deep)."""
    if _dc.is_dataclass(obj):
        return {k: _dcd(v) for k, v in _dc.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_dcd(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dcd(v) for k, v in obj.items()}
    return obj


def _hex(b: bytes | bytearray | None) -> str | None:
    return None if b is None else "0x" + bytes(b).hex()


def _b(hex_or_bytes: str | bytes | bytearray) -> bytes:
    if isinstance(hex_or_bytes, (bytes, bytearray)):
        return bytes(hex_or_bytes)
    s = hex_or_bytes.lower()
    if s.startswith("0x"):
        s = s[2:]
    return bytes.fromhex(s)


def _compute_header_hash(header: t.Any) -> str | None:
    try:
        if _header_sign_bytes is not None:
            data = _header_sign_bytes(header)
        elif _cbor_dumps is not None:
            data = _cbor_dumps(_dcd(header))
        else:  # pragma: no cover
            return None
        return _hex(_sha3_256(data))
    except Exception:
        return None


def _compute_tx_hash(tx: t.Any) -> str | None:
    try:
        if _tx_sign_bytes is not None:
            data = _tx_sign_bytes(tx)
        elif _cbor_dumps is not None:
            data = _cbor_dumps(_dcd(tx))
        else:  # pragma: no cover
            return None
        return _hex(_sha3_256(data))
    except Exception:
        return None


def _tx_view(tx: t.Any) -> dict[str, t.Any]:
    """Project a Tx dataclass/object into a JSON-friendly view."""
    # Read common fields defensively
    _from = getattr(tx, "sender", getattr(tx, "frm", getattr(tx, "from_", None)))
    to = getattr(tx, "to", None)
    nonce = getattr(tx, "nonce", None)
    gas = getattr(tx, "gas_limit", getattr(tx, "gas", None))
    tip = getattr(tx, "tip", getattr(tx, "gas_price", None))
    value = getattr(tx, "value", 0)
    kind = getattr(tx, "kind", getattr(tx, "tx_kind", None))
    access_list = getattr(tx, "access_list", None)
    data = getattr(tx, "data", getattr(tx, "payload", None))

    v = {
        "hash": _compute_tx_hash(tx),
        "from": _from,
        "to": to,
        "nonce": int(nonce) if nonce is not None else None,
        "gas": int(gas) if gas is not None else None,
        "tip": int(tip) if tip is not None else None,
        "value": int(value) if value is not None else None,
        "kind": kind,
        "data": _hex(data) if isinstance(data, (bytes, bytearray)) else data,
        "accessList": access_list,
    }
    return {k: v for k, v in v.items() if v is not None}


def _log_view(log: t.Any) -> dict[str, t.Any]:
    addr = getattr(log, "address", None)
    topics = getattr(log, "topics", None)
    data = getattr(log, "data", None)
    return {
        "address": addr,
        "topics": [(_hex(t) if isinstance(t, (bytes, bytearray)) else t) for t in (topics or [])],
        "data": _hex(data) if isinstance(data, (bytes, bytearray)) else data,
    }


def _receipt_view(r: t.Any) -> dict[str, t.Any]:
    status = getattr(r, "status", None)
    gas_used = getattr(r, "gas_used", getattr(r, "gasUsed", None))
    logs = getattr(r, "logs", [])
    contract_addr = getattr(r, "contract_address", getattr(r, "contractAddress", None))
    return {
        "status": int(status) if status is not None else None,
        "gasUsed": int(gas_used) if gas_used is not None else None,
        "contractAddress": contract_addr,
        "logs": [_log_view(l) for l in (logs or [])],
    }


def _header_roots_view(roots: t.Any) -> dict[str, t.Any] | None:
    if not roots:
        return None
    if isinstance(roots, dict):
        out: dict[str, t.Any] = {}
        for k, v in roots.items():
            out[k] = _hex(v) if isinstance(v, (bytes, bytearray)) else v
        return out
    return None


def _block_view(
    block: t.Any,
    height: int | None,
    *,
    include_txs: bool,
    include_receipts: bool,
) -> dict[str, t.Any]:
    """Block view with toggles for tx objects & receipts."""
    header = getattr(block, "header", None) or getattr(block, "Header", None) or block  # tolerate shapes
    txs = getattr(block, "txs", getattr(block, "transactions", [])) or []
    receipts = getattr(block, "receipts", []) or []

    parent_hash = getattr(header, "parent_hash", getattr(header, "parentHash", None))
    timestamp = getattr(header, "timestamp", None)
    chain_id = getattr(header, "chain_id", getattr(header, "chainId", None))
    theta_micro = getattr(header, "theta_micro", getattr(header, "thetaMicro", None))
    mix_seed = getattr(header, "mix_seed", getattr(header, "mixSeed", None))
    nonce = getattr(header, "nonce", None)
    roots = getattr(header, "roots", None)

    v: dict[str, t.Any] = {
        "number": int(height) if height is not None else None,
        "hash": _compute_header_hash(header),
        "parentHash": _hex(parent_hash) if isinstance(parent_hash, (bytes, bytearray)) else parent_hash,
        "timestamp": int(timestamp) if timestamp is not None else None,
        "chainId": int(chain_id) if chain_id is not None else None,
        "thetaMicro": int(theta_micro) if theta_micro is not None else None,
        "mixSeed": _hex(mix_seed) if isinstance(mix_seed, (bytes, bytearray)) else mix_seed,
        "nonce": _hex(nonce) if isinstance(nonce, (bytes, bytearray)) else nonce,
        "roots": _header_roots_view(roots),
    }

    if include_txs:
        v["transactions"] = [_tx_view(tx) for tx in txs]
    else:
        # Only hashes
        v["transactions"] = [_compute_tx_hash(tx) for tx in txs]

    if include_receipts:
        v["receipts"] = [_receipt_view(r) for r in receipts]

    # drop None keys
    return {k: val for k, val in v.items() if val is not None}


def _normalize_block_number(n: t.Any) -> int:
    """
    Accepts: int, decimal string, hex string (0x…), or special keywords: 'latest'/'head'/'earliest'
    """
    if isinstance(n, int):
        return n
    if isinstance(n, str):
        s = n.strip().lower()
        if s in ("latest", "head", "safe", "finalized"):  # all map to current best for now
            h, _hdr = deps.get_head()[0], deps.get_head()[1]  # type: ignore
            return int(h)
        if s in ("earliest", "genesis"):
            return 0
        # hex?
        if s.startswith("0x"):
            return int(s, 16)
        return int(s)
    raise TypeError("block number must be int or string")


def _resolve_block_by_number(height: int) -> tuple[int | None, t.Any | None]:
    """
    Returns (height, block) if found, else (None, None).
    deps may expose different shapes; try a few.
    """
    # Try a direct call first
    if hasattr(deps, "get_block_by_height"):
        blk = deps.get_block_by_height(height)  # type: ignore
        if blk is None:
            return None, None
        return height, blk

    # Or via a state service
    if hasattr(deps, "state_service") and hasattr(deps.state_service, "get_block_by_height"):
        blk = deps.state_service.get_block_by_height(height)  # type: ignore
        if blk is None:
            return None, None
        return height, blk

    # Fallback: try to walk from hash index if available
    if hasattr(deps, "get_block_hash_by_height") and hasattr(deps, "get_block_by_hash"):
        h = deps.get_block_hash_by_height(height)  # type: ignore
        if h is None:
            return None, None
        blk = deps.get_block_by_hash(h)  # type: ignore
        return height, blk

    return None, None


def _resolve_block_by_hash(h: str) -> tuple[int | None, t.Any | None]:
    """
    Returns (height, block) if found, else (None, None).
    """
    hb = _b(h)
    # Direct lookup
    if hasattr(deps, "get_block_by_hash"):
        blk = deps.get_block_by_hash(hb)  # type: ignore
        if blk is None:
            return None, None
        # Try to get height if present
        height = getattr(blk, "height", None)
        if height is None and hasattr(deps, "get_height_by_block_hash"):
            height = deps.get_height_by_block_hash(hb)  # type: ignore
        return (int(height) if height is not None else None), blk

    # Via state service
    if hasattr(deps, "state_service") and hasattr(deps.state_service, "get_block_by_hash"):
        blk = deps.state_service.get_block_by_hash(hb)  # type: ignore
        if blk is None:
            return None, None
        height = getattr(blk, "height", None)
        if height is None and hasattr(deps.state_service, "get_height_by_block_hash"):
            height = deps.state_service.get_height_by_block_hash(hb)  # type: ignore
        return (int(height) if height is not None else None), blk

    return None, None


# -----------------------
# Methods
# -----------------------

@method(
    "chain.getBlockByNumber",
    desc="Get a block by number. Params: (number, includeTxObjects: bool=false, includeReceipts: bool=false)",
)
def chain_get_block_by_number(
    number: t.Union[int, str],
    includeTxObjects: bool = False,
    includeReceipts: bool = False,
) -> t.Optional[dict]:
    """
    number can be an int, hex string (0x…), decimal string, or 'latest'/'earliest'.
    Returns a JSON object or null if not found.
    """
    height = _normalize_block_number(number)
    h, blk = _resolve_block_by_number(height)
    if blk is None:
        return None
    return _block_view(blk, h, include_txs=bool(includeTxObjects), include_receipts=bool(includeReceipts))


@method(
    "chain.getBlockByHash",
    desc="Get a block by hash. Params: (hash, includeTxObjects: bool=false, includeReceipts: bool=false)",
)
def chain_get_block_by_hash(
    blockHash: str,
    includeTxObjects: bool = False,
    includeReceipts: bool = False,
) -> t.Optional[dict]:
    """
    blockHash must be a hex string (0x…).
    Returns a JSON object or null if not found.
    """
    h, blk = _resolve_block_by_hash(blockHash)
    if blk is None:
        return None
    return _block_view(blk, h, include_txs=bool(includeTxObjects), include_receipts=bool(includeReceipts))
