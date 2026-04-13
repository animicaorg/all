from __future__ import annotations

import logging
import os
import typing as t

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method

try:
    from core.utils.deploy_metadata import (
        canonical_contract_address as _canonical_contract_address,
        copy_deploy_metadata_fields as _copy_deploy_metadata_fields,
    )
except Exception:  # pragma: no cover
    _canonical_contract_address = None  # type: ignore[assignment]
    _copy_deploy_metadata_fields = None  # type: ignore[assignment]

HexStr = str
log = logging.getLogger(__name__)


# ——— Helpers ———


def _is_hex(s: str) -> bool:
    s = s.lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) % 2 == 1:
        return False
    try:
        bytes.fromhex(s)
        return True
    except Exception:
        return False


def _ensure_hex_prefixed(b: bytes | bytearray | memoryview | str) -> str:
    if isinstance(b, str):
        if b.startswith("0x") and _is_hex(b):
            return b.lower()
        if _is_hex(b):
            return "0x" + b.lower()
        raise ValueError("not hex")
    return "0x" + bytes(b).hex()


def _parse_tx_hash(h: t.Any) -> tuple[str, bytes]:
    if not isinstance(h, str) or not h:
        raise rpc_errors.InvalidParams("txHash must be a non-empty hex string")
    s = h.strip().lower()
    if not _is_hex(s):
        raise rpc_errors.InvalidParams("txHash must be 0x-prefixed hex")
    if not s.startswith("0x"):
        s = "0x" + s
    return s, bytes.fromhex(s[2:])


def _hex_quantity(n: int) -> str:
    if n < 0:
        raise rpc_errors.InternalError("negative quantity not allowed")
    return hex(n)


def _pending_contains(tx_hash_hex: str) -> bool:
    # Prefer canonical mempool service used by tx admission/mining.
    try:
        from rpc.methods import tx as tx_methods
    except Exception:
        tx_methods = None  # type: ignore[assignment]
    if tx_methods is not None and hasattr(tx_methods, "_get_mempool_service"):
        try:
            svc = tx_methods._get_mempool_service()  # type: ignore[attr-defined]
        except Exception:
            svc = None
        if svc is not None:
            if hasattr(svc, "has_hash") and callable(getattr(svc, "has_hash")):
                try:
                    return bool(svc.has_hash(tx_hash_hex))
                except Exception:
                    pass
            if hasattr(svc, "snapshot") and callable(getattr(svc, "snapshot")):
                try:
                    snap = svc.snapshot(limit=1000)
                    for entry in getattr(snap, "entries", []) or []:
                        if getattr(entry, "hash_hex", None) == tx_hash_hex:
                            return True
                except Exception:
                    pass

    # Legacy fallback only when canonical service is unavailable.
    pool = getattr(deps, "pending_pool", None)
    if pool is None:
        return False
    if hasattr(pool, "has"):
        try:
            return bool(pool.has(tx_hash_hex))
        except Exception:
            pass
    if hasattr(pool, "contains"):
        try:
            return bool(pool.contains(tx_hash_hex))
        except Exception:
            pass
    try:
        return tx_hash_hex in pool  # type: ignore[operator]
    except Exception:
        return False


# ——— Receipt lookup paths ———


class _ReceiptLoc(t.TypedDict, total=False):
    height: int
    index: int
    block_hash: bytes


