"""Tests for ANM-H09 (PTL canonical txid) and ANM-M10 (relay txid<->bytes binding).

ANM-H09: PTL derived txid = sha3_256(raw wire bytes) with no canonicalization,
so non-minimal / extra-field re-encodings of one logical tx minted distinct
txids (malleability / dedup bypass).

ANM-M10: the PTL relay accepted peer-supplied (txid, tx_bytes) separately and
never asserted the advertised txid binds to the bytes, so a peer could poison
receipt/dedup bookkeeping with a mismatched id.

These tests prove:
  * Backward-compat: canonical txs and opaque bytes are unchanged by default.
  * Fail-closed: strict mode rejects non-canonical encodings; the relay rejects
    advertised txids that do not bind to the tx bytes (on by default).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import cbor2
import pytest

from core.ptl.canonical_txid import (
    NonCanonicalTxError,
    canonical_ptl_txid,
    strict_txid_enabled,
)
from core.ptl.service import PtlService
from core.ptl.store import PtlStore
from core.utils.hash import sha3_256
from coretx.canonical import decode_tx_envelope, encode_tx_envelope
from coretx.types import TxAuth, TxBody, TxEnvelope, TxId, TxKind


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _canonical_tx_bytes() -> bytes:
    """Build the canonical CBOR wire bytes for a well-formed transaction."""
    body = TxBody(
        version=1,
        chain_id=777,
        nonce=3,
        from_addr=b"\x11" * 32,
        to_addr=b"\x22" * 32,
        value=1000,
        fee=10,
        gas_limit=21000,
        data=b"",
        memo="hi",
        timestamp=1_700_000_000,
        kind=TxKind.TRANSFER,
    )
    auth = TxAuth(
        scheme_id=0x1003,
        pubkey_bytes=b"\xaa" * 32,
        signature_bytes=b"\xbb" * 64,
        prehash_id=2,
    )
    env = TxEnvelope(body=body, auth=auth, txid=TxId(bytes32=b"\x00" * 32))
    return encode_tx_envelope(env)


def _noncanonical_variant(canonical: bytes) -> bytes:
    """Produce a byte-different encoding of the SAME logical tx by padding the
    envelope map with a junk key (decoder ignores extra fields -- ANM-M05)."""
    decoded = cbor2.loads(canonical)
    assert isinstance(decoded, dict)
    decoded["zzz_junk"] = 1  # extra/unknown field
    variant = cbor2.dumps(decoded)
    assert variant != canonical
    return variant


@pytest.fixture()
def service():
    with tempfile.TemporaryDirectory() as tmp:
        store = PtlStore(str(Path(tmp) / "ptl.db"))
        svc = PtlService(store, ttl_seconds=3600, min_peer_acks=2)
        yield svc
        store.close()


# --------------------------------------------------------------------------- #
# Backward-compat (default / legacy behavior preserved)
# --------------------------------------------------------------------------- #


def test_strict_default_off(monkeypatch):
    monkeypatch.delenv("ANIMICA_PTL_STRICT_TXID", raising=False)
    assert strict_txid_enabled() is False
    monkeypatch.setenv("ANIMICA_PTL_STRICT_TXID", "1")
    assert strict_txid_enabled() is True
    # Explicit override always wins.
    assert strict_txid_enabled(False) is False


def test_canonical_tx_txid_matches_legacy_raw_hash():
    """A canonical tx must keep the exact same txid as the legacy raw hash so
    honest wallets are unaffected."""
    canonical = _canonical_tx_bytes()
    store_bytes, txid = canonical_ptl_txid(canonical, strict=False)
    assert store_bytes == canonical
    assert txid == sha3_256(canonical)
    # And identical whether or not strict is enabled (already canonical).
    assert canonical_ptl_txid(canonical, strict=True) == (canonical, txid)


def test_opaque_bytes_preserved_by_default():
    """PTL has always stored arbitrary bytes; default mode keeps that."""
    raw = b"test transaction data"
    store_bytes, txid = canonical_ptl_txid(raw, strict=False)
    assert store_bytes == raw
    assert txid == sha3_256(raw)


@pytest.mark.asyncio
async def test_submit_canonical_and_dedup(service):
    canonical = _canonical_tx_bytes()
    txid1, entry1 = await service.submit(canonical, origin="a")
    assert txid1 == sha3_256(canonical)
    assert entry1.tx_bytes == canonical
    # Re-submitting identical bytes must dedup to the same entry.
    txid2, entry2 = await service.submit(canonical, origin="b")
    assert txid2 == txid1
    assert entry2.txid == entry1.txid
    assert len(service.store.list_pending(100)) == 1


@pytest.mark.asyncio
async def test_submit_opaque_bytes_legacy(service):
    raw = b"lifecycle test transaction"
    txid, entry = await service.submit(raw, origin="t")
    assert txid == sha3_256(raw)
    assert entry.tx_bytes == raw


@pytest.mark.asyncio
async def test_noncanonical_accepted_but_warned_by_default(service, caplog):
    """Default (non-strict) mode must NOT reject non-canonical encodings (live
    behavior), but should log a loud warning."""
    canonical = _canonical_tx_bytes()
    variant = _noncanonical_variant(canonical)
    with caplog.at_level("WARNING"):
        txid, entry = await service.submit(variant, origin="legacy", strict=False)
    assert entry.tx_bytes == variant
    assert txid == sha3_256(variant)
    assert any("ANM-H09" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------- #
# ANM-H09 fail-closed (strict mode)
# --------------------------------------------------------------------------- #


def test_helper_maps_two_encodings_to_one_canonical_txid():
    """The canonical re-encode of any accepted encoding is identical, so both
    byte encodings of the same logical tx resolve to one txid."""
    canonical = _canonical_tx_bytes()
    variant = _noncanonical_variant(canonical)
    # Decoding the malleable variant and re-encoding yields the canonical bytes.
    reencoded = encode_tx_envelope(decode_tx_envelope(variant))
    assert reencoded == canonical
    assert sha3_256(reencoded) == sha3_256(canonical)


def test_strict_rejects_noncanonical_encoding():
    canonical = _canonical_tx_bytes()
    variant = _noncanonical_variant(canonical)
    # Canonical is accepted in strict mode.
    store_bytes, txid = canonical_ptl_txid(canonical, strict=True)
    assert txid == sha3_256(canonical)
    # The malleable re-encoding is rejected (fail-closed).
    with pytest.raises(NonCanonicalTxError):
        canonical_ptl_txid(variant, strict=True)


def test_strict_rejects_undecodable_bytes():
    with pytest.raises(NonCanonicalTxError):
        canonical_ptl_txid(b"not a tx envelope", strict=True)


@pytest.mark.asyncio
async def test_submit_strict_rejects_noncanonical_and_dedups(service):
    """In strict mode the canonical tx is stored once and the malleable variant
    is rejected -- it cannot create a second/duplicate ledger entry."""
    canonical = _canonical_tx_bytes()
    variant = _noncanonical_variant(canonical)

    txid, _ = await service.submit(canonical, strict=True, origin="a")
    assert txid == sha3_256(canonical)

    with pytest.raises(NonCanonicalTxError):
        await service.submit(variant, strict=True, origin="attacker")

    # Only the single canonical entry exists.
    assert len(service.store.list_pending(100)) == 1


# --------------------------------------------------------------------------- #
# ANM-M10 relay txid<->bytes binding
# --------------------------------------------------------------------------- #


def _make_relay(service):
    from p2p.ptl_relay import PtlRelayService

    sent_acks: list = []

    async def _send_announce(conn_id, txids):
        pass

    async def _send_want(conn_id, txids):
        pass

    async def _send_push(conn_id, items):
        pass

    async def _send_ack(conn_id, data):
        sent_acks.append((conn_id, data))

    relay = PtlRelayService(
        service,
        peer_ids=lambda: [],
        peer_eligible=lambda p: True,
        send_announce=_send_announce,
        send_want=_send_want,
        send_push=_send_push,
        send_ack=_send_ack,
    )
    return relay, sent_acks


@pytest.mark.asyncio
async def test_relay_accepts_matching_txid(service):
    """Backward-compat: an honest push (advertised txid == sha3_256(bytes)) is
    accepted and receipted."""
    relay, sent_acks = _make_relay(service)
    canonical = _canonical_tx_bytes()
    good_txid = sha3_256(canonical)

    await relay.on_ptl_push("peerX", [{"txid": good_txid, "tx_bytes": canonical}])

    entry = await service.get(good_txid)
    assert entry is not None
    assert entry.tx_bytes == canonical
    # An ack (not a reject) was sent for the good txid.
    ack_statuses = {d.get("status") for _, d in sent_acks}
    assert "ack" in ack_statuses
    assert any(
        good_txid in d.get("txids", []) and d.get("status") == "ack"
        for _, d in sent_acks
    )


@pytest.mark.asyncio
async def test_relay_rejects_mismatched_txid(service):
    """ANM-M10: a push advertising a txid that does not bind to the bytes must be
    rejected -- no ack, no receipt keyed on the bogus id."""
    relay, sent_acks = _make_relay(service)
    canonical = _canonical_tx_bytes()
    real_txid = sha3_256(canonical)
    bogus_txid = sha3_256(b"a completely different payload")
    assert bogus_txid != real_txid

    await relay.on_ptl_push("peerEvil", [{"txid": bogus_txid, "tx_bytes": canonical}])

    # No receipt/dedup entry may be created under the bogus advertised id.
    assert await service.get(bogus_txid) is None

    # The bogus id must not have been acked; it must be rejected instead.
    for _, data in sent_acks:
        if data.get("status") == "ack":
            assert bogus_txid not in data.get("txids", [])
    assert any(
        data.get("status") == "reject" and bogus_txid in data.get("txids", [])
        for _, data in sent_acks
    )


@pytest.mark.asyncio
async def test_relay_opaque_bytes_binding_backward_compat(service):
    """The legacy replication path pushes opaque bytes with txid == sha3_256(bytes);
    that must still bind and be accepted by default."""
    relay, sent_acks = _make_relay(service)
    raw = b"test transaction for replication"
    txid = sha3_256(raw)

    await relay.on_ptl_push("peerA", [{"txid": txid, "tx_bytes": raw}])

    entry = await service.get(txid)
    assert entry is not None
    assert entry.tx_bytes == raw
    assert any(
        txid in d.get("txids", []) and d.get("status") == "ack" for _, d in sent_acks
    )
