from __future__ import annotations

import typing as t

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method

HexStr = str


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
    pool = getattr(deps, "pending_pool", None)
    if pool is None:
        return False
    # best-effort: support .has, .contains, or 'in'
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
                        return _ReceiptLoc(height=int(loc[0]), index=int(loc[1]))
                    if isinstance(loc, dict) and "height" in loc and "index" in loc:
                        return _ReceiptLoc(
                            height=int(loc["height"]), index=int(loc["index"])
                        )
                except Exception:
                    pass

    # 3) Not found
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

    return None


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
    aliases=("tx_getTransactionReceipt",),
)
def tx_get_transaction_receipt(txHash: HexStr) -> t.Optional[dict]:
    # Validate & parse hash
    tx_hash_hex, tx_hash_b = _parse_tx_hash(txHash)

    # If it's still pending, report null per spec
    if _pending_contains(tx_hash_hex):
        return None

    # Find location (height, index), then fetch receipt and block context
    loc = _lookup_receipt_loc(tx_hash_b)
    if loc is None:
        # As a last-chance, some block_db offer get_receipt_by_hash directly
        bdb = getattr(deps, "block_db", None)
        if bdb is not None and hasattr(bdb, "get_receipt_by_hash"):
            try:
                rec = bdb.get_receipt_by_hash(tx_hash_b)  # type: ignore[attr-defined]
                if rec is None:
                    return None
                # We don't know height/index; build a minimal result
                return _normalize_receipt(tx_hash_hex, _ReceiptLoc(), None, rec)
            except Exception:
                pass
        return None

    pair = _fetch_block_and_receipt(loc, tx_hash_b)
    if pair is None:
        return None

    blk, rec = pair
    return _normalize_receipt(tx_hash_hex, loc, blk, rec)