def _lookup_receipt_loc(tx_hash_b: bytes) -> _ReceiptLoc | None:
    """
    Try to find (height, index) via deps.tx_index, or directly via block_db helpers.
    """
    # 1) Direct block_db helpers
    bdb = getattr(deps, "block_db", None)
    if bdb is not None:
        if hasattr(bdb, "get_receipt_loc_by_hash"):  # preferred API
            loc = bdb.get_receipt_loc_by_hash(tx_hash_b)  # type: ignore[attr-defined]
            if loc:
                return t.cast(_ReceiptLoc, loc)
        if hasattr(bdb, "get_loc_by_tx_hash"):
            loc = bdb.get_loc_by_tx_hash(tx_hash_b)  # type: ignore[attr-defined]
            if loc:
                return t.cast(_ReceiptLoc, loc)

    # 2) tx_index → (height, index)
    tidx = getattr(deps, "tx_index", None)
    if tidx is not None:
        # Try common shapes
        for meth in ("get", "lookup", "get_loc"):
            if hasattr(tidx, meth):
                try:
                    loc = getattr(tidx, meth)(tx_hash_b)  # type: ignore[misc]
                    if isinstance(loc, (tuple, list)) and len(loc) >= 2:
                        out: _ReceiptLoc = _ReceiptLoc(height=int(loc[0]), index=int(loc[1]))
                        if len(loc) >= 3 and isinstance(loc[2], (bytes, bytearray, memoryview)):
                            out["block_hash"] = bytes(loc[2])
                        return out
                    if isinstance(loc, dict) and "height" in loc and "index" in loc:
                        out: _ReceiptLoc = _ReceiptLoc(
                            height=int(loc["height"]), index=int(loc["index"])
                        )
                        bh = loc.get("block_hash")
                        if isinstance(bh, (bytes, bytearray, memoryview)):
                            out["block_hash"] = bytes(bh)
                        return out
                    if hasattr(loc, "height") and hasattr(loc, "index"):
                        out = _ReceiptLoc(
                            height=int(getattr(loc, "height")),
                            index=int(getattr(loc, "index")),
                        )
                        bh = getattr(loc, "block_hash", None)
                        if isinstance(bh, (bytes, bytearray, memoryview)):
                            out["block_hash"] = bytes(bh)
                        return out
                except Exception:
                    pass

    # 3) Scan recent canonical blocks as a resilient fallback if indexes lag.
    scan_depth = int(os.environ.get("ANIMICA_TX_RECEIPT_SCAN_DEPTH", "4096") or 4096)
    if scan_depth > 0:
        loc = _scan_receipt_loc_by_blocks(tx_hash_b, max_depth=scan_depth)
        if loc is not None:
            return loc

    # 4) Not found
    return None


def _tx_hash_from_obj(tx_obj: t.Any) -> bytes | None:
    if isinstance(tx_obj, dict):
        h = tx_obj.get("hash")
        if isinstance(h, str) and h:
            raw = h[2:] if h.startswith("0x") else h
            try:
                return bytes.fromhex(raw)
            except Exception:
                return None
        if isinstance(h, (bytes, bytearray, memoryview)):
            return bytes(h)
    txid_fn = getattr(tx_obj, "txid", None)
    if callable(txid_fn):
        try:
            hv = txid_fn()
            if isinstance(hv, (bytes, bytearray, memoryview)):
                return bytes(hv)
        except Exception:
            pass
    h_attr = getattr(tx_obj, "hash", None)
    if callable(h_attr):
        try:
            hv = h_attr()
            if isinstance(hv, (bytes, bytearray, memoryview)):
                return bytes(hv)
        except Exception:
            pass
    elif isinstance(h_attr, (bytes, bytearray, memoryview)):
        return bytes(h_attr)
    elif isinstance(h_attr, str):
        raw = h_attr[2:] if h_attr.startswith("0x") else h_attr
        try:
            return bytes.fromhex(raw)
        except Exception:
            return None
    return None


def _lookup_deploy_metadata(tx_hash_b: bytes) -> dict | None:
    bdb = getattr(deps, "block_db", None)
    if bdb is None:
        return None
    getter = getattr(bdb, "get_deploy_metadata_by_tx_hash", None)
    if not callable(getter):
        return None
    try:
        out = getter(tx_hash_b)
    except Exception:
        return None
    return out if isinstance(out, dict) else None


def _bytes_from_hex_string(value: t.Any) -> bytes | None:
    if not isinstance(value, str):
        return None
    raw = value[2:] if value.startswith("0x") else value
    if not raw:
        return None
    try:
        return bytes.fromhex(raw)
    except Exception:
        return None


