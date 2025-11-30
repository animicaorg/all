"""
Build/Sign/Send helpers for tests (uses sdk/python)
===================================================

This module wires the Python SDK (omni_sdk) into small utilities that tests can
use to quickly form transactions, sign them with PQ signers, send them via the
JSON-RPC test client, and (optionally) await receipts.

It purposefully avoids re-implementing SDK logic; instead, it delegates to:

- omni_sdk.tx.build      → transfer / call / deploy builders (+ gas helpers)
- omni_sdk.tx.encode     → CBOR SignBytes & signed TX encoding
- omni_sdk.wallet.signer → Signer interface (Dilithium3 / SPHINCS+)
- tests.harness.clients  → HttpRpcClient (already provided in this repo)

Usage in tests:

    from tests.harness.clients import HttpRpcClient
    from tests.harness.tx import send_transfer

    with HttpRpcClient(os.environ["RPC_URL"]) as rpc:
        res = send_transfer(rpc, signer, to_addr, 123_000)
        assert int(res.receipt["status"], 16) == 1

The helpers are robust to slight SDK surface differences (e.g. function name
aliases) to keep tests resilient while the SDK evolves.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple, Union

# --- SDK imports (builders/encoders) ---
from omni_sdk.tx import build as tx_build
from omni_sdk.tx import encode as tx_encode
from omni_sdk.utils import hash as sdk_hash  # keccak helpers if available

# --- Required test-time transport (local harness) ---
from tests.harness.clients import HttpRpcClient

# Optional address module for validation/normalization (best effort)
try:  # pragma: no cover - optional convenience only
    from omni_sdk import address as addr_mod
except Exception:  # pragma: no cover
    addr_mod = None  # type: ignore


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SendResult:
    tx_hash: str
    raw_tx_hex: str
    receipt: Optional[Dict[str, Any]]
    signed_tx: Optional[Dict[str, Any]] = None
    sign_bytes: Optional[bytes] = None


# ---------------------------------------------------------------------------
# Internal helpers (SDK function name shims + RPC helpers)
# ---------------------------------------------------------------------------


def _to_hex_qty(n: int) -> str:
    return hex(int(n))


def _from_hex_qty(h: Union[str, int]) -> int:
    if isinstance(h, int):
        return h
    if isinstance(h, str) and h.startswith("0x"):
        return int(h, 16)
    return int(h)


def _sdk_sign_bytes(tx: Dict[str, Any]) -> bytes:
    """
    Locate the SDK sign-bytes function with a few tolerant aliases.
    """
    for name in (
        "sign_bytes",
        "encode_sign_bytes",
        "encode_signing_bytes",
        "signing_bytes",
    ):
        fn = getattr(tx_encode, name, None)
        if callable(fn):
            return fn(tx)
    raise RuntimeError("omni_sdk.tx.encode is missing a sign-bytes function")


def _sdk_encode_signed_tx(
    tx: Dict[str, Any],
    pubkey: bytes,
    signature: bytes,
    scheme: Optional[str] = None,
) -> bytes:
    """
    Locate the SDK 'package/encode signed tx' function with tolerant aliases.
    """
    candidates = (
        "encode_signed_tx",
        "pack_signed_tx",
        "encode_signed",
        "cbor_encode_signed_tx",
    )
    for name in candidates:
        fn = getattr(tx_encode, name, None)
        if callable(fn):
            try:
                # Prefer explicit scheme if the SDK supports it
                return fn(tx, pubkey, signature, scheme=scheme)  # type: ignore[call-arg]
            except TypeError:
                # Older signature without scheme kw
                return fn(tx, pubkey, signature)  # type: ignore[misc]
    raise RuntimeError("omni_sdk.tx.encode is missing an encode-signed-tx function")


def _sdk_tx_hash(raw_tx_bytes: bytes) -> str:
    """
    Hash a raw CBOR tx using SDK keccak helper if available; fallback to sha256.
    """
    # Preferred: SDK keccak
    for name in ("keccak256_hex", "keccak_hex", "keccak256"):
        fn = getattr(sdk_hash, name, None)
        if callable(fn):
            h = fn(raw_tx_bytes)
            return (
                ("0x" + h) if isinstance(h, str) and not h.startswith("0x") else str(h)
            )
    # Fallback: sha256 (tests that don't check txHash format)
    import hashlib

    return "0x" + hashlib.sha256(raw_tx_bytes).hexdigest()


def _rpc_get_chain_id(rpc: HttpRpcClient) -> Optional[int]:
    return rpc.get_chain_id()


def _rpc_get_nonce(rpc: HttpRpcClient, address: str, tag: str = "pending") -> int:
    try:
        n = rpc.call_first(
            ["omni_getTransactionCount", "eth_getTransactionCount"],
            [address, tag],
        )
        return _from_hex_qty(n)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch nonce for {address}: {e}") from e


def _rpc_gas_price(rpc: HttpRpcClient, default: int = 1_000_000_000) -> int:
    try:
        g = rpc.call_first(["omni_gasPrice", "eth_gasPrice"])
        return _from_hex_qty(g)
    except Exception:
        return int(default)


def _normalize_address(addr: str) -> str:
    if addr_mod is None:
        return addr
    try:
        return addr_mod.normalize(addr)  # type: ignore[attr-defined]
    except Exception:
        return addr


def _addr_of(signer: Any) -> str:
    """
    Best-effort way to get the signer's address (tests only).
    """
    for name in ("address", "addr", "get_address"):
        val = getattr(signer, name, None)
        if callable(val):
            try:
                return val()
            except Exception:
                pass
        elif isinstance(val, str):
            return val
    # As a last resort, allow tests to pass explicit from_addr to builders
    raise AttributeError("Signer does not expose an address() accessor")


def _pubkey_of(signer: Any) -> bytes:
    for name in ("pubkey", "public_key", "public_key_bytes"):
        fn = getattr(signer, name, None)
        if callable(fn):
            v = fn()
            return bytes(v)
        elif isinstance(fn, (bytes, bytearray)):
            return bytes(fn)
    raise AttributeError("Signer does not expose a pubkey() accessor")


def _scheme_of(signer: Any) -> Optional[str]:
    for name in ("scheme", "algorithm", "algo"):
        v = getattr(signer, name, None)
        if isinstance(v, str):
            return v
        if v is not None:
            try:
                return str(v)
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# Build helpers (delegate to omni_sdk.tx.build)
# ---------------------------------------------------------------------------


def build_transfer_tx(
    *,
    from_addr: str,
    to_addr: str,
    amount: int,
    nonce: int,
    chain_id: int,
    gas_limit: Optional[int] = None,
    gas_price: Optional[int] = None,
    memo: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a transfer tx using the SDK builder (with compatible parameter shapes).
    """
    to_addr = _normalize_address(to_addr)
    from_addr = _normalize_address(from_addr)

    # Try the canonical signature; gracefully fall back to variant orders.
    try:
        return tx_build.build_transfer(
            sender=from_addr,
            to=to_addr,
            amount=int(amount),
            nonce=int(nonce),
            chain_id=int(chain_id),
            gas_limit=gas_limit,
            gas_price=gas_price,
            memo=memo,
        )
    except TypeError:
        # Older order or names
        return tx_build.build_transfer(
            from_addr,
            to_addr,
            int(amount),
            int(nonce),
            int(chain_id),
            gas_limit,
            gas_price,
            memo,
        )


