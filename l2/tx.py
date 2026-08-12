"""L2 transaction envelope, per-type payloads, canonical bytes, signing, hashing.

Wire layout of a signed L2 tx (all via :mod:`l2.codec`, so bijective):

    ── body (also the signing preimage input) ──
    version:        uvarint
    l2_chain_id:    uvarint
    tx_type:        uvarint
    sender:         32B
    nonce:          uvarint
    fee:            uvarint            (nanos, paid by sender)
    expiry:         uvarint            (L2 batch height after which invalid; 0 = none)
    payload:        type-specific (see _encode_payload)
    ── envelope ──
    sig_scheme:     uvarint
    pubkey:         1952B              (ML-DSA-65; address = sha3_256(alg||pubkey)[..32])
    signature:      3309B

The signing hash binds domain + L2 chain id via :func:`l2.tx.signing_hash`, which
delegates to the L1 ``pq.py.sign.build_sign_bytes`` so L2 and L1 share one audited
domain-separation construction. Because the signed bytes are exactly the body
bytes (never the envelope), a tx cannot be signed over one thing and broadcast as
another, and the pubkey must hash to ``sender`` or verification is refused.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import codec
from .constants import (
    PROTOCOL_MINTED_TYPES,
    SIGN_DOMAIN,
    SigScheme,
    TxType,
    USER_SIGNED_TYPES,
)

# ML-DSA-65 alg id, identical to L1's canonical scheme (0x1003). The address of
# an account is sha3_256(u16_be(alg_id) || pubkey) truncated to 32B — the same
# derivation the L1 wallet/address code uses, so an L1 anim1… account maps to the
# same 32-byte key on L2.
ALG_ID_ML_DSA_65 = 0x1003


def address_from_pubkey(pubkey: bytes, alg_id: int = ALG_ID_ML_DSA_65) -> bytes:
    """Derive the 32-byte account key from a public key. Matches pq/py/address."""
    payload = alg_id.to_bytes(2, "big") + pubkey
    return hashlib.sha3_256(payload).digest()


# ── payloads ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TransferPayload:
    recipient: bytes
    amount: int
    memo: bytes = b""  # opaque for TRANSFER; Animica-Pay ref for PAY


@dataclass(frozen=True)
class WithdrawPayload:
    # L1 account that will be able to claim the unlocked ANM.
    l1_recipient: bytes
    amount: int


@dataclass(frozen=True)
class DepositClaimPayload:
    # Protocol-minted: credits `beneficiary` from a finalized L1 deposit.
    beneficiary: bytes
    amount: int
    deposit_id: bytes  # unique per L1 deposit; enforced no-double-claim by bridge


@dataclass(frozen=True)
class EscrowOpenPayload:
    escrow_id: bytes  # 32B caller-chosen unique id (scoped to sender)
    beneficiary: bytes
    amount: int
    refund_after: int  # L2 height after which sender may refund; 0 = never auto


@dataclass(frozen=True)
class EscrowRefPayload:
    escrow_id: bytes  # for RELEASE / REFUND


@dataclass(frozen=True)
class AgentPaymentPayload:
    provider: bytes
    amount: int
    agent_id: bytes  # 32B agent identity
    task_hash: bytes  # 32B binding to the off-chain task/receipt


@dataclass(frozen=True)
class InferencePaymentPayload:
    provider: bytes
    amount: int
    request_hash: bytes  # 32B AICF inference-request id
    model_id: bytes  # up to 32B model identifier (right-padded/opaque)


@dataclass(frozen=True)
class BatchPaymentPayload:
    payments: List[Tuple[bytes, int]]  # (recipient, amount) — one signature authorizes all


# ── envelope ─────────────────────────────────────────────────────────────────


@dataclass
class L2Tx:
    version: int
    l2_chain_id: int
    tx_type: TxType
    sender: bytes
    nonce: int
    fee: int
    expiry: int
    payload: object
    sig_scheme: SigScheme = SigScheme.ML_DSA_65
    pubkey: bytes = b""
    signature: bytes = b""

    # ── body (signing preimage input) ──
    def body_bytes(self) -> bytes:
        out = bytearray()
        codec.write_uvarint(out, self.version)
        codec.write_uvarint(out, self.l2_chain_id)
        codec.write_uvarint(out, int(self.tx_type))
        codec.write_addr(out, self.sender)
        codec.write_uvarint(out, self.nonce)
        codec.write_amount(out, self.fee)
        codec.write_uvarint(out, self.expiry)
        _encode_payload(out, self.tx_type, self.payload)
        return bytes(out)

    # ── full signed envelope ──
    def encode(self) -> bytes:
        out = bytearray(self.body_bytes())
        codec.write_uvarint(out, int(self.sig_scheme))
        codec.write_pubkey(out, self.pubkey)
        codec.write_sig(out, self.signature)
        data = bytes(out)
        codec.guard_tx_size(data)
        return data

    def txid(self) -> bytes:
        """Consensus id = sha3_256(full signed envelope). Signature-inclusive,
        matching L1 txid semantics (two signings of one body get distinct ids)."""
        return hashlib.sha3_256(self.encode()).digest()

    def signing_hash(self) -> bytes:
        return signing_hash_for(self.body_bytes(), self.l2_chain_id)


def signing_hash_for(body_bytes: bytes, l2_chain_id: int) -> bytes:
    """Domain-separated signing digest. Delegates to the L1 build_sign_bytes so
    both layers share one audited construction; the L2 domain tag + L2 chain id
    make L1<->L2 and cross-instance replay impossible."""
    try:
        from pq.py.sign import build_sign_bytes  # type: ignore

        return build_sign_bytes(
            body_bytes,
            domain=SIGN_DOMAIN,
            chain_id=l2_chain_id,
            fork_id=None,
            alg_id=ALG_ID_ML_DSA_65,
            context=b"",
            prehash="sha3-512",
        )
    except Exception:
        # Deterministic, domain-separated fallback with the identical field
        # order build_sign_bytes uses, so a node without the L1 package on its
        # path still computes the same digest.
        tag = b"animica:sign/v1"
        parts = [
            tag,
            SIGN_DOMAIN,
            _uv(l2_chain_id),
            b"",  # fork_id absent -> zero-length segment
            _uv(ALG_ID_ML_DSA_65),
            b"",  # context
            body_bytes,
        ]
        raw = bytearray()
        for p in parts:
            raw += len(p).to_bytes(8, "big") + p
        return hashlib.sha3_512(bytes(raw)).digest()


def _uv(value: int) -> bytes:
    out = bytearray()
    codec.write_uvarint(out, value)
    return bytes(out)


# ── payload (de)serialization ────────────────────────────────────────────────


def _encode_payload(out: bytearray, tx_type: TxType, p: object) -> None:
    if tx_type in (TxType.TRANSFER, TxType.PAY):
        assert isinstance(p, TransferPayload)
        codec.write_addr(out, p.recipient)
        codec.write_amount(out, p.amount)
        codec.write_memo(out, p.memo)
    elif tx_type == TxType.WITHDRAW or tx_type == TxType.FORCED_WITHDRAW:
        assert isinstance(p, WithdrawPayload)
        codec.write_addr(out, p.l1_recipient)
        codec.write_amount(out, p.amount)
    elif tx_type == TxType.DEPOSIT_CLAIM:
        assert isinstance(p, DepositClaimPayload)
        codec.write_addr(out, p.beneficiary)
        codec.write_amount(out, p.amount)
        codec.write_hash(out, p.deposit_id)
    elif tx_type == TxType.ESCROW_OPEN:
        assert isinstance(p, EscrowOpenPayload)
        codec.write_hash(out, p.escrow_id)
        codec.write_addr(out, p.beneficiary)
        codec.write_amount(out, p.amount)
        codec.write_uvarint(out, p.refund_after)
    elif tx_type in (TxType.ESCROW_RELEASE, TxType.ESCROW_REFUND):
        assert isinstance(p, EscrowRefPayload)
        codec.write_hash(out, p.escrow_id)
    elif tx_type == TxType.AGENT_PAYMENT:
        assert isinstance(p, AgentPaymentPayload)
        codec.write_addr(out, p.provider)
        codec.write_amount(out, p.amount)
        codec.write_hash(out, p.agent_id)
        codec.write_hash(out, p.task_hash)
    elif tx_type == TxType.INFERENCE_PAYMENT:
        assert isinstance(p, InferencePaymentPayload)
        codec.write_addr(out, p.provider)
        codec.write_amount(out, p.amount)
        codec.write_hash(out, p.request_hash)
        codec.write_hash(out, p.model_id)
    elif tx_type == TxType.BATCH_PAYMENT:
        assert isinstance(p, BatchPaymentPayload)
        codec.write_recipients(out, p.payments)
    else:
        raise codec.CodecError(f"unknown tx_type {tx_type}")


def _decode_payload(buf: memoryview, pos: int, tx_type: TxType) -> Tuple[object, int]:
    if tx_type in (TxType.TRANSFER, TxType.PAY):
        recipient, pos = codec.read_addr(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        memo, pos = codec.read_memo(buf, pos)
        return TransferPayload(recipient, amount, memo), pos
    if tx_type in (TxType.WITHDRAW, TxType.FORCED_WITHDRAW):
        l1_recipient, pos = codec.read_addr(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        return WithdrawPayload(l1_recipient, amount), pos
    if tx_type == TxType.DEPOSIT_CLAIM:
        beneficiary, pos = codec.read_addr(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        deposit_id, pos = codec.read_hash(buf, pos)
        return DepositClaimPayload(beneficiary, amount, deposit_id), pos
    if tx_type == TxType.ESCROW_OPEN:
        escrow_id, pos = codec.read_hash(buf, pos)
        beneficiary, pos = codec.read_addr(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        refund_after, pos = codec.read_uvarint(buf, pos)
        return EscrowOpenPayload(escrow_id, beneficiary, amount, refund_after), pos
    if tx_type in (TxType.ESCROW_RELEASE, TxType.ESCROW_REFUND):
        escrow_id, pos = codec.read_hash(buf, pos)
        return EscrowRefPayload(escrow_id), pos
    if tx_type == TxType.AGENT_PAYMENT:
        provider, pos = codec.read_addr(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        agent_id, pos = codec.read_hash(buf, pos)
        task_hash, pos = codec.read_hash(buf, pos)
        return AgentPaymentPayload(provider, amount, agent_id, task_hash), pos
    if tx_type == TxType.INFERENCE_PAYMENT:
        provider, pos = codec.read_addr(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        request_hash, pos = codec.read_hash(buf, pos)
        model_id, pos = codec.read_hash(buf, pos)
        return InferencePaymentPayload(provider, amount, request_hash, model_id), pos
    if tx_type == TxType.BATCH_PAYMENT:
        payments, pos = codec.read_recipients(buf, pos)
        return BatchPaymentPayload(payments), pos
    raise codec.CodecError(f"unknown tx_type {tx_type}")


def decode(data: bytes) -> L2Tx:
    """Strict decode of a full signed envelope. Raises CodecError on any
    ambiguity or structural violation; never returns a partially-built tx."""
    codec.guard_tx_size(data)
    buf = memoryview(data)
    pos = 0
    version, pos = codec.read_uvarint(buf, pos)
    l2_chain_id, pos = codec.read_uvarint(buf, pos)
    type_int, pos = codec.read_uvarint(buf, pos)
    try:
        tx_type = TxType(type_int)
    except ValueError:
        raise codec.CodecError(f"unknown tx_type {type_int}")
    sender, pos = codec.read_addr(buf, pos)
    nonce, pos = codec.read_uvarint(buf, pos)
    fee, pos = codec.read_amount(buf, pos)
    expiry, pos = codec.read_uvarint(buf, pos)
    payload, pos = _decode_payload(buf, pos, tx_type)
    sig_scheme_int, pos = codec.read_uvarint(buf, pos)
    try:
        sig_scheme = SigScheme(sig_scheme_int)
    except ValueError:
        raise codec.CodecError(f"unknown sig_scheme {sig_scheme_int}")
    pubkey, pos = codec.read_pubkey(buf, pos)
    signature, pos = codec.read_sig(buf, pos)
    codec.ensure_consumed(buf, pos)
    return L2Tx(
        version=version,
        l2_chain_id=l2_chain_id,
        tx_type=tx_type,
        sender=sender,
        nonce=nonce,
        fee=fee,
        expiry=expiry,
        payload=payload,
        sig_scheme=sig_scheme,
        pubkey=pubkey,
        signature=signature,
    )


def decode_body(data: bytes, tx_type: TxType) -> Tuple[dict, int]:
    """Decode just a body (used by forced-inclusion parsing from L1). Returns the
    field dict and the number of bytes consumed."""
    buf = memoryview(data)
    pos = 0
    version, pos = codec.read_uvarint(buf, pos)
    l2_chain_id, pos = codec.read_uvarint(buf, pos)
    _t, pos = codec.read_uvarint(buf, pos)
    sender, pos = codec.read_addr(buf, pos)
    nonce, pos = codec.read_uvarint(buf, pos)
    fee, pos = codec.read_amount(buf, pos)
    expiry, pos = codec.read_uvarint(buf, pos)
    payload, pos = _decode_payload(buf, pos, tx_type)
    return (
        {
            "version": version,
            "l2_chain_id": l2_chain_id,
            "sender": sender,
            "nonce": nonce,
            "fee": fee,
            "expiry": expiry,
            "payload": payload,
        },
        pos,
    )
