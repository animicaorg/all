from __future__ import annotations

import dataclasses as _dc
import typing as t

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method

# ——— Optional deps (be tolerant during early bring-up) ———

# CBOR codec (canonical, from core)
try:
    from core.encoding.cbor import dumps as _cbor_dumps
    from core.encoding.cbor import loads as _cbor_loads  # type: ignore
except Exception as _e:  # pragma: no cover
    _cbor_loads = None  # type: ignore
    _cbor_dumps = None  # type: ignore

# Canonical SignBytes encoders (preferred)
try:
    from core.encoding.canonical import \
        tx_sign_bytes as _tx_sign_bytes  # type: ignore
except Exception:  # pragma: no cover
    _tx_sign_bytes = None  # type: ignore

# Tx dataclass (optional; we can operate on dicts too)
try:
    from core.types.tx import Tx as _Tx  # type: ignore
except Exception:  # pragma: no cover
    _Tx = None  # type: ignore

# Hashing
try:
    from core.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib

    def _sha3_256(b: bytes) -> bytes:  # type: ignore
        return hashlib.sha3_256(b).digest()


# Pending pool (strongly preferred)
_PEND = None
try:
    from rpc.pending_pool import pool as _PEND  # type: ignore
except Exception:  # pragma: no cover
    _PEND = None  # type: ignore

# PQ verify
try:
    from pq.py import verify as _pq_verify  # type: ignore
except Exception:  # pragma: no cover
    _pq_verify = None  # type: ignore


# ——— Local fallback pending store (development only) ———
# Map tx_hash_hex → raw_tx_bytes
_FALLBACK_PENDING: dict[str, bytes] = {}


# ——— Helpers ———


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


def _b(x: str | bytes | bytearray) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    s = x.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    return bytes.fromhex(s)


def _compute_tx_hash(tx_like: t.Any) -> str:
    """
    Compute tx hash from canonical SignBytes (preferred) or canonical CBOR fallback.
    """
    try:
        if _tx_sign_bytes is not None:
            sb = _tx_sign_bytes(tx_like)
        else:
            # Fallback: dump dict without signature field
            if _cbor_dumps is None:
                raise RuntimeError("No CBOR encoder available")
            if _dc.is_dataclass(tx_like):
                obj = _dcd(tx_like)
            else:
                obj = dict(tx_like)
            for k in ("sig", "signature"):
                obj.pop(k, None)
            sb = _cbor_dumps(obj)
        return _hex(_sha3_256(sb)) or ""  # type: ignore[return-value]
    except Exception as e:  # pragma: no cover
        raise rpc_errors.InternalError(f"tx hash failed: {e}")


def _sign_bytes(tx_like: t.Any) -> bytes:
    if _tx_sign_bytes is not None:
        return _tx_sign_bytes(tx_like)
    if _cbor_dumps is None:
        raise rpc_errors.InternalError("No canonical encoder for SignBytes")
    if _dc.is_dataclass(tx_like):
        obj = _dcd(tx_like)
    else:
        obj = dict(tx_like)
    for k in ("sig", "signature"):
        obj.pop(k, None)
    return _cbor_dumps(obj)


def _chain_id_required() -> int:
    # Prefer deps.get_chain_params(), else deps.chain_id, else config
    if hasattr(deps, "get_chain_params"):
        cp = deps.get_chain_params()  # type: ignore
        cid = getattr(cp, "chain_id", getattr(cp, "chainId", None))
        if cid is not None:
            return int(cid)
    if hasattr(deps, "chain_id"):
        return int(getattr(deps, "chain_id"))  # type: ignore
    if hasattr(deps, "config") and hasattr(deps.config, "chain_id"):
        return int(deps.config.chain_id)  # type: ignore
    # Fallback mainnet id
    return 1


def _extract_sig(obj: dict) -> tuple[int, bytes, bytes]:
    """
    Extract (alg_id, pubkey, signature) from obj["sig"] or obj["signature"].
    Supports hex strings or raw bytes.
    """
    sig = obj.get("sig") or obj.get("signature")
    if not isinstance(sig, dict):
        raise rpc_errors.InvalidParams("Missing 'sig' object")
    alg_id = sig.get("algId") or sig.get("alg_id") or sig.get("alg")
    if alg_id is None:
        raise rpc_errors.InvalidParams("Missing 'sig.algId'")
    # Allow str or int for alg_id
    if isinstance(alg_id, str):
        try:
            alg_id = int(alg_id, 0)
        except Exception:
            # leave as str; pq.verify may accept names
            pass
    pub = sig.get("pubkey") or sig.get("pub") or sig.get("pk")
    s = sig.get("sig") or sig.get("signature")
    if pub is None or s is None:
        raise rpc_errors.InvalidParams("Missing 'sig.pubkey' or 'sig.sig'")
    return (
        alg_id,
        _b(pub) if isinstance(pub, str) else bytes(pub),
        _b(s) if isinstance(s, str) else bytes(s),
    )


