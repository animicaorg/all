from __future__ import annotations

"""
rpc.state_service
=================

A thin, defensive service layer the RPC methods call into. It wraps:
- Balances & nonces (read-only state)
- Block/tx lookup by number/hash
- Tx decode (CBOR) and PQ signature verification
- Helpers for address parsing and tx hashing

This module intentionally tolerates evolution of the core/ and pq/ packages:
it detects functions/attributes at runtime and falls back to safe alternatives
so the RPC surface stays stable even as internals change.
"""

import binascii
import hashlib
import typing as t
from dataclasses import dataclass

from .deps import get_ctx, cbor_loads, cbor_dumps


# -------- Address helpers ----------------------------------------------------

def _import(path: str):
    import importlib
    return importlib.import_module(path)

def _is_hex(s: str) -> bool:
    try:
        if s.startswith("0x"):
            int(s[2:], 16)
            return True
        int(s, 16)
        return True
    except Exception:
        return False

def parse_address(addr: str) -> bytes:
    """
    Accepts 'anim1…' (bech32m) or hex '0x…'/'…' and returns raw 32-byte payload.
    For bech32m, we expect payload = (alg_id || sha3_256(pubkey)) as per pq/address.py.
    """
    s = addr.strip()
    # Hex path
    if _is_hex(s):
        h = s[2:] if s.startswith("0x") else s
        b = binascii.unhexlify(h)
        return b

    # bech32m path
    try:
        bech = _import("pq.py.utils.bech32")
        hrp, data, spec = bech.bech32_decode(s)
        if spec != "bech32m":
            raise ValueError("expected bech32m")
        payload = bech.convertbits(data, 5, 8, False)  # type: ignore[attr-defined]
        if payload is None:
            raise ValueError("bad bech32m data")
        return bytes(payload)
    except Exception:
        # Try higher-level codec if available
        try:
            addr_mod = _import("pq.py.address")
            return addr_mod.decode(addr)  # type: ignore[attr-defined]
        except Exception as e:
            raise ValueError(f"Invalid address format: {addr}") from e


# -------- Hash helpers -------------------------------------------------------

def _sha3_256(data: bytes) -> bytes:
    try:
        hmod = _import("core.utils.hash")
        if hasattr(hmod, "sha3_256"):
            return hmod.sha3_256(data)  # type: ignore
    except Exception:
        pass
    return hashlib.sha3_256(data).digest()

def _keccak_256(data: bytes) -> bytes:
    try:
        import sha3  # pysha3
        k = sha3.keccak_256()
        k.update(data)
        return k.digest()
    except Exception:
        # Fallback: SHA3-256 (NOT keccak) if keccak unavailable; only for debug
        return hashlib.sha3_256(data).digest()


# -------- Tx decode / encode domain -----------------------------------------

@dataclass
class DecodedTx:
    raw: t.Any                 # original object (dict or core.types.tx.Tx)
    view: dict                 # JSON-friendly dict view (for RPC models)
    sign_bytes: bytes          # canonical message that was signed
    hash: str                  # tx hash (0x…)
    alg_id: int | None         # PQ algorithm id (if present)
    pubkey: bytes | None       # signer pubkey bytes (if present)
    signature: bytes | None    # signature bytes (if present)
    chain_id: int | None       # chain id inside tx (if present)
    from_addr: str | None      # bech32/hex address if included/derivable


def _tx_sign_bytes(tx_obj: t.Any) -> bytes:
    """
    Try multiple entrypoints for canonical sign-bytes:
    - core.encoding.canonical.tx_sign_bytes(tx)
    - core.encoding.canonical.sign_bytes_for_tx(tx)
    - tx.sign_bytes() method
    - dict → CBOR of dict without 'sig'/'signature' fields
    """
    # canonical helpers
    try:
        can = _import("core.encoding.canonical")
        for name in ("tx_sign_bytes", "sign_bytes_for_tx"):
            fn = getattr(can, name, None)
            if callable(fn):
                return t.cast(bytes, fn(tx_obj))
    except Exception:
        pass

    # method on object
    try:
        sb = getattr(tx_obj, "sign_bytes", None)
        if callable(sb):
            return t.cast(bytes, sb())
    except Exception:
        pass

    # last resort: dict filtered & CBOR-encoded
    if isinstance(tx_obj, dict):
        filtered = {k: v for k, v in tx_obj.items() if k not in ("sig", "signature")}
        return cbor_dumps(filtered)

    # unknown object — serialize via CBOR best-effort
    return cbor_dumps(tx_obj)


