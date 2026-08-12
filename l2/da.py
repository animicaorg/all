"""Data availability: compact, self-describing, reconstructable batch blobs.

A state root alone lets nobody rebuild the ledger. For every batch we publish a
DA blob from which any independent node can deterministically replay the exact
transactions and therefore re-derive ``new_state_root`` — the core of trust
minimization and of the forced-exit escape hatch.

Encoding pipeline (each stage benchmarked; see l2/benchmarks):

1. **Address dictionary.** Collect every distinct 32-byte address in the batch,
   emit the dictionary once, then reference addresses by a small varint index
   inside each tx. Payments in a real economy reuse merchants/providers heavily,
   so this alone is a large win.
2. **Dictionary-aware tx encoding.** Re-encode each tx with addresses replaced by
   their dictionary index; everything else uses the same minimal varints as the
   wire codec.
3. **zlib compression** over the concatenation. (zstandard would compress a bit
   better/faster; zlib is always in the stdlib so the protocol has zero external
   DA dependency. The interface is stable if we later swap the codec.)

``data_root = sha3_256(DA_MAGIC || uncompressed_body)`` binds the blob to the
batch header. Reconstruction verifies the root before trusting a byte of it.
"""

from __future__ import annotations

import hashlib
import zlib
from typing import Dict, List, Tuple

from . import codec
from . import tx as l2tx
from .constants import MAX_BATCH_BYTES, TxType

DA_MAGIC = b"ANML2DA1"


# ── address dictionary ───────────────────────────────────────────────────────


def _collect_addresses(txs: List[l2tx.L2Tx]) -> List[bytes]:
    seen: Dict[bytes, None] = {}
    for t in txs:
        for a in _tx_addresses(t):
            seen.setdefault(a, None)
    return list(seen.keys())


def _tx_addresses(t: l2tx.L2Tx) -> List[bytes]:
    out = [t.sender]
    p = t.payload
    tt = t.tx_type
    if tt in (TxType.TRANSFER, TxType.PAY):
        out.append(p.recipient)
    elif tt in (TxType.WITHDRAW, TxType.FORCED_WITHDRAW):
        out.append(p.l1_recipient)
    elif tt == TxType.DEPOSIT_CLAIM:
        out.append(p.beneficiary)
    elif tt == TxType.ESCROW_OPEN:
        out.append(p.beneficiary)
    elif tt in (TxType.AGENT_PAYMENT, TxType.INFERENCE_PAYMENT):
        out.append(p.provider)
    elif tt == TxType.BATCH_PAYMENT:
        for r, _ in p.payments:
            out.append(r)
    return out


# ── dictionary-aware tx body (addresses -> index) ────────────────────────────


def _encode_tx_dict(t: l2tx.L2Tx, idx_of: Dict[bytes, int]) -> bytes:
    """Encode a tx for DA: identical to the wire body but each 32-byte address
    is replaced by its dictionary index (varint). Signature + pubkey are included
    (needed to re-verify on reconstruction) but the pubkey is deduped separately
    below is future work; for 10.0.0 we keep them inline for provability."""
    out = bytearray()
    codec.write_uvarint(out, t.version)
    codec.write_uvarint(out, t.l2_chain_id)
    codec.write_uvarint(out, int(t.tx_type))
    codec.write_uvarint(out, idx_of[t.sender])
    codec.write_uvarint(out, t.nonce)
    codec.write_amount(out, t.fee)
    codec.write_uvarint(out, t.expiry)
    _encode_payload_dict(out, t, idx_of)
    codec.write_uvarint(out, int(t.sig_scheme))
    codec.write_pubkey(out, t.pubkey)
    codec.write_sig(out, t.signature)
    return bytes(out)


