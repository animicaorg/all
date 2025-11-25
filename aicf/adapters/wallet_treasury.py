from __future__ import annotations

"""
aicf.adapters.wallet_treasury
=============================

Thin adapter that can **sign & submit treasury transfers** when AICF maintains
L2 accounting but payouts ultimately move funds on the core chain.

Design goals
------------
- Post-quantum friendly: caller supplies a Signer with an `alg_id` (e.g.
  "dilithium3" or "sphincs+" ) and a `sign(bytes)->bytes` method.
- Transport-agnostic: caller supplies a CoreRPC facade with the minimal methods
  we need (`get_account_nonce`, `broadcast_tx`, optional `estimate_fee`).
- Deterministic SignBytes: domain-separated SHA3-256 over a canonical encoding
  of the unsigned tx skeleton.
- Safe defaults: if the RPC cannot estimate a fee, we apply a simple fallback.

Notes
-----
* This module intentionally avoids a hard dependency on a specific CBOR library.
  If `cbor2` is available, we use canonical CBOR; otherwise we fall back to
  canonical JSON (sorted keys) for SignBytes and raw submission. Networks that
  require CBOR on the wire should ensure `cbor2` (or a compatible encoder) is
  installed on the node that uses this adapter.
"""

import time
import json
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from aicf.errors import AICFError


# ---- Optional encoders ---------------------------------------------------------

try:
    import cbor2  # type: ignore
    _HAS_CBOR2 = True
except Exception:  # pragma: no cover - optional
    cbor2 = None  # type: ignore
    _HAS_CBOR2 = False


def _encode_canonical(obj: Any) -> bytes:
    """Encode using canonical CBOR if available, else canonical JSON."""
    if _HAS_CBOR2:
        # cbor2 supports canonical form
        return cbor2.dumps(obj, canonical=True)
    # Fallback: canonical JSON (sorted keys, no spaces)
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---- Protocols (DI) ------------------------------------------------------------

@runtime_checkable
class Signer(Protocol):
    """PQ signer interface injected by the caller."""

    alg_id: str        # e.g., "dilithium3" | "sphincs+" (policy-defined)
    address: str       # bech32m "anim1..." or hex-raw depending on network

    def sign(self, message: bytes) -> bytes:
        """Return a signature over message with domain separation handled by the caller."""


@runtime_checkable
class CoreRPC(Protocol):
    """Minimal RPC surface used by this adapter."""

    def get_account_nonce(self, address: str) -> int:
        """Return the current nonce for the given address."""

    def estimate_fee(self, tx_skeleton: Dict[str, Any]) -> int:
        """Return a fee/tip value in the chain's base units (may raise NotImplementedError)."""

    def broadcast_tx(self, raw_tx: bytes) -> str:
        """Broadcast a raw transaction (CBOR or JSON bytes). Returns tx hash (hex/bech)."""


# ---- Data ----------------------------------------------------------------------

DOMAIN_TREASURY_TX = b"ANIMICA/tx/treasury_transfer/v1"
DEFAULT_FEE_FALLBACK = 1_000  # conservative default if we can't estimate (network-specific)


@dataclass(frozen=True)
class TreasuryTransfer:
    """Unsigned, chain-agnostic skeleton for a treasury transfer tx."""
    chainId: int
    kind: str                # "treasury_transfer"
    sender: str              # treasury address (bech32m or hex)
    to: str                  # recipient/provider address
    amount: int              # base units (int)
    nonce: int               # sequential
    fee: int                 # base units (tip or total fee per policy)
    timestamp: int           # seconds since epoch (advisory/anti-replay per policy)

    def to_obj(self) -> Dict[str, Any]:
        return {
            "chainId": self.chainId,
            "kind": self.kind,
            "from": self.sender,
            "to": self.to,
            "amount": int(self.amount),
            "nonce": int(self.nonce),
            "fee": int(self.fee),
            "timestamp": int(self.timestamp),
        }