def _tx_extract_fields(tx_obj: t.Any) -> tuple[int | None, bytes | None, bytes | None, str | None]:
    """
    Returns (alg_id, pubkey, signature, from_addr) if discoverable.
    Tries common field names regardless of case/underscore differences.
    """
    def get(obj, *names):
        for n in names:
            if isinstance(obj, dict) and n in obj:
                return obj[n]
            if hasattr(obj, n):
                return getattr(obj, n)
        return None

    alg_id = get(tx_obj, "alg_id", "algId", "signature_alg", "sig_alg")
    pubkey = get(tx_obj, "pubkey", "public_key", "sender_pubkey", "fromPubkey")
    signature = get(tx_obj, "sig", "signature", "tx_sig")

    # from address (optional)
    from_addr = get(tx_obj, "from", "sender", "sender_addr", "fromAddress")
    if isinstance(from_addr, (bytes, bytearray)):
        from_addr = "0x" + binascii.hexlify(t.cast(bytes, from_addr)).decode()

    # Normalize ints & bytes
    if isinstance(alg_id, str) and alg_id.isdigit():
        alg_id = int(alg_id)
    if isinstance(alg_id, (bytes, bytearray)):
        # Sometimes encoded as 1-byte prefix
        try:
            alg_id = int.from_bytes(alg_id[:2], "big")
        except Exception:
            alg_id = None

    def to_bytes(x):
        if x is None:
            return None
        if isinstance(x, (bytes, bytearray)):
            return bytes(x)
        if isinstance(x, str):
            xx = x[2:] if x.startswith("0x") else x
            try:
                return binascii.unhexlify(xx)
            except Exception:
                return x.encode()
        return None

    return t.cast(int | None, alg_id), to_bytes(pubkey), to_bytes(signature), t.cast(str | None, from_addr)


def _tx_compute_hash(tx_obj: t.Any) -> str:
    """
    Compute a stable tx hash. Preference order:
    - core.types.tx.hash(tx) or tx.hash() if available
    - keccak256(CBOR(tx)) as a conventional choice (debug)
    """
    try:
        mod = _import("core.types.tx")
        # function hash(tx)
        fn = getattr(mod, "tx_hash", None) or getattr(mod, "hash", None)
        if callable(fn):
            h = fn(tx_obj)  # type: ignore
            if isinstance(h, (bytes, bytearray)):
                return "0x" + binascii.hexlify(h).decode()
            if isinstance(h, str):
                return h if h.startswith("0x") else "0x" + h
    except Exception:
        pass

    # method on instance
    try:
        meth = getattr(tx_obj, "hash", None)
        if callable(meth):
            h = meth()
            if isinstance(h, (bytes, bytearray)):
                return "0x" + binascii.hexlify(h).decode()
            if isinstance(h, str):
                return h if h.startswith("0x") else "0x" + h
    except Exception:
        pass

    # fallback: keccak256 over canonical CBOR
    enc = cbor_dumps(tx_obj)
    h = _keccak_256(enc)
    return "0x" + binascii.hexlify(h).decode()


def decode_tx(cbor_bytes: bytes) -> DecodedTx:
    """
    Decode a CBOR-encoded transaction into a friendly view plus verification
    materials (sign-bytes, pubkey/sig, chain id).
    """
    obj = cbor_loads(cbor_bytes)
    # If a core Tx constructor exists, try to upgrade the dict; otherwise keep dict.
    tx_obj = obj
    try:
        mod = _import("core.types.tx")
        for name in ("from_dict", "from_obj", "decode"):
            fn = getattr(mod, name, None)
            if callable(fn) and isinstance(obj, dict):
                tx_obj = fn(obj)  # type: ignore
                break
    except Exception:
        pass

    sign_bytes = _tx_sign_bytes(tx_obj)
    tx_hash = _tx_compute_hash(tx_obj)
    alg_id, pubkey, signature, from_addr = _tx_extract_fields(tx_obj)

    # ChainId (if the object carries it)
    chain_id = None
    for k in ("chain_id", "chainId", "cid"):
        if isinstance(tx_obj, dict) and k in tx_obj:
            chain_id = int(tx_obj[k])
            break
        if hasattr(tx_obj, k):
            chain_id = int(getattr(tx_obj, k))
            break

    # Prepare a JSON-friendly view
    if isinstance(obj, dict):
        view = obj.copy()
        view["hash"] = tx_hash
    else:
        # Best-effort attribute projection
        view = {"hash": tx_hash}
        for k in ("kind", "from", "to", "nonce", "gas", "value"):
            if hasattr(tx_obj, k):
                view[k] = getattr(tx_obj, k)

    return DecodedTx(
        raw=tx_obj,
        view=view,
        sign_bytes=sign_bytes,
        hash=tx_hash,
        alg_id=alg_id,
        pubkey=pubkey,
        signature=signature,
        chain_id=chain_id,
        from_addr=from_addr,
    )