def _scan_receipt_loc_by_blocks(tx_hash_b: bytes, *, max_depth: int) -> _ReceiptLoc | None:
    bdb = getattr(deps, "block_db", None)
    if bdb is None:
        return None

    head_height: int | None = None
    try:
        head = deps.ensure_started().get_head()
        if isinstance(head, dict):
            head_height = int(head.get("height") or 0)
    except Exception:
        head_height = None
    if head_height is None:
        try:
            if hasattr(bdb, "get_height"):
                head_height = int(bdb.get_height())  # type: ignore[attr-defined]
        except Exception:
            head_height = None
    if head_height is None:
        return None

    lower = max(0, int(head_height) - max(1, int(max_depth)) + 1)
    for h in range(int(head_height), lower - 1, -1):
        blk = None
        try:
            if hasattr(bdb, "get_block_by_height"):
                blk = bdb.get_block_by_height(h)  # type: ignore[attr-defined]
            elif hasattr(bdb, "get_block"):
                blk = bdb.get_block(h)  # type: ignore[attr-defined]
        except Exception:
            blk = None
        if blk is None:
            continue

        txs = getattr(blk, "txs", None)
        if txs is None and isinstance(blk, dict):
            txs = blk.get("txs") or blk.get("transactions")
        if not isinstance(txs, (list, tuple)):
            continue

        for idx, tx_obj in enumerate(txs):
            if _tx_hash_from_obj(tx_obj) != tx_hash_b:
                continue
            out: _ReceiptLoc = _ReceiptLoc(height=int(h), index=int(idx))
            block_hash = _extract_block_hash(blk)
            if isinstance(block_hash, (bytes, bytearray, memoryview)):
                out["block_hash"] = bytes(block_hash)
            return out
    return None


def _fetch_block_and_receipt(
    loc: _ReceiptLoc, tx_hash_b: bytes
) -> tuple[dict | t.Any, t.Any] | None:
    """
    Retrieve (block, receipt_obj) using block_db. Support multiple shapes.
    """
    bdb = getattr(deps, "block_db", None)
    if bdb is None:
        return None

    h = int(loc["height"])
    idx = int(loc["index"])

    # Direct "get_receipt_at" API
    if hasattr(bdb, "get_receipt_at"):
        try:
            r = bdb.get_receipt_at(h, idx)  # type: ignore[attr-defined]
            # Try get block too for hashes/number
            blk = None
            if hasattr(bdb, "get_block_by_height"):
                blk = bdb.get_block_by_height(h)  # type: ignore[attr-defined]
            elif hasattr(bdb, "get_block"):
                blk = bdb.get_block(h)  # type: ignore[attr-defined]
            return (blk, r)
        except Exception:
            pass

    # Fetch block and pull receipt by index
    blk = None
    for getter in ("get_block_by_height", "get_block"):
        if hasattr(bdb, getter):
            try:
                blk = getattr(bdb, getter)(h)  # type: ignore[misc]
                break
            except Exception:
                pass
    if blk is None:
        return None

    # Try to load receipts array from block_db if provided
    recs = None
    for getter in ("get_receipts_by_height", "get_receipts"):
        if hasattr(bdb, getter):
            try:
                recs = getattr(bdb, getter)(h)  # type: ignore[misc]
                break
            except Exception:
                pass

    if recs is not None and isinstance(recs, (list, tuple)) and 0 <= idx < len(recs):
        return (blk, recs[idx])

    # Fallback: block may embed receipts or a tx→receipt map
    if isinstance(blk, dict):
        if (
            "receipts" in blk
            and isinstance(blk["receipts"], list)
            and 0 <= idx < len(blk["receipts"])
        ):
            return (blk, blk["receipts"][idx])

    # Last resort: try a single-shot API by hash
    if hasattr(bdb, "get_receipt_by_hash"):
        try:
            r = bdb.get_receipt_by_hash(tx_hash_b)  # type: ignore[attr-defined]
            return (blk, r)
        except Exception:
            pass
    # Last fallback: tx is known in canonical chain, but receipt payload is unavailable.
    # Return an empty placeholder so callers can still surface block inclusion.
    return (blk, {})