def _encode_payload_dict(out: bytearray, t: l2tx.L2Tx, idx_of: Dict[bytes, int]) -> None:
    p = t.payload
    tt = t.tx_type
    if tt in (TxType.TRANSFER, TxType.PAY):
        codec.write_uvarint(out, idx_of[p.recipient])
        codec.write_amount(out, p.amount)
        codec.write_memo(out, p.memo)
    elif tt in (TxType.WITHDRAW, TxType.FORCED_WITHDRAW):
        codec.write_uvarint(out, idx_of[p.l1_recipient])
        codec.write_amount(out, p.amount)
    elif tt == TxType.DEPOSIT_CLAIM:
        codec.write_uvarint(out, idx_of[p.beneficiary])
        codec.write_amount(out, p.amount)
        codec.write_hash(out, p.deposit_id)
    elif tt == TxType.ESCROW_OPEN:
        codec.write_hash(out, p.escrow_id)
        codec.write_uvarint(out, idx_of[p.beneficiary])
        codec.write_amount(out, p.amount)
        codec.write_uvarint(out, p.refund_after)
    elif tt in (TxType.ESCROW_RELEASE, TxType.ESCROW_REFUND):
        codec.write_hash(out, p.escrow_id)
    elif tt == TxType.AGENT_PAYMENT:
        codec.write_uvarint(out, idx_of[p.provider])
        codec.write_amount(out, p.amount)
        codec.write_hash(out, p.agent_id)
        codec.write_hash(out, p.task_hash)
    elif tt == TxType.INFERENCE_PAYMENT:
        codec.write_uvarint(out, idx_of[p.provider])
        codec.write_amount(out, p.amount)
        codec.write_hash(out, p.request_hash)
        codec.write_hash(out, p.model_id)
    elif tt == TxType.BATCH_PAYMENT:
        codec.write_uvarint(out, len(p.payments))
        for r, amt in p.payments:
            codec.write_uvarint(out, idx_of[r])
            codec.write_amount(out, amt)
    else:
        raise codec.CodecError(f"unknown tx_type {tt}")