# -------- Verification -------------------------------------------------------

@dataclass
class VerifyResult:
    ok: bool
    reason: str | None = None
    alg_id: int | None = None

def verify_tx_signature(dt: DecodedTx) -> VerifyResult:
    """
    Verify the PQ signature in a decoded tx using pq.verify.verify.
    Also enforces chain-id match with the running node (if present).
    """
    ctx = get_ctx()
    # Chain-id check
    if dt.chain_id is not None and int(dt.chain_id) != int(ctx.cfg.chain_id):
        return VerifyResult(ok=False, reason=f"ChainIdMismatch: {dt.chain_id} != {ctx.cfg.chain_id}", alg_id=dt.alg_id)

    # Signature presence
    if dt.signature is None or dt.pubkey is None or dt.alg_id is None:
        return VerifyResult(ok=False, reason="Missing signature/pubkey/alg_id", alg_id=dt.alg_id)

    try:
        pv = _import("pq.py.verify")
        ok = bool(pv.verify(dt.alg_id, dt.pubkey, dt.signature, dt.sign_bytes))
        return VerifyResult(ok=ok, reason=None if ok else "SignatureInvalid", alg_id=dt.alg_id)
    except Exception as e:
        return VerifyResult(ok=False, reason=f"VerifyError: {e}", alg_id=dt.alg_id)


# -------- State DB (balance/nonce) ------------------------------------------

def get_balance(addr_str: str) -> int:
    """
    Returns the integer balance (base units) for the given address.
    """
    ctx = get_ctx()
    addr = parse_address(addr_str)
    sdb = ctx.state_db

    # Try canonical helpers in order of preference
    for name in ("get_balance", "read_balance", "balance_of"):
        fn = getattr(sdb, name, None)
        if callable(fn):
            return int(fn(addr))  # type: ignore

    # Try account getter
    acct = None
    for name in ("get_account", "read_account", "account_of"):
        fn = getattr(sdb, name, None)
        if callable(fn):
            acct = fn(addr)  # type: ignore
            break
    if isinstance(acct, dict) and "balance" in acct:
        return int(acct["balance"])

    # Fallback: zero if unknown
    return 0


def get_nonce(addr_str: str) -> int:
    """
    Returns the current transaction nonce for the address (0 if none).
    """
    ctx = get_ctx()
    addr = parse_address(addr_str)
    sdb = ctx.state_db

    for name in ("get_nonce", "read_nonce", "nonce_of"):
        fn = getattr(sdb, name, None)
        if callable(fn):
            return int(fn(addr))  # type: ignore

    acct = None
    for name in ("get_account", "read_account", "account_of"):
        fn = getattr(sdb, name, None)
        if callable(fn):
            acct = fn(addr)  # type: ignore
            break
    if isinstance(acct, dict) and "nonce" in acct:
        return int(acct["nonce"])
    return 0


# -------- Block & Tx lookup --------------------------------------------------

def _block_view(block_obj: t.Any, include_txs: bool = True) -> dict:
    """
    Convert a core.types.block.Block or dict into a JSON-friendly view.
    """
    if isinstance(block_obj, dict):
        out = dict(block_obj)
        # normalize hash field naming if needed
        if "blockHash" in out and "hash" not in out:
            out["hash"] = out["blockHash"]
        return out

    out: dict[str, t.Any] = {}
    for k in ("height", "hash", "parentHash", "time", "mixSeed"):
        if hasattr(block_obj, k):
            out[k] = getattr(block_obj, k)
    # header projection
    hdr = getattr(block_obj, "header", None)
    if hdr is not None:
        out["header"] = {}
        for k in ("number", "parentHash", "stateRoot", "txRoot", "proofsRoot", "daRoot", "theta", "nonce", "mixSeed", "chainId"):
            if hasattr(hdr, k):
                out["header"][k] = getattr(hdr, k)
    # txs projection
    if include_txs and hasattr(block_obj, "txs"):
        txs = []
        for tx in getattr(block_obj, "txs"):
            try:
                enc = cbor_dumps(tx)
                dt = decode_tx(enc)
                txs.append(dt.view)
            except Exception:
                txs.append({"raw": str(tx)})
        out["txs"] = txs
    return out