def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def _domain_separated_hash(domain: bytes, payload: bytes) -> bytes:
    # H = SHA3-256( len(domain)||domain || payload )
    # Simple length-prefix prevents accidental concatenation collisions.
    dl = len(domain).to_bytes(4, "big")
    return _sha3_256(dl + domain + payload)


# ---- Adapter -------------------------------------------------------------------

class TreasuryWalletAdapter:
    """
    Helper to build, sign, and submit treasury transfers.

    Example:
        rpc = MyCoreRPC(...)
        signer = MyPQSigner(...)
        wallet = TreasuryWalletAdapter(rpc, chain_id=1337, treasury_address="anim1...")
        tx_hash = wallet.transfer(to="anim1xyz...", amount=123_456)
    """

    def __init__(
        self,
        rpc: CoreRPC,
        chain_id: int,
        treasury_address: str,
    ) -> None:
        self.rpc = rpc
        self.chain_id = int(chain_id)
        self.treasury_address = treasury_address

    # -- public high-level ------------------------------------------------------

    def transfer(
        self,
        to: str,
        amount: int,
        signer: Signer,
        fee: Optional[int] = None,
        nonce: Optional[int] = None,
        timestamp: Optional[int] = None,
    ) -> str:
        """
        Build, sign, and broadcast a treasury transfer. Returns tx hash.

        Args:
            to: recipient address
            amount: base units
            signer: PQ signer with alg_id/address bound to the treasury account
            fee: optional explicit fee; if None we try rpc.estimate_fee or fallback
            nonce: optional explicit nonce; if None we query rpc.get_account_nonce
            timestamp: optional override; default = now (int seconds)
        """
        if amount <= 0:
            raise AICFError("amount must be positive")

        if signer.address != self.treasury_address:
            # Safety: ensure the supplied signer corresponds to treasury
            raise AICFError("signer address does not match treasury address")

        if nonce is None:
            nonce = self.rpc.get_account_nonce(self.treasury_address)

        skel = self._build_skeleton(
            to=to,
            amount=amount,
            nonce=nonce,
            fee=self._resolve_fee(fee, to, amount, nonce),
            timestamp=timestamp if timestamp is not None else int(time.time()),
        )

        raw = self._sign_and_encode(skel, signer)
        tx_hash = self.rpc.broadcast_tx(raw)
        return tx_hash

    # -- building/signing -------------------------------------------------------

    def _build_skeleton(
        self, to: str, amount: int, nonce: int, fee: int, timestamp: int
    ) -> TreasuryTransfer:
        return TreasuryTransfer(
            chainId=self.chain_id,
            kind="treasury_transfer",
            sender=self.treasury_address,
            to=to,
            amount=int(amount),
            nonce=int(nonce),
            fee=int(fee),
            timestamp=int(timestamp),
        )

    def _sign_and_encode(self, tx: TreasuryTransfer, signer: Signer) -> bytes:
        unsigned_obj = tx.to_obj()
        sign_bytes = _encode_canonical(unsigned_obj)
        digest = _domain_separated_hash(DOMAIN_TREASURY_TX, sign_bytes)
        sig = signer.sign(digest)

        envelope = {
            "tx": unsigned_obj,
            "signature": sig if _HAS_CBOR2 else sig.hex(),  # CBOR can carry bytes directly
            "alg": signer.alg_id,
            "from": signer.address,
        }
        return _encode_canonical(envelope)

    def _resolve_fee(self, fee: Optional[int], to: str, amount: int, nonce: int) -> int:
        if fee is not None:
            return int(fee)
        skel = {
            "chainId": self.chain_id,
            "kind": "treasury_transfer",
            "from": self.treasury_address,
            "to": to,
            "amount": int(amount),
            "nonce": int(nonce),
            "timestamp": int(time.time()),
        }
        try:
            est = self.rpc.estimate_fee(skel)
            if isinstance(est, int) and est > 0:
                return est
        except Exception:
            pass
        return DEFAULT_FEE_FALLBACK


__all__ = [
    "Signer",
    "CoreRPC",
    "TreasuryTransfer",
    "TreasuryWalletAdapter",
    "DOMAIN_TREASURY_TX",
]