def _extract_block_hash(blk: t.Any) -> bytes | None:
    if blk is None:
        return None
    if isinstance(blk, dict):
        if "hash" in blk:
            v = blk["hash"]
            if isinstance(v, (bytes, bytearray, memoryview)):
                return bytes(v)
            if isinstance(v, str) and _is_hex(v):
                return bytes.fromhex(v[2:] if v.startswith("0x") else v)
        if (
            "header" in blk
            and isinstance(blk["header"], dict)
            and "hash" in blk["header"]
        ):
            v = blk["header"]["hash"]
            if isinstance(v, (bytes, bytearray, memoryview)):
                return bytes(v)
            if isinstance(v, str) and _is_hex(v):
                return bytes.fromhex(v[2:] if v.startswith("0x") else v)
    # Object-style with attribute
    for attr in ("hash", "block_hash"):
        if hasattr(blk, attr):
            v = getattr(blk, attr)
            if isinstance(v, (bytes, bytearray, memoryview)):
                return bytes(v)
            if isinstance(v, str) and _is_hex(v):
                return bytes.fromhex(v[2:] if v.startswith("0x") else v)
    return None


def _normalize_logs(logs: t.Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(logs, (list, tuple)):
        return out
    for l in logs:
        if isinstance(l, dict):
            addr = l.get("address") or l.get("addr") or l.get("from") or ""
            data = l.get("data") or l.get("payload") or b""
            topics = l.get("topics") or []
        else:
            # unknown shape → skip
            continue
        try:
            addr_s = str(addr)
        except Exception:
            addr_s = ""
        # normalize hex fields
        if isinstance(data, (bytes, bytearray, memoryview)):
            data_s = _ensure_hex_prefixed(data)
        elif isinstance(data, str) and _is_hex(data):
            data_s = data if data.startswith("0x") else "0x" + data
        else:
            data_s = "0x"
        topics_s: list[str] = []
        if isinstance(topics, (list, tuple)):
            for tpc in topics:
                if isinstance(tpc, (bytes, bytearray, memoryview)):
                    topics_s.append(_ensure_hex_prefixed(tpc))
                elif isinstance(tpc, str) and _is_hex(tpc):
                    topics_s.append(tpc if tpc.startswith("0x") else "0x" + tpc)
        out.append({"address": addr_s, "data": data_s, "topics": topics_s})
    return out


def _normalize_receipt(
    tx_hash_hex: str,
    loc: _ReceiptLoc,
    blk: t.Any,
    rec: t.Any,
    deploy_meta: dict | None = None,
) -> dict:
    """
    Map various receipt object shapes into the RPC ReceiptView.
    Returns a plain dict; JSON-RPC layer will serialize it.
    """
    height = int(loc.get("height", -1))
    index = int(loc.get("index", -1))
    b_hash = loc.get("block_hash") or _extract_block_hash(blk)
    block_hash_hex = _ensure_hex_prefixed(b_hash) if b_hash else None

    # Extract common fields with tolerant keys
    def _pick(d: t.Any, *names: str, default=None):
        if isinstance(d, dict):
            for n in names:
                if n in d:
                    return d[n]
        for n in names:
            if hasattr(d, n):
                return getattr(d, n)
        return default

    status = _pick(rec, "status", "Status", default=None)
    gas_used = _pick(rec, "gasUsed", "gas_used", "gas", default=None)
    logs = _pick(rec, "logs", "event_logs", default=[])
    bloom = _pick(rec, "logsBloom", "bloom", default=None)
    contract_addr = _pick(rec, "contractAddress", "contract_addr", default=None)
    if contract_addr is None and callable(_canonical_contract_address):
        try:
            contract_addr = _canonical_contract_address(deploy_meta)
        except Exception:
            contract_addr = None

    # Normalize basic types
    if isinstance(gas_used, (bytes, bytearray, memoryview)):
        try:
            gas_used = int.from_bytes(gas_used, "big")
        except Exception:
            gas_used = None
    if isinstance(gas_used, str):
        try:
            gas_used = int(gas_used, 0 if gas_used.startswith(("0x", "0X")) else 10)
        except Exception:
            gas_used = None
    if isinstance(status, str) and status.isdigit():
        status = int(status)
    if status is None and isinstance(deploy_meta, dict):
        status = deploy_meta.get("status")
        if isinstance(status, str) and status.isdigit():
            status = int(status)

    # Build dictionary
    out: dict = {
        "transactionHash": tx_hash_hex,
        "transactionIndex": index if index >= 0 else None,
        "blockNumber": height if height >= 0 else None,
        "blockHash": block_hash_hex,
        "status": status,  # e.g., "SUCCESS" | "REVERT" | 1/0 depending on execution layer
        "gasUsed": (
            int(gas_used) if isinstance(gas_used, int) and gas_used >= 0 else None
        ),
        "logs": _normalize_logs(logs),
    }
    if contract_addr:
        out["contractAddress"] = str(contract_addr)
        out["createdAddress"] = str(contract_addr)
    if callable(_copy_deploy_metadata_fields):
        try:
            _copy_deploy_metadata_fields(out, deploy_meta, include_created_alias=True)
        except Exception:
            pass
    if bloom is not None:
        if isinstance(bloom, (bytes, bytearray, memoryview)):
            out["logsBloom"] = _ensure_hex_prefixed(bloom)
        elif isinstance(bloom, str) and _is_hex(bloom):
            out["logsBloom"] = bloom if bloom.startswith("0x") else "0x" + bloom

    return out


# ——— RPC Method ———


@method(
    "tx.getTransactionReceipt",
    desc=(
        "Return the transaction receipt for a mined transaction hash. "
        "If the transaction is pending or unknown, returns null."
    ),
    aliases=("tx_getTransactionReceipt", "tx.getReceipt", "tx_getReceipt"),
)
def tx_get_transaction_receipt(txHash: HexStr) -> t.Optional[dict]:
    # Validate & parse hash
    tx_hash_hex, tx_hash_b = _parse_tx_hash(txHash)
    deploy_meta = _lookup_deploy_metadata(tx_hash_b)
    lookup_trace: dict[str, t.Any] = {
        "tx_hash": tx_hash_hex,
        "checked": {
            "pending_mempool": False,
            "receipt_loc": False,
            "receipt_by_tx_hash": False,
            "receipt_by_hash": False,
        },
        "result": "unknown",
    }

    # Capture pending state, but do not early-return before checking canonical inclusion.
    # A stale mempool entry may coexist with a canonical inclusion.
    lookup_trace["checked"]["pending_mempool"] = True
    is_pending = _pending_contains(tx_hash_hex)

    # Find location (height, index), then fetch receipt and block context
    lookup_trace["checked"]["receipt_loc"] = True
    loc = _lookup_receipt_loc(tx_hash_b)
    if loc is None:
        # Reuse tx-status canonical lookup fallback (which scans recent blocks when indexes lag).
        try:
            from rpc.methods import tx as tx_methods

            if hasattr(tx_methods, "_lookup_persisted_tx"):
                _view, h, idx, bh = tx_methods._lookup_persisted_tx(tx_hash_hex)  # type: ignore[attr-defined]
                if h is not None:
                    loc = _ReceiptLoc(height=int(h), index=int(idx or 0))
                    if isinstance(bh, (bytes, bytearray, memoryview)):
                        loc["block_hash"] = bytes(bh)
                    pair = _fetch_block_and_receipt(loc, tx_hash_b)
                    if pair is None:
                        # Canonical inclusion known, receipt payload unavailable -> synthesize minimal view.
                        lookup_trace["result"] = "found_by_tx_scan"
                        log.debug("tx.getTransactionReceipt lookup", extra=lookup_trace)
                        return _normalize_receipt(tx_hash_hex, loc, None, {}, deploy_meta)
                    blk, rec = pair
                    lookup_trace["result"] = "found_by_tx_scan"
                    log.debug("tx.getTransactionReceipt lookup", extra=lookup_trace)
                    return _normalize_receipt(tx_hash_hex, loc, blk, rec, deploy_meta)
        except Exception:
            pass

        # Try the new get_receipt_by_tx_hash method on block_db
        bdb = getattr(deps, "block_db", None)
        if bdb is not None and hasattr(bdb, "get_receipt_by_tx_hash"):
            try:
                lookup_trace["checked"]["receipt_by_tx_hash"] = True
                result = bdb.get_receipt_by_tx_hash(tx_hash_b)  # type: ignore[attr-defined]
                if result is not None:
                    height, idx, block_hash, receipt = result
                    loc = _ReceiptLoc(height=height, index=idx, block_hash=block_hash)
                    # Fetch the block for context
                    blk = bdb.get_block_by_hash(block_hash)
                    lookup_trace["result"] = "found_by_tx_hash"
                    log.debug("tx.getTransactionReceipt lookup", extra=lookup_trace)
                    return _normalize_receipt(tx_hash_hex, loc, blk, receipt, deploy_meta)
            except Exception:
                pass

        # As a last-chance, some block_db offer get_receipt_by_hash directly
        bdb = getattr(deps, "block_db", None)
        if bdb is not None and hasattr(bdb, "get_receipt_by_hash"):
            try:
                lookup_trace["checked"]["receipt_by_hash"] = True
                rec = bdb.get_receipt_by_hash(tx_hash_b)  # type: ignore[attr-defined]
                if rec is None:
                    lookup_trace["result"] = "pending" if is_pending else "not_found"
                    log.debug("tx.getTransactionReceipt lookup", extra=lookup_trace)
                    return None
                # We don't know height/index; build a minimal result
                lookup_trace["result"] = "found_by_hash"
                log.debug("tx.getTransactionReceipt lookup", extra=lookup_trace)
                return _normalize_receipt(tx_hash_hex, _ReceiptLoc(), None, rec, deploy_meta)
            except Exception:
                pass
        if isinstance(deploy_meta, dict):
            loc_from_meta = _ReceiptLoc()
            meta_height = deploy_meta.get("blockNumber")
            meta_index = deploy_meta.get("transactionIndex")
            meta_block_hash = _bytes_from_hex_string(deploy_meta.get("blockHash"))
            if meta_height is not None:
                try:
                    loc_from_meta["height"] = int(meta_height)
                except Exception:
                    pass
            if meta_index is not None:
                try:
                    loc_from_meta["index"] = int(meta_index)
                except Exception:
                    pass
            if meta_block_hash is not None:
                loc_from_meta["block_hash"] = meta_block_hash
            if loc_from_meta:
                lookup_trace["result"] = "found_by_deploy_meta"
                log.debug("tx.getTransactionReceipt lookup", extra=lookup_trace)
                return _normalize_receipt(tx_hash_hex, loc_from_meta, None, {}, deploy_meta)
        lookup_trace["result"] = "pending" if is_pending else "not_found"
        log.debug("tx.getTransactionReceipt lookup", extra=lookup_trace)
        return None

    pair = _fetch_block_and_receipt(loc, tx_hash_b)
    if pair is None:
        lookup_trace["result"] = "not_found"
        log.debug("tx.getTransactionReceipt lookup", extra=lookup_trace)
        return None

    blk, rec = pair
    lookup_trace["result"] = "found"
    log.debug("tx.getTransactionReceipt lookup", extra=lookup_trace)
    return _normalize_receipt(tx_hash_hex, loc, blk, rec, deploy_meta)