def get_block_by_number(height: int, include_txs: bool = True) -> dict | None:
    ctx = get_ctx()
    bdb = ctx.block_db
    # Prefer function get_block_by_height or class method variants
    for name in ("get_block_by_height", "by_height", "get_block_height"):
        fn = getattr(bdb, name, None)
        if callable(fn):
            blk = fn(height)  # type: ignore
            return None if blk is None else _block_view(blk, include_txs)
    # Some implementations separate header/tx fetch:
    get_header = getattr(bdb, "get_header_by_height", None)
    if callable(get_header):
        hdr = get_header(height)  # type: ignore
        if hdr is None:
            return None
        return _block_view({"header": hdr, "height": height}, include_txs=False)
    return None


def get_block_by_hash(block_hash: str, include_txs: bool = True) -> dict | None:
    ctx = get_ctx()
    bdb = ctx.block_db
    for name in ("get_block_by_hash", "by_hash", "get_block_hash"):
        fn = getattr(bdb, name, None)
        if callable(fn):
            blk = fn(block_hash)  # type: ignore
            return None if blk is None else _block_view(blk, include_txs)
    get_header = getattr(bdb, "get_header_by_hash", None)
    if callable(get_header):
        hdr = get_header(block_hash)  # type: ignore
        if hdr is None:
            return None
        height = hdr.get("number") if isinstance(hdr, dict) else getattr(hdr, "number", None)
        return _block_view({"header": hdr, "height": height, "hash": block_hash}, include_txs=False)
    return None


def get_tx_by_hash(tx_hash: str) -> dict | None:
    """
    Lookup a transaction by hash via the tx_index if present; fall back to scanning recent blocks if needed.
    """
    ctx = get_ctx()
    tix = ctx.tx_index
    bdb = ctx.block_db

    # Through index
    for name in ("get", "lookup", "find"):
        fn = getattr(tix, name, None)
        if callable(fn):
            loc = fn(tx_hash)  # type: ignore
            # Common shapes: (height, idx) or {'height':..,'index':..}
            if loc:
                if isinstance(loc, (list, tuple)) and len(loc) >= 2:
                    height, idx = int(loc[0]), int(loc[1])
                elif isinstance(loc, dict):
                    height, idx = int(loc.get("height", -1)), int(loc.get("index", -1))
                else:
                    height, idx = -1, -1
                # fetch block and project tx
                blk = None
                for bn in ("get_block_by_height", "by_height", "get_block_height"):
                    bf = getattr(bdb, bn, None)
                    if callable(bf):
                        blk = bf(height)  # type: ignore
                        break
                if blk and hasattr(blk, "txs"):
                    txs = getattr(blk, "txs")
                    if 0 <= idx < len(txs):
                        enc = cbor_dumps(txs[idx])
                        dt = decode_tx(enc)
                        return dt.view
            break

    # Fallback: try a light get-by-hash in block db if provided
    for name in ("get_tx_by_hash", "tx_by_hash"):
        fn = getattr(bdb, name, None)
        if callable(fn):
            tx = fn(tx_hash)  # type: ignore
            if tx:
                enc = cbor_dumps(tx)
                return decode_tx(enc).view

    return None


# -------- Public facade ------------------------------------------------------

class StateService:
    """Stateless facade — methods read from the global context opened by rpc.deps."""
    # Balances / nonces
    def get_balance(self, address: str) -> int:
        return get_balance(address)

    def get_nonce(self, address: str) -> int:
        return get_nonce(address)

    # Blocks
    def get_block_by_number(self, height: int, include_txs: bool = True) -> dict | None:
        return get_block_by_number(height, include_txs)

    def get_block_by_hash(self, block_hash: str, include_txs: bool = True) -> dict | None:
        return get_block_by_hash(block_hash, include_txs)

    # Tx
    def get_tx_by_hash(self, tx_hash: str) -> dict | None:
        return get_tx_by_hash(tx_hash)

    # Decode & verify
    def decode_tx(self, tx_cbor: bytes) -> DecodedTx:
        return decode_tx(tx_cbor)

    def verify_tx(self, tx_cbor: bytes) -> VerifyResult:
        dt = decode_tx(tx_cbor)
        return verify_tx_signature(dt)


__all__ = [
    "StateService",
    "decode_tx",
    "verify_tx_signature",
    "get_balance",
    "get_nonce",
    "get_block_by_number",
    "get_block_by_hash",
    "get_tx_by_hash",
    "DecodedTx",
    "VerifyResult",
    "parse_address",
]