def build_call_tx(
    *,
    from_addr: str,
    to_addr: str,
    data: Union[bytes, str],
    nonce: int,
    chain_id: int,
    gas_limit: Optional[int] = None,
    gas_price: Optional[int] = None,
    value: int = 0,
) -> Dict[str, Any]:
    """
    Build a contract call tx (data is already ABI-encoded).
    """
    to_addr = _normalize_address(to_addr)
    from_addr = _normalize_address(from_addr)
    if isinstance(data, str) and data.startswith("0x"):
        data_bytes = bytes.fromhex(data[2:])
    elif isinstance(data, str):
        data_bytes = data.encode("utf-8")
    else:
        data_bytes = bytes(data)

    try:
        return tx_build.build_call(
            sender=from_addr,
            to=to_addr,
            data=data_bytes,
            value=int(value),
            nonce=int(nonce),
            chain_id=int(chain_id),
            gas_limit=gas_limit,
            gas_price=gas_price,
        )
    except TypeError:
        return tx_build.build_call(
            from_addr,
            to_addr,
            data_bytes,
            int(value),
            int(nonce),
            int(chain_id),
            gas_limit,
            gas_price,
        )


def build_deploy_tx(
    *,
    from_addr: str,
    manifest: Dict[str, Any],
    code: Union[bytes, str],
    init_args: Optional[Sequence[Any]] = None,
    nonce: int,
    chain_id: int,
    gas_limit: Optional[int] = None,
    gas_price: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build a deploy tx (manifest + code). init_args are ABI-encoded by the SDK if supported.
    """
    from_addr = _normalize_address(from_addr)
    code_bytes = (
        bytes.fromhex(code[2:])
        if isinstance(code, str) and code.startswith("0x")
        else (code.encode("utf-8") if isinstance(code, str) else bytes(code))
    )

    try:
        return tx_build.build_deploy(
            sender=from_addr,
            manifest=manifest,
            code=code_bytes,
            init_args=list(init_args or []),
            nonce=int(nonce),
            chain_id=int(chain_id),
            gas_limit=gas_limit,
            gas_price=gas_price,
        )
    except TypeError:
        return tx_build.build_deploy(
            from_addr,
            manifest,
            code_bytes,
            list(init_args or []),
            int(nonce),
            int(chain_id),
            gas_limit,
            gas_price,
        )


# ---------------------------------------------------------------------------
# Sign / encode / send
# ---------------------------------------------------------------------------


def sign_tx(
    signer: Any, tx: Dict[str, Any]
) -> Tuple[bytes, bytes, Optional[str], bytes]:
    """
    Produce (raw_tx_bytes, signature, scheme, sign_bytes).

    - Uses omni_sdk.tx.encode for CBOR SignBytes and signed-tx encoding.
    - Extracts pubkey/scheme from the signer (Dilithium3 / SPHINCS+).
    """
    sign_bytes = _sdk_sign_bytes(tx)
    sig = signer.sign(sign_bytes)  # type: ignore[attr-defined]
    pub = _pubkey_of(signer)
    scheme = _scheme_of(signer)
    raw = _sdk_encode_signed_tx(tx, pub, sig, scheme)
    return raw, sig, scheme, sign_bytes


def send_raw(
    rpc: HttpRpcClient,
    raw_tx_bytes: bytes,
    *,
    await_receipt: bool = True,
    timeout: float = 60.0,
) -> SendResult:
    raw_hex = "0x" + raw_tx_bytes.hex()
    tx_hash = rpc.send_raw_transaction(raw_hex)
    receipt = rpc.await_receipt(tx_hash, timeout=timeout) if await_receipt else None
    return SendResult(tx_hash=tx_hash, raw_tx_hex=raw_hex, receipt=receipt)


# ---------------------------------------------------------------------------
# High-level convenience (transfer / call / deploy)
# ---------------------------------------------------------------------------


def send_transfer(
    rpc: HttpRpcClient,
    signer: Any,
    to_addr: str,
    amount: int,
    *,
    gas_limit: Optional[int] = None,
    gas_price: Optional[int] = None,
    nonce: Optional[int] = None,
    chain_id: Optional[int] = None,
    memo: Optional[str] = None,
    await_receipt: bool = True,
    timeout: float = 60.0,
) -> SendResult:
    """
    Build a transfer using SDK, sign with the provided signer, and send via RPC.
    """
    chain_id = chain_id or _rpc_get_chain_id(rpc)
    if chain_id is None:
        raise RuntimeError("Could not determine chainId; pass chain_id= explicitly")
    from_addr = _addr_of(signer)
    nonce = _rpc_get_nonce(rpc, from_addr) if nonce is None else int(nonce)
    gas_price = _rpc_gas_price(rpc) if gas_price is None else int(gas_price)

    tx = build_transfer_tx(
        from_addr=from_addr,
        to_addr=to_addr,
        amount=int(amount),
        nonce=nonce,
        chain_id=int(chain_id),
        gas_limit=gas_limit,
        gas_price=gas_price,
        memo=memo,
    )
    raw, sig, scheme, sbytes = sign_tx(signer, tx)
    res = send_raw(rpc, raw, await_receipt=await_receipt, timeout=timeout)
    res.signed_tx = tx
    res.sign_bytes = sbytes
    return res


def send_call(
    rpc: HttpRpcClient,
    signer: Any,
    to_addr: str,
    data: Union[bytes, str],
    *,
    value: int = 0,
    gas_limit: Optional[int] = None,
    gas_price: Optional[int] = None,
    nonce: Optional[int] = None,
    chain_id: Optional[int] = None,
    await_receipt: bool = True,
    timeout: float = 60.0,
) -> SendResult:
    """
    Build a contract call using SDK (data must be ABI-encoded), sign, and send.
    """
    chain_id = chain_id or _rpc_get_chain_id(rpc)
    if chain_id is None:
        raise RuntimeError("Could not determine chainId; pass chain_id= explicitly")
    from_addr = _addr_of(signer)
    nonce = _rpc_get_nonce(rpc, from_addr) if nonce is None else int(nonce)
    gas_price = _rpc_gas_price(rpc) if gas_price is None else int(gas_price)

    tx = build_call_tx(
        from_addr=from_addr,
        to_addr=to_addr,
        data=data,
        value=int(value),
        nonce=nonce,
        chain_id=int(chain_id),
        gas_limit=gas_limit,
        gas_price=gas_price,
    )
    raw, sig, scheme, sbytes = sign_tx(signer, tx)
    res = send_raw(rpc, raw, await_receipt=await_receipt, timeout=timeout)
    res.signed_tx = tx
    res.sign_bytes = sbytes
    return res


def send_deploy(
    rpc: HttpRpcClient,
    signer: Any,
    manifest: Dict[str, Any],
    code: Union[bytes, str],
    *,
    init_args: Optional[Sequence[Any]] = None,
    gas_limit: Optional[int] = None,
    gas_price: Optional[int] = None,
    nonce: Optional[int] = None,
    chain_id: Optional[int] = None,
    await_receipt: bool = True,
    timeout: float = 120.0,
) -> SendResult:
    """
    Build a deploy tx using SDK, sign, and send. Returns tx hash and receipt.
    """
    chain_id = chain_id or _rpc_get_chain_id(rpc)
    if chain_id is None:
        raise RuntimeError("Could not determine chainId; pass chain_id= explicitly")
    from_addr = _addr_of(signer)
    nonce = _rpc_get_nonce(rpc, from_addr) if nonce is None else int(nonce)
    gas_price = _rpc_gas_price(rpc) if gas_price is None else int(gas_price)

    tx = build_deploy_tx(
        from_addr=from_addr,
        manifest=manifest,
        code=code,
        init_args=list(init_args or []),
        nonce=nonce,
        chain_id=int(chain_id),
        gas_limit=gas_limit,
        gas_price=gas_price,
    )
    raw, sig, scheme, sbytes = sign_tx(signer, tx)
    res = send_raw(rpc, raw, await_receipt=await_receipt, timeout=timeout)
    res.signed_tx = tx
    res.sign_bytes = sbytes
    return res


# ---------------------------------------------------------------------------
# Lightweight gas estimation helpers (fall back to node RPC when needed)
# ---------------------------------------------------------------------------


def estimate_gas_transfer(
    rpc: HttpRpcClient,
    *,
    from_addr: str,
    to_addr: str,
    amount: int,
) -> Optional[int]:
    """
    Try SDK estimator first (if present), otherwise ask the node via estimateGas.
    """
    # SDK estimator
    est = getattr(tx_build, "estimate_gas_transfer", None)
    if callable(est):
        try:
            return int(est(from_addr=from_addr, to=to_addr, amount=int(amount)))
        except TypeError:
            return int(est(from_addr, to_addr, int(amount)))  # type: ignore[misc]
        except Exception:
            pass

    # Node fallback
    try:
        call_obj = {
            "from": _normalize_address(from_addr),
            "to": _normalize_address(to_addr),
            "value": _to_hex_qty(int(amount)),
        }
        r = rpc.call_first(["omni_estimateGas", "eth_estimateGas"], [call_obj])
        return _from_hex_qty(r)
    except Exception:
        return None


def estimate_gas_call(
    rpc: HttpRpcClient,
    *,
    from_addr: str,
    to_addr: str,
    data: Union[bytes, str],
    value: int = 0,
) -> Optional[int]:
    est = getattr(tx_build, "estimate_gas_call", None)
    if callable(est):
        try:
            return int(
                est(from_addr=from_addr, to=to_addr, data=data, value=int(value))
            )
        except TypeError:
            return int(est(from_addr, to_addr, data, int(value)))  # type: ignore[misc]
        except Exception:
            pass

    try:
        if isinstance(data, (bytes, bytearray)):
            data_hex = "0x" + bytes(data).hex()
        elif isinstance(data, str) and data.startswith("0x"):
            data_hex = data
        else:
            data_hex = "0x" + data.encode("utf-8").hex()
        call_obj = {
            "from": _normalize_address(from_addr),
            "to": _normalize_address(to_addr),
            "data": data_hex,
            "value": _to_hex_qty(int(value)),
        }
        r = rpc.call_first(["omni_estimateGas", "eth_estimateGas"], [call_obj])
        return _from_hex_qty(r)
    except Exception:
        return None