def _validate_chain_id(obj: dict) -> None:
    want = _chain_id_required()
    cid = obj.get("chainId") or obj.get("chain_id")
    if cid is None:
        # Some txs rely on external chainId; enforce explicit for now.
        raise rpc_errors.ChainIdMismatch(
            f"Transaction missing chainId (required {want})"
        )
    if int(cid) != int(want):
        raise rpc_errors.ChainIdMismatch(f"chainId mismatch: tx={cid} node={want}")


def _verify_pq_signature(tx_like: t.Any, obj: dict) -> None:
    if _pq_verify is None:
        raise rpc_errors.InternalError("PQ verification unavailable")
    alg_id, pub, sig = _extract_sig(obj)
    msg = _sign_bytes(tx_like)
    ok = _pq_verify.verify(alg_id=alg_id, pubkey=pub, message=msg, signature=sig)  # type: ignore
    if not ok:
        raise rpc_errors.InvalidTx("Post-quantum signature verification failed")


def _decode_tx(raw: bytes) -> tuple[t.Any, dict]:
    if _cbor_loads is None:
        raise rpc_errors.InternalError("CBOR decoder unavailable")
    obj = _cbor_loads(raw)
    if _Tx is not None:
        try:
            # Try friendly constructors if present
            if hasattr(_Tx, "from_dict"):
                tx = _Tx.from_dict(obj)  # type: ignore[attr-defined]
            else:
                tx = _Tx(**obj)  # type: ignore[call-arg]
            return tx, obj
        except Exception:
            # Fall back to dict shape
            pass
    if not isinstance(obj, dict):
        raise rpc_errors.InvalidParams("CBOR did not decode to a Tx object")
    return obj, obj


def _tx_view(
    tx: t.Any,
    obj: dict,
    *,
    pending: bool,
    block_hash: bytes | None = None,
    block_number: int | None = None,
    tx_index: int | None = None,
) -> dict:
    _from = obj.get("from") or obj.get("sender") or getattr(tx, "sender", None)
    to = obj.get("to", getattr(tx, "to", None))
    nonce = obj.get("nonce", getattr(tx, "nonce", None))
    gas = obj.get("gas", obj.get("gasLimit", getattr(tx, "gas_limit", None)))
    tip = obj.get("tip", obj.get("gasPrice", getattr(tx, "tip", None)))
    value = obj.get("value", getattr(tx, "value", 0))
    data = obj.get("data", obj.get("payload", getattr(tx, "data", None)))
    hash_hex = _compute_tx_hash(tx if _dc.is_dataclass(tx) else obj)
    v = {
        "hash": hash_hex,
        "from": _from,
        "to": to,
        "nonce": int(nonce) if nonce is not None else None,
        "gas": int(gas) if gas is not None else None,
        "tip": int(tip) if tip is not None else None,
        "value": int(value) if value is not None else None,
        "data": _hex(data) if isinstance(data, (bytes, bytearray)) else data,
        "blockHash": (
            None
            if pending
            else (
                _hex(block_hash)
                if isinstance(block_hash, (bytes, bytearray))
                else block_hash
            )
        ),
        "blockNumber": (
            None
            if pending
            else (int(block_number) if block_number is not None else None)
        ),
        "transactionIndex": (
            None if pending else (int(tx_index) if tx_index is not None else None)
        ),
    }
    return {k: v for k, v in v.items() if v is not None}


def _pending_put(tx_hash_hex: str, raw: bytes) -> None:
    # Prefer the real pool
    if _PEND is not None and hasattr(_PEND, "add_raw"):
        _PEND.add_raw(tx_hash_hex, raw)  # type: ignore[attr-defined]
        return
    if _PEND is not None and hasattr(_PEND, "add"):
        _PEND.add(tx_hash_hex, raw)  # type: ignore[attr-defined]
        return
    # Fallback (dev)
    _FALLBACK_PENDING[tx_hash_hex] = raw


def _pending_get(tx_hash_hex: str) -> bytes | None:
    if _PEND is not None and hasattr(_PEND, "get_raw"):
        return _PEND.get_raw(tx_hash_hex)  # type: ignore[attr-defined]
    if _PEND is not None and hasattr(_PEND, "get"):
        return _PEND.get(tx_hash_hex)  # type: ignore[attr-defined]
    return _FALLBACK_PENDING.get(tx_hash_hex)


