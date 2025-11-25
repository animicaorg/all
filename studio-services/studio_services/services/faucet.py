"""
Faucet service: controlled drip with optional hot key and storage-backed rate limits.

Design goals
------------
- Optional: If FAUCET_KEY is not configured, the faucet is disabled (explicit error).
- Safe: Enforce per-IP and per-address buckets in addition to any router-level limits.
- Minimal coupling: Delegate chain calls to NodeRPC adapter; when absent, fall back to
  omni_sdk to build+sign a transfer with the hot key.
- Observability: Structured logs; surface reason codes in diagnostics when denied.

Public API
----------
drip(node, req, *, client_ip=None) -> FaucetResponse
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict
from typing import Any, Optional, Tuple, Dict, Callable

from studio_services.errors import ApiError, BadRequest
from studio_services.models.faucet import FaucetRequest, FaucetResponse
from studio_services.adapters.node_rpc import NodeRPC
from studio_services.adapters import pq_addr as addr_adapter  # address validation
from studio_services import config as cfg_mod

# storage
from studio_services.storage import sqlite as storage_sqlite  # type: ignore

log = logging.getLogger(__name__)

# ------------------------ Config helpers ------------------------

def _get_faucet_cfg():
    """
    Resolve faucet-related settings from config/env with safe defaults.
    Expected (if present):
      - FAUCET_KEY: hot key material (see _make_signer)
      - CHAIN_ID: default chain id (int or str parsable)
      - FAUCET_MAX_DRIP: max units per drip (int)
      - FAUCET_DEFAULT_DRIP: default units when amount not provided (int)
      - FAUCET_WINDOW_SECONDS: rate window (default 86400)
      - FAUCET_MAX_PER_ADDR: allowed drips per window per address (default 1)
      - FAUCET_MAX_PER_IP: allowed drips per window per IP (default 2)
    """
    # Prefer config module attributes; fall back to environment.
    get = getattr(cfg_mod, "get", None)
    def _cfg(name: str, default: Optional[str] = None) -> Optional[str]:
        if callable(get):
            try:
                v = get(name)
                if v is not None:
                    return str(v)
            except Exception:
                pass
        return os.getenv(name, default)

    return {
        "FAUCET_KEY": _cfg("FAUCET_KEY"),
        "CHAIN_ID": _cfg("CHAIN_ID"),
        "FAUCET_MAX_DRIP": int(_cfg("FAUCET_MAX_DRIP", "0") or "0") or 0,
        "FAUCET_DEFAULT_DRIP": int(_cfg("FAUCET_DEFAULT_DRIP", "0") or "0") or 0,
        "FAUCET_WINDOW_SECONDS": int(_cfg("FAUCET_WINDOW_SECONDS", "86400") or "86400"),
        "FAUCET_MAX_PER_ADDR": int(_cfg("FAUCET_MAX_PER_ADDR", "1") or "1"),
        "FAUCET_MAX_PER_IP": int(_cfg("FAUCET_MAX_PER_IP", "2") or "2"),
    }


# ------------------------ Rate limiting ------------------------

class _RateStore:
    """
    Thin compatibility wrapper over storage_sqlite rate counter APIs.

    We support either:
      - store.consume(bucket, tokens, window_seconds, capacity) -> (ok, remaining, reset_ts)
      - store.bump(bucket, now, window_seconds) / count(bucket, now, window_seconds)
      - raw get/set with timestamps
    """

    def __init__(self) -> None:
        # Try to instantiate a store object; else use module-level fns.
        for name in ("RateStore", "Store", "Database"):
            if hasattr(storage_sqlite, name):
                try:
                    self._store = getattr(storage_sqlite, name)()  # type: ignore
                    break
                except Exception:  # pragma: no cover
                    self._store = storage_sqlite  # type: ignore
                    break
        else:
            self._store = storage_sqlite  # type: ignore

    def consume(
        self,
        bucket: str,
        *,
        window_seconds: int,
        capacity: int,
        tokens: int = 1,
        now: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        now_i = int(now or time.time())
        # Preferred API
        if hasattr(self._store, "consume"):
            ok, remaining, reset_ts = self._store.consume(  # type: ignore
                bucket, tokens=tokens, window_seconds=window_seconds, capacity=capacity, now=now_i
            )
            return bool(ok), int(remaining), int(reset_ts)
        # Fallback: bump/count
        count = 0
        reset_ts = now_i + window_seconds
        if hasattr(self._store, "count"):
            count = int(self._store.count(bucket, now=now_i, window_seconds=window_seconds))  # type: ignore
        elif hasattr(self._store, "get_count"):
            count = int(self._store.get_count(bucket, now_i, window_seconds))  # type: ignore
        ok = (count + tokens) <= capacity
        if ok:
            if hasattr(self._store, "bump"):
                self._store.bump(bucket, now=now_i, window_seconds=window_seconds)  # type: ignore
            elif hasattr(self._store, "increment"):
                self._store.increment(bucket, now_i, window_seconds)  # type: ignore
        remaining = max(0, capacity - min(capacity, count + (tokens if ok else 0)))
        return ok, remaining, reset_ts


def _rate_bucket_addr(addr: str) -> str:
    return f"faucet:addr:{addr.lower()}"


def _rate_bucket_ip(ip: str) -> str:
    return f"faucet:ip:{ip}"


# ------------------------ Signing & send helpers ------------------------

def _make_signer(secret: str):
    """
    Return a tuple (addr_fn, sign_fn, pubkey_fn, alg_id_fn) built on omni_sdk,
    configured from a hot secret. Accepts explicit prefixes:

      "dilithium3:<hexseed>" or "sphincs:<hexseed>"

    If prefix is absent, defaults to Dilithium3.

    Raises BadRequest if omni_sdk is unavailable or secret invalid.
    """
    try:
        from omni_sdk.wallet.signer import DilithiumSigner, SphincsSigner  # type: ignore
        from omni_sdk.address import address_from_pubkey  # type: ignore
    except Exception as e:  # pragma: no cover
        raise BadRequest(f"Server missing omni_sdk for faucet signing: {e}")

    algo = "dilithium3"
    seed_hex = secret
    if ":" in secret:
        algo, seed_hex = secret.split(":", 1)
        algo = algo.strip().lower()
    seed_hex = seed_hex.strip().lower().removeprefix("0x")

    if algo not in ("dilithium3", "sphincs", "sphincs_shake_128s", "sphincs+"):
        raise BadRequest(f"Unsupported faucet key algo '{algo}'")

    signer = DilithiumSigner.from_seed_hex(seed_hex) if algo == "dilithium3" else SphincsSigner.from_seed_hex(seed_hex)

    def addr_fn() -> str:
        return address_from_pubkey(signer.alg_id(), signer.public_key())

    def sign_fn(msg: bytes, domain: Optional[bytes] = None) -> bytes:
        return signer.sign(msg, domain=domain)

    def pubkey_fn() -> bytes:
        return signer.public_key()

    def alg_id_fn() -> int:
        return signer.alg_id()

    return addr_fn, sign_fn, pubkey_fn, alg_id_fn


def _sdk_send_transfer(
    node: NodeRPC,
    *,
    secret: str,
    to_address: str,
    amount: int,
    chain_id: Optional[int],
) -> str:
    """
    Build+sign+send a transfer using omni_sdk if the NodeRPC does not offer a convenience API.
    """
    try:
        from omni_sdk.tx.build import transfer as build_transfer  # type: ignore
        from omni_sdk.tx.encode import sign_bytes as tx_sign_bytes, encode_signed as encode_signed_tx  # type: ignore
        from omni_sdk.utils.cbor import to_bytes as _to_cbor_bytes  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ApiError(f"Cannot send faucet transfer without SDK support: {e}")

    addr_fn, sign_fn, pubkey_fn, alg_id_fn = _make_signer(secret)
    from_addr = addr_fn()

    # Resolve live params
    get_chain_id = getattr(node, "get_chain_id", None)
    live_chain_id = None
    if callable(get_chain_id):
        try:
            live_chain_id = int(get_chain_id())
        except Exception:
            live_chain_id = None

    final_chain_id = int(chain_id or live_chain_id or 0) or None
    if final_chain_id is None:
        raise BadRequest("Unable to determine chainId for faucet transfer")

    # Nonce & gas price
    get_nonce = getattr(node, "get_nonce", None)
    nonce = 0
    if callable(get_nonce):
        nonce = int(get_nonce(from_addr))

    get_gas_price = getattr(node, "get_gas_price", None)
    gas_price = int(get_gas_price()) if callable(get_gas_price) else 1

    # Build unsigned tx dict
    tx = build_transfer(
        from_address=from_addr,
        to_address=to_address,
        amount=int(amount),
        chain_id=final_chain_id,
        nonce=nonce,
        gas_price=gas_price,
    )

    sb = tx_sign_bytes(tx)
    sig = sign_fn(sb, domain=b"animica/tx")
    raw = encode_signed_tx(
        tx,
        pubkey=pubkey_fn(),
        alg_id=alg_id_fn(),
        signature=sig,
    )
    # Submit
    send_raw = getattr(node, "send_raw_transaction", None) or getattr(node, "tx_sendRawTransaction", None)
    if not callable(send_raw):
        raise ApiError("NodeRPC lacks send_raw_transaction method")
    tx_hash = send_raw(raw)
    return str(tx_hash)


# ------------------------ Faucet service ------------------------

def drip(
    node: NodeRPC,
    req: FaucetRequest,
    *,
    client_ip: Optional[str] = None,
) -> FaucetResponse:
    """
    Controlled drip entrypoint.

    Enforces:
      - Faucet enabled (hot key present)
      - Address validity
      - Per-address & per-IP buckets
      - Amount caps (per config)
    """
    fc = _get_faucet_cfg()
    secret = fc["FAUCET_KEY"]
    if not secret:
        # Surface a typed error if the project defines FaucetOff
        try:
            from studio_services.errors import FaucetOff  # type: ignore
            raise FaucetOff("Faucet is disabled")
        except Exception:
            raise BadRequest("Faucet is disabled (no FAUCET_KEY configured)")

    # Validate destination
    if not req.address:
        raise BadRequest("address is required")
    try:
        validator = getattr(addr_adapter, "validate", None) or getattr(addr_adapter, "validate_address", None)
        if callable(validator):
            validator(req.address)
    except Exception as e:
        raise BadRequest(f"Invalid address: {e}")

    # Determine amount
    max_drip = int(fc["FAUCET_MAX_DRIP"] or 0) or 0
    default_drip = int(fc["FAUCET_DEFAULT_DRIP"] or 0) or max(1, max_drip or 1)
    amount = int(req.amount or default_drip)
    if amount <= 0:
        raise BadRequest("amount must be > 0")
    if max_drip and amount > max_drip:
        amount = max_drip  # clip

    # Rate limits
    window = int(fc["FAUCET_WINDOW_SECONDS"])
    max_per_addr = int(fc["FAUCET_MAX_PER_ADDR"])
    max_per_ip = int(fc["FAUCET_MAX_PER_IP"])

    rs = _RateStore()
    ok_addr, remaining_addr, reset_addr = rs.consume(_rate_bucket_addr(req.address), window_seconds=window, capacity=max_per_addr)
    if not ok_addr:
        raise BadRequest(f"address quota exceeded; try again after {reset_addr}")

    if client_ip:
        ok_ip, remaining_ip, reset_ip = rs.consume(_rate_bucket_ip(client_ip), window_seconds=window, capacity=max_per_ip)
        if not ok_ip:
            # Roll back address token (best-effort) if IP fails
            try:
                rs.consume(_rate_bucket_addr(req.address), window_seconds=window, capacity=max_per_addr, tokens=-1)
            except Exception:
                pass
            raise BadRequest(f"ip quota exceeded; try again after {reset_ip}")

    # Optional: Node convenience path
    tx_hash: Optional[str] = None
    try:
        send_from_secret = getattr(node, "send_transfer_from_secret", None)
        if callable(send_from_secret):
            tx_hash = str(send_from_secret(secret, req.address, int(amount), chain_id=fc["CHAIN_ID"]))
    except Exception as e:
        log.info("NodeRPC send_transfer_from_secret path failed, falling back to SDK: %s", e)

    if not tx_hash:
        tx_hash = _sdk_send_transfer(
            node,
            secret=secret,
            to_address=req.address,
            amount=amount,
            chain_id=int(fc["CHAIN_ID"]) if fc["CHAIN_ID"] else None,
        )

    # Build response
    resp = FaucetResponse(
        address=req.address,
        amount=int(amount),
        tx_hash=str(tx_hash),
        message="drip accepted",
        limits={
            "window_seconds": window,
            "addr_remaining": remaining_addr,
            "ip_remaining": (remaining_ip if client_ip else None),
        },
    )
    log.info("faucet.drip ok addr=%s amount=%s tx=%s", req.address, amount, tx_hash)
    return resp


__all__ = ["drip"]
