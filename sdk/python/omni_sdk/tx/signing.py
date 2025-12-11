"""
Shared transaction signing helpers.

This module centralizes the canonical steps for computing Animica
transaction sign-bytes, invoking a PQ signer with chain_id domain
separation, and packing the resulting signature into the wire-format
envelope expected by the node.

The helpers here are intentionally minimal and reusable by both the SDK
and the CLI so that all transaction signing flows share the exact same
logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from omni_sdk.tx.encode import pack_signed, sign_bytes


@runtime_checkable
class TxSigner(Protocol):
    """Protocol for signers capable of Animica transaction signing."""

    alg_id: int
    public_key: bytes

    def sign_tx(self, message: bytes, chain_id: int) -> bytes:
        """Sign CBOR-encoded transaction body bytes with chain_id separation."""


@dataclass(frozen=True)
class SignedTx:
    """Result of signing a transaction."""

    sign_bytes: bytes
    signature: bytes
    raw_tx: bytes


def sign_transaction(tx: object, signer: TxSigner, chain_id: int) -> SignedTx:
    """
    Sign a transaction using the provided signer and pack into a raw envelope.

    Parameters
    ----------
    tx : object
        Transaction object compatible with omni_sdk.tx.encode.canonical_body_dict.
    signer : TxSigner
        Signer implementing alg_id, public_key, and sign_tx(message, chain_id).
    chain_id : int
        Chain ID for domain separation.

    Returns
    -------
    SignedTx
        Container with the sign-bytes, raw signature, and packed CBOR envelope.
    """

    msg = sign_bytes(tx)
    sig = signer.sign_tx(msg, chain_id)
    raw = pack_signed(
        tx,
        signature=sig,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )
    return SignedTx(sign_bytes=msg, signature=sig, raw_tx=raw)

