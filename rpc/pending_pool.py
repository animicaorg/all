"""
Animica RPC Pending Pool
========================

A lightweight, in-memory queue used by the RPC layer to accept *pending*
transactions before they are mined or moved into the full mempool service.

Features
--------
- Duplicate suppression by tx-hash.
- TTL-based expiration & periodic purge helper.
- Post-quantum (PQ) signature *precheck*:
  - Verifies signature against canonical SignBytes.
  - Verifies `from` address matches (alg_id || sha3_256(pubkey)) per spec.
  - Rejects obviously malformed transactions fast.
- Thread- and asyncio-safe (uses an internal asyncio.Lock).

This is *not* the full mempool policy engine (see `mempool/`). It’s a thin
accept buffer used by `rpc/methods/tx.py` so clients get immediate feedback.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

# Core / encoding (canonical SignBytes)
from core.encoding import canonical as canonical_enc
from core.encoding import cbor as cbor_enc
from core.types.tx import \
    Tx  # structured Tx object (matches spec/tx_format.cddl)
from core.utils.hash import sha3_256, sha3_256_hex
# PQ primitives (address & signature verification)
from pq.py.address import decode_address as pq_decode_address
from pq.py.address import encode_address as pq_encode_address
from pq.py.verify import verify as pq_verify

from .models import TxView
# Local RPC helpers
from .types import Address, HashHex32, ensure_hash32_hex, is_address

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------
# Errors & results
# --------------------------------------------------------------------------------------


class PendingPoolError(Exception):
    """Base class for pending pool errors."""


class MalformedTx(PendingPoolError):
    """Tx failed canonical decode or missing fields."""


class BadSignature(PendingPoolError):
    """Signature did not verify for provided public key / domain bytes."""


class AddressMismatch(PendingPoolError):
    """`from` address is not consistent with (alg_id, pubkey)."""


@dataclass(frozen=True)
class AddResult:
    """Outcome of adding a tx to the pending pool."""

    hash: HashHex32
    accepted: bool
    duplicate: bool = False
    reason: Optional[str] = None


# --------------------------------------------------------------------------------------
# Internal entry
# --------------------------------------------------------------------------------------


@dataclass
class _Entry:
    tx: Tx
    raw_cbor: bytes
    received_at: float
    expires_at: float

    @property
    def tx_hash_hex(self) -> HashHex32:
        # Canonical tx hash is sha3-256 over the canonical CBOR bytes (envelope as submitted)
        return ensure_hash32_hex("0x" + sha3_256(self.raw_cbor).hex())


# --------------------------------------------------------------------------------------
# Pending Pool
# --------------------------------------------------------------------------------------


class PendingPool:
    """
    In-memory pending-tx pool with TTL, duplicate suppression and PQ signature precheck.
    """

    def __init__(
        self,
        ttl_seconds: int = 300,
        max_items: int = 50_000,
    ) -> None:
        self._ttl = int(ttl_seconds)
        self._max = int(max_items)
        self._entries: Dict[HashHex32, _Entry] = {}
        self._lock = asyncio.Lock()

    # --------------------------
    # Admission
    # --------------------------

    async def add_raw(self, raw_cbor: bytes) -> AddResult:
        """
        Decode canonical CBOR → Tx, run PQ precheck, and insert (dup-suppressed).
        """
        try:
            tx = self._decode_tx(raw_cbor)
        except Exception as e:  # decode/shape errors
            log.debug("pending_pool.add_raw: decode failed", exc_info=e)
            raise MalformedTx(f"CBOR decode/shape error: {e}") from e

        # Precompute hash (over raw CBOR)
        tx_hash = ensure_hash32_hex("0x" + sha3_256(raw_cbor).hex())

        async with self._lock:
            # TTL purge opportunistically
            self._purge_locked()

            if tx_hash in self._entries:
                return AddResult(
                    hash=tx_hash, accepted=False, duplicate=True, reason="duplicate"
                )

            if len(self._entries) >= self._max:
                # Evict oldest expired first, then refuse if still full
                self._purge_locked()
                if len(self._entries) >= self._max:
                    return AddResult(
                        hash=tx_hash,
                        accepted=False,
                        duplicate=False,
                        reason="pool_full",
                    )

            # PQ Precheck (signature + address linkage)
            self._pq_precheck(tx, raw_cbor)

            now = time.time()
            ent = _Entry(
                tx=tx,
                raw_cbor=raw_cbor,
                received_at=now,
                expires_at=now + self._ttl,
            )
            self._entries[tx_hash] = ent
            log.debug(
                "pending_pool: accepted tx %s (entries=%d)", tx_hash, len(self._entries)
            )
            return AddResult(hash=tx_hash, accepted=True, duplicate=False, reason=None)

    async def add_tx(self, tx: Tx, raw_cbor: Optional[bytes] = None) -> AddResult:
        """
        Add an already-decoded Tx. If raw_cbor not provided, it will be re-encoded canonically.
        """
        r = raw_cbor or cbor_enc.dumps(tx.to_cbor_obj())  # Tx implements to_cbor_obj()
        return await self.add_raw(r)

    # --------------------------
    # Queries
    # --------------------------

    async def get(self, tx_hash: str) -> Optional[Tx]:
        tx_hash = ensure_hash32_hex(tx_hash)
        async with self._lock:
            ent = self._entries.get(tx_hash)
            if ent and ent.expires_at > time.time():
                return ent.tx
            return None

    async def get_view(self, tx_hash: str) -> Optional[TxView]:
        tx = await self.get(tx_hash)
        if tx is None:
            return None
        return self._to_txview(tx)

    async def list_hashes(self, limit: int = 1000) -> List[HashHex32]:
        async with self._lock:
            self._purge_locked()
            return list(self._entries.keys())[: int(limit)]

    async def list_views(self, limit: int = 100) -> List[TxView]:
        out: List[TxView] = []
        async with self._lock:
            self._purge_locked()
            for ent in list(self._entries.values())[: int(limit)]:
                out.append(self._to_txview(ent.tx))
        return out

    async def size(self) -> int:
        async with self._lock:
            self._purge_locked()
            return len(self._entries)

    async def remove(self, tx_hash: str) -> bool:
        tx_hash = ensure_hash32_hex(tx_hash)
        async with self._lock:
            return self._entries.pop(tx_hash, None) is not None

    async def purge_expired(self) -> int:
        async with self._lock:
            return self._purge_locked()

    # --------------------------
    # Internals
    # --------------------------

    def _decode_tx(self, raw_cbor: bytes) -> Tx:
        """
        Decode CBOR → dict → Tx dataclass, enforcing canonical form.
        """
        obj = cbor_enc.loads(raw_cbor)  # canonical decoder
        # Tx.from_cbor_obj must validate shape and value domains
        tx = Tx.from_cbor_obj(obj)
        return tx

    def _pq_precheck(self, tx: Tx, raw_cbor: bytes) -> None:
        """
        Verify PQ signature and linkage between `from` and (alg_id, pubkey).
        """
        # 1) Compute canonical SignBytes for transactions
        try:
            sign_bytes: bytes = canonical_enc.tx_sign_bytes(tx)
        except AttributeError:
            # Fallback: many Tx classes also expose a method
            if hasattr(tx, "sign_bytes"):
                sign_bytes = tx.sign_bytes()  # type: ignore[assignment]
            else:
                # As a last resort, derive sign-bytes via canonical module using CBOR + domain
                sign_bytes = canonical_enc.tx_sign_bytes_from_cbor(raw_cbor)

        # 2) Extract signature triplet (alg_id, pubkey, sig) from Tx
        try:
            alg_id: int = int(tx.sig.alg_id)  # e.g., 0x31 for Dilithium3
            pubkey: bytes = bytes(tx.sig.pk)  # raw public key bytes
            signature: bytes = bytes(tx.sig.sig)  # raw signature bytes
        except Exception as e:
            raise MalformedTx(f"missing signature fields: {e}") from e

        # 3) Verify signature against domain-separated sign-bytes
        ok = pq_verify(alg_id, pubkey, sign_bytes, signature)
        if not ok:
            raise BadSignature("post-quantum signature verification failed")

        # 4) Verify `from` address matches (alg_id || sha3_256(pubkey)) with bech32m hrp
        if not isinstance(tx.from_addr, str) or not is_address(tx.from_addr):
            raise MalformedTx("invalid or missing `from` address")

        # hrp is embedded in bech32m address string; we only need to re-encode and compare
        # We deliberately *do not* normalize casing beyond bech32m rules (all lowercase).
        expected_addr: Address = pq_encode_address(alg_id, pubkey)
        if expected_addr != tx.from_addr:
            # Some deployments may choose to allow custom HRPs per chain;
            # try decoding then re-encoding with the same HRP as the provided `from`.
            try:
                hrp, _alg2, _pkhash = pq_decode_address(tx.from_addr)
                expected_with_hrp: Address = (
                    pq_encode_address(alg_id, pubkey)
                    if hrp == "anim"
                    else pq_encode_address(alg_id, pubkey).replace(
                        "anim1", f"{hrp}1", 1
                    )
                )
            except Exception:
                expected_with_hrp = expected_addr

            if expected_with_hrp != tx.from_addr:
                raise AddressMismatch("`from` address does not match (alg_id, pubkey)")

    def _purge_locked(self) -> int:
        """Remove expired entries. Caller must hold _lock."""
        now = time.time()
        to_del: List[HashHex32] = [
            h for h, ent in self._entries.items() if ent.expires_at <= now
        ]
        for h in to_del:
            self._entries.pop(h, None)
        if to_del:
            log.debug("pending_pool: purged %d expired entries", len(to_del))
        return len(to_del)

    # --------------------------
    # View conversion
    # --------------------------

    def _to_txview(self, tx: Tx) -> TxView:
        """
        Convert a core.types.tx.Tx into the RPC-facing TxView.

        Assumes Tx exposes fields named per spec:
          - hash_hex() or hash property (we recompute when needed)
          - chain_id, from_addr, to_addr, nonce, gas_limit, gas_price, value,
            type, data, access_list, sig.{alg_id,sig}
          - inclusion pointers are None for pending
        """
        # Compute hash from canonical CBOR (stable & matches add_raw path)
        raw = cbor_enc.dumps(tx.to_cbor_obj())
        tx_hash_hex = "0x" + sha3_256(raw).hex()

        # Access-list view
        access_list = None
        if getattr(tx, "access_list", None):
            # Normalize items to the TxView AccessListItem shape
            items = []
            for it in tx.access_list:
                addr = it.address if isinstance(it.address, str) else str(it.address)
                storage_keys = [
                    k if isinstance(k, str) else f"0x{k.hex()}" for k in it.storage_keys
                ]
                items.append({"address": addr, "storageKeys": storage_keys})
            access_list = items

        # Build the pydantic TxView
        tv = TxView.model_validate(
            {
                "hash": tx_hash_hex,
                "chainId": tx.chain_id,
                "from": tx.from_addr,
                "to": getattr(tx, "to_addr", None),
                "nonce": tx.nonce,
                "gasLimit": tx.gas_limit,
                "gasPrice": tx.gas_price,
                "value": (
                    getattr(tx, "value_hex", "0x00")
                    if hasattr(tx, "value_hex")
                    else getattr(tx, "value", "0x00")
                ),
                "type": tx.type,  # "transfer" | "deploy" | "call"
                "data": (
                    getattr(tx, "data_hex", "0x")
                    if hasattr(tx, "data_hex")
                    else getattr(tx, "data", "0x")
                ),
                "accessList": access_list,
                "algId": int(tx.sig.alg_id),
                "sig": "0x" + bytes(tx.sig.sig).hex(),
                "blockHash": None,
                "blockNumber": None,
                "transactionIndex": None,
            }
        )
        return tv


# --------------------------------------------------------------------------------------
# Convenience (sync wrapper)
# --------------------------------------------------------------------------------------


def new_pool(ttl_seconds: int = 300, max_items: int = 50_000) -> PendingPool:
    """
    Quick factory to create a pending pool.
    """
    return PendingPool(ttl_seconds=ttl_seconds, max_items=max_items)