def _lookup_persisted_tx(
    tx_hash_hex: str,
) -> tuple[dict | None, int | None, int | None, bytes | None]:
    """
    Return (obj_view, block_number, tx_index, block_hash) if found in DB; otherwise (None, None, None, None).
    """
    # Use state_service if exposed
    svc = getattr(deps, "state_service", None)
    if svc is not None:
        # Expect methods like: get_transaction_by_hash, get_receipt_by_hash, etc.
        if hasattr(svc, "get_transaction_by_hash"):
            tx_rec = svc.get_transaction_by_hash(tx_hash_hex)  # type: ignore
            if tx_rec:
                # tx_rec is expected to have (tx, block_number, index, block_hash)
                tx_obj = tx_rec.get("tx") or tx_rec
                block_number = tx_rec.get("blockNumber")
                index = tx_rec.get("transactionIndex")
                b_hash = tx_rec.get("blockHash")
                if isinstance(b_hash, str):
                    b_hash = _b(b_hash)
                # tx_obj might be raw CBOR or dict or dataclass
                if isinstance(tx_obj, (bytes, bytearray)) and _cbor_loads:
                    obj = _cbor_loads(bytes(tx_obj))
                    tx_like = obj
                else:
                    tx_like = tx_obj
                    obj = _dcd(tx_obj) if _dc.is_dataclass(tx_obj) else dict(tx_obj)
                view = _tx_view(
                    tx_like,
                    obj,
                    pending=False,
                    block_hash=b_hash,
                    block_number=block_number,
                    tx_index=index,
                )
                return (
                    view,
                    int(block_number) if block_number is not None else None,
                    int(index) if index is not None else None,
                    b_hash if isinstance(b_hash, (bytes, bytearray)) else None,
                )

    # Try lower-level deps if present
    if hasattr(deps, "get_tx_by_hash"):
        rec = deps.get_tx_by_hash(tx_hash_hex)  # type: ignore
        if rec:
            # Best effort projection
            obj = rec.get("obj", {})
            blk = rec.get("block", {})
            h = blk.get("number") or blk.get("height")
            idx = rec.get("index")
            bh = blk.get("hash")
            if isinstance(bh, str):
                bh = _b(bh)
            view = _tx_view(
                obj,
                obj if isinstance(obj, dict) else _dcd(obj),
                pending=False,
                block_hash=bh,
                block_number=h,
                tx_index=idx,
            )
            return view, h, idx, bh
    return None, None, None, None


# ——— Methods ———


@method(
    "tx.sendRawTransaction",
    desc="Submit a signed CBOR-encoded transaction. Param: rawTx (hex string '0x…' or base64 '0b:…'). Returns tx hash.",
    aliases=("tx_sendRawTransaction",),
)
def tx_send_raw_transaction(rawTx: str) -> str:
    # Accept hex only for now
    if not isinstance(rawTx, str):
        raise rpc_errors.InvalidParams("rawTx must be a hex string")
    if rawTx.startswith("0b:"):
        raise rpc_errors.InvalidParams("base64 not supported yet; send hex (0x…)")

    raw = _b(rawTx)
    tx_like, obj = _decode_tx(raw)

    # Basic chainId check
    _validate_chain_id(obj)

    # PQ signature verify
    _verify_pq_signature(tx_like, obj)

    # Compute hash
    tx_hash_hex = _compute_tx_hash(tx_like if _dc.is_dataclass(tx_like) else obj)

    # Duplicate suppression: if already in pending/persisted, return hash (idempotent)
    if _pending_get(tx_hash_hex) is not None:
        return tx_hash_hex
    persisted, *_ = _lookup_persisted_tx(tx_hash_hex)
    if persisted is not None:
        return tx_hash_hex

    # Admit to pending pool (stateless checks already done here)
    _pending_put(tx_hash_hex, raw)

    # Notify WS hub (best-effort)
    try:
        if hasattr(deps, "ws_broadcast_pending"):
            deps.ws_broadcast_pending(tx_hash_hex, obj)  # type: ignore
    except Exception:
        pass

    return tx_hash_hex


@method(
    "tx.getTransactionByHash",
    desc="Get a transaction by hash. Returns full object with pending/persisted context.",
    aliases=("tx_getTransactionByHash",),
)
def tx_get_transaction_by_hash(txHash: str) -> t.Optional[dict]:
    if not isinstance(txHash, str):
        raise rpc_errors.InvalidParams("txHash must be hex string")
    tx_hash_hex = txHash.lower()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex

    # 1) Check pending pool
    raw = _pending_get(tx_hash_hex)
    if raw is not None and _cbor_loads is not None:
        obj = _cbor_loads(raw)
        tx_like = obj
        return _tx_view(
            tx_like, obj if isinstance(obj, dict) else _dcd(obj), pending=True
        )

    # 2) Check persisted DB via deps/state_service
    view, *_etc = _lookup_persisted_tx(tx_hash_hex)
    if view is not None:
        return view

    # 3) Not found
    return None