def _decode_tx_dict(buf: memoryview, pos: int, dictionary: List[bytes]) -> Tuple[l2tx.L2Tx, int]:
    def addr(i: int) -> bytes:
        if i >= len(dictionary):
            raise codec.CodecError("dictionary index out of range")
        return dictionary[i]

    version, pos = codec.read_uvarint(buf, pos)
    l2_chain_id, pos = codec.read_uvarint(buf, pos)
    type_int, pos = codec.read_uvarint(buf, pos)
    tt = TxType(type_int)
    sidx, pos = codec.read_uvarint(buf, pos)
    sender = addr(sidx)
    nonce, pos = codec.read_uvarint(buf, pos)
    fee, pos = codec.read_amount(buf, pos)
    expiry, pos = codec.read_uvarint(buf, pos)

    if tt in (TxType.TRANSFER, TxType.PAY):
        ri, pos = codec.read_uvarint(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        memo, pos = codec.read_memo(buf, pos)
        payload = l2tx.TransferPayload(addr(ri), amount, memo)
    elif tt in (TxType.WITHDRAW, TxType.FORCED_WITHDRAW):
        ri, pos = codec.read_uvarint(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        payload = l2tx.WithdrawPayload(addr(ri), amount)
    elif tt == TxType.DEPOSIT_CLAIM:
        bi, pos = codec.read_uvarint(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        did, pos = codec.read_hash(buf, pos)
        payload = l2tx.DepositClaimPayload(addr(bi), amount, did)
    elif tt == TxType.ESCROW_OPEN:
        eid, pos = codec.read_hash(buf, pos)
        bi, pos = codec.read_uvarint(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        refund_after, pos = codec.read_uvarint(buf, pos)
        payload = l2tx.EscrowOpenPayload(eid, addr(bi), amount, refund_after)
    elif tt in (TxType.ESCROW_RELEASE, TxType.ESCROW_REFUND):
        eid, pos = codec.read_hash(buf, pos)
        payload = l2tx.EscrowRefPayload(eid)
    elif tt == TxType.AGENT_PAYMENT:
        pi, pos = codec.read_uvarint(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        agent_id, pos = codec.read_hash(buf, pos)
        task_hash, pos = codec.read_hash(buf, pos)
        payload = l2tx.AgentPaymentPayload(addr(pi), amount, agent_id, task_hash)
    elif tt == TxType.INFERENCE_PAYMENT:
        pi, pos = codec.read_uvarint(buf, pos)
        amount, pos = codec.read_amount(buf, pos)
        request_hash, pos = codec.read_hash(buf, pos)
        model_id, pos = codec.read_hash(buf, pos)
        payload = l2tx.InferencePaymentPayload(addr(pi), amount, request_hash, model_id)
    elif tt == TxType.BATCH_PAYMENT:
        count, pos = codec.read_uvarint(buf, pos)
        pays = []
        for _ in range(count):
            ri, pos = codec.read_uvarint(buf, pos)
            amt, pos = codec.read_amount(buf, pos)
            pays.append((addr(ri), amt))
        payload = l2tx.BatchPaymentPayload(pays)
    else:
        raise codec.CodecError(f"unknown tx_type {tt}")

    scheme_int, pos = codec.read_uvarint(buf, pos)
    pubkey, pos = codec.read_pubkey(buf, pos)
    sig, pos = codec.read_sig(buf, pos)
    from .constants import SigScheme

    t = l2tx.L2Tx(
        version=version,
        l2_chain_id=l2_chain_id,
        tx_type=tt,
        sender=sender,
        nonce=nonce,
        fee=fee,
        expiry=expiry,
        payload=payload,
        sig_scheme=SigScheme(scheme_int),
        pubkey=pubkey,
        signature=sig,
    )
    return t, pos


# ── public: encode / decode / verify ─────────────────────────────────────────


def _uncompressed_body(txs: List[l2tx.L2Tx]) -> bytes:
    dictionary = _collect_addresses(txs)
    idx_of = {a: i for i, a in enumerate(dictionary)}
    body = bytearray()
    body += DA_MAGIC
    codec.write_uvarint(body, len(dictionary))
    for a in dictionary:
        codec.write_addr(body, a)
    codec.write_uvarint(body, len(txs))
    for t in txs:
        enc = _encode_tx_dict(t, idx_of)
        codec.write_bytes(body, enc)  # length-prefixed so decode is unambiguous
    return bytes(body)


def data_root_of(txs: List[l2tx.L2Tx]) -> bytes:
    """Commitment folded into the batch header. Computed over the *uncompressed*
    body so it is independent of the compressor."""
    return hashlib.sha3_256(_uncompressed_body(txs)).digest()


def encode_batch(txs: List[l2tx.L2Tx], level: int = 6) -> Tuple[bytes, bytes]:
    """Return (compressed_blob, data_root)."""
    body = _uncompressed_body(txs)
    blob = zlib.compress(body, level)
    if len(blob) > MAX_BATCH_BYTES:
        raise codec.CodecError("DA blob exceeds max batch bytes")
    return blob, hashlib.sha3_256(body).digest()


def decode_batch(blob: bytes) -> List[l2tx.L2Tx]:
    """Reconstruct the exact tx list from a DA blob. Strict — any structural
    problem raises CodecError so a bad blob can never yield a partial replay."""
    body = zlib.decompress(blob)
    buf = memoryview(body)
    pos = 0
    magic = bytes(buf[:len(DA_MAGIC)])
    if magic != DA_MAGIC:
        raise codec.CodecError("bad DA magic")
    pos = len(DA_MAGIC)
    dict_len, pos = codec.read_uvarint(buf, pos)
    dictionary: List[bytes] = []
    for _ in range(dict_len):
        a, pos = codec.read_addr(buf, pos)
        dictionary.append(a)
    n, pos = codec.read_uvarint(buf, pos)
    txs: List[l2tx.L2Tx] = []
    for _ in range(n):
        enc, pos = codec.read_bytes(buf, pos, len(body))
        t, inner = _decode_tx_dict(memoryview(enc), 0, dictionary)
        codec.ensure_consumed(memoryview(enc), inner)
        txs.append(t)
    codec.ensure_consumed(buf, pos)
    return txs


def verify_blob(blob: bytes, expected_root: bytes) -> bool:
    """Availability check: does this blob decompress, reconstruct, and hash to
    the header's data_root? Anyone can run this against published DA."""
    try:
        body = zlib.decompress(blob)
    except Exception:
        return False
    if not body.startswith(DA_MAGIC):
        return False
    if hashlib.sha3_256(body).digest() != expected_root:
        return False
    try:
        decode_batch(blob)
    except Exception:
        return False
    return True


def compression_ratio(txs: List[l2tx.L2Tx]) -> float:
    """raw-wire-bytes / compressed-DA-bytes — reported for observability."""
    raw = sum(len(t.encode()) for t in txs)
    if raw == 0:
        return 1.0
    blob, _ = encode_batch(txs)
    return raw / max(1, len(blob))
