"""
ANM-M05 / ANM-L07 — strict canonical CBOR decode.

M05: the canonical CBOR decoder accepted non-minimal integer/length encodings
     (additional-info 24-27 regardless of value) and ``decode_tx_envelope``
     silently ignored unknown/extra map keys, so decode->re-encode was NOT
     identity — feeding raw-bytes txid malleability (ANM-H09).

L07: coretx/canonical.py fell back to a lenient bare-cbor2 codec when the strict
     codec import failed.

The strict enforcement is gated behind env flags that default OFF (the decoder
sits on the block-import/DB-read path and tx-submission path of a live chain).
These tests prove BOTH:
  * default-off preserves the current lenient behavior (backward-compat), and
  * flag-on fails closed on every non-canonical encoding while still accepting
    every encoding the canonical encoder actually PRODUCES.
"""

import importlib

import pytest

from core.encoding import cbor
from core.encoding.cbor import dumps, loads, DecodeError
from coretx import canonical
from coretx.canonical import (
    decode_tx_envelope,
    encode_tx_envelope,
    compute_txid,
    cbor_dumps as canon_dumps,
    cbor_loads as canon_loads,
)
from coretx.types import TxBody, TxAuth, TxEnvelope, TxId, TxKind


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _make_envelope():
    body = TxBody(
        version=1,
        chain_id=1,
        nonce=7,
        from_addr=b"\x01" * 32,
        to_addr=b"\x02" * 32,
        value=1_000_000,
        fee=21,
        gas_limit=21_000,
        data=b"payload",
        memo="hello",
        timestamp=1_234_567_890,
        kind=TxKind.TRANSFER,
    )
    auth = TxAuth(
        scheme_id=11,
        pubkey_bytes=b"pk" * 100,
        signature_bytes=b"sig" * 900,
        prehash_id=2,
    )
    env = TxEnvelope(body=body, auth=auth, txid=TxId(bytes32=b"\x00" * 32))
    return TxEnvelope(body=body, auth=auth, txid=compute_txid(env))


def _envelope_dict(env, *, extra_body_key=False):
    d = {
        "auth": {
            "prehash_id": env.auth.prehash_id,
            "pubkey_bytes": env.auth.pubkey_bytes,
            "scheme_id": env.auth.scheme_id,
            "signature_bytes": env.auth.signature_bytes,
        },
        "body": {
            "chain_id": env.body.chain_id,
            "data": env.body.data,
            "fee": env.body.fee,
            "from_addr": env.body.from_addr,
            "gas_limit": env.body.gas_limit,
            "kind": int(env.body.kind),
            "memo": env.body.memo,
            "nonce": env.body.nonce,
            "timestamp": env.body.timestamp,
            "to_addr": env.body.to_addr,
            "value": env.body.value,
            "version": env.body.version,
        },
    }
    if extra_body_key:
        d["body"]["zzz_junk"] = b"malicious"
    return d


# --------------------------------------------------------------------------- #
# L07 — no lenient cbor2 fallback
# --------------------------------------------------------------------------- #
def test_L07_no_cbor2_fallback_uses_strict_codec():
    # canonical.py must bind directly to the strict codec, not a cbor2 shim.
    assert canon_dumps is cbor.dumps
    assert canon_loads is cbor.loads
    src = importlib.util.find_spec("coretx.canonical").origin
    with open(src, "r", encoding="utf-8") as fh:
        text = fh.read()
    # The lenient fallback (import cbor2 / except ImportError shim) must be gone.
    assert "import cbor2" not in text
    assert "except ImportError" not in text


# --------------------------------------------------------------------------- #
# encoder is always minimal (the safety precondition for strict-reject)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "n",
    [0, 1, 23, 24, 255, 256, 65_535, 65_536, 2**32 - 1, 2**32, 2**63, 2**64 - 1, 2**64, 2**100],
)
def test_encoder_emits_minimal_roundtrip_identity(n):
    b = dumps(n)
    assert loads(b) == n
    # dumps(loads(x)) == x is the identity property that makes strict-reject safe
    assert dumps(loads(b)) == b


def test_real_envelope_roundtrip_identity_under_strict(monkeypatch):
    # A real canonical envelope must still decode+re-encode to identical bytes,
    # even with strict-minimal turned ON (backward-compat for live data).
    env = _make_envelope()
    enc = encode_tx_envelope(env)
    assert dumps(loads(enc)) == enc
    monkeypatch.setattr(cbor, "STRICT_MINIMAL", True)
    assert dumps(loads(enc)) == enc  # still round-trips identically
    assert loads(enc) == loads(enc)


# --------------------------------------------------------------------------- #
# M05a — non-minimal integer/length encodings (core.encoding.cbor)
# --------------------------------------------------------------------------- #
# Non-minimal encodings of the value 5 / a 1-byte string, per additional-info.
_NONMINIMAL_INT = bytes([0x18, 0x05])            # uint 5 via ai=24 (should be 0x05)
_NONMINIMAL_INT_25 = bytes([0x19, 0x00, 0x05])   # uint 5 via ai=25
_NONMINIMAL_INT_26 = bytes([0x1A, 0, 0, 0, 0x05])  # uint 5 via ai=26
_NONMINIMAL_BSTR = bytes([0x58, 0x01, 0xAA])     # bstr len 1 via ai=24 (should be 0x41)


def test_M05a_default_off_accepts_nonminimal():
    # Backward-compat: with the flag OFF (default) the decoder is lenient exactly
    # as the live network currently behaves.
    assert cbor.STRICT_MINIMAL is False
    assert loads(_NONMINIMAL_INT) == 5
    assert loads(_NONMINIMAL_INT_25) == 5
    assert loads(_NONMINIMAL_INT_26) == 5
    assert loads(_NONMINIMAL_BSTR) == b"\xAA"


@pytest.mark.parametrize(
    "blob",
    [_NONMINIMAL_INT, _NONMINIMAL_INT_25, _NONMINIMAL_INT_26, _NONMINIMAL_BSTR],
)
def test_M05a_strict_rejects_nonminimal(monkeypatch, blob):
    monkeypatch.setattr(cbor, "STRICT_MINIMAL", True)
    with pytest.raises(DecodeError, match="non-minimal"):
        loads(blob)


def test_M05a_strict_still_accepts_minimal(monkeypatch):
    monkeypatch.setattr(cbor, "STRICT_MINIMAL", True)
    # Minimal encodings across the ai boundaries must all still decode.
    for n in [0, 23, 24, 255, 256, 65_535, 65_536, 2**32 - 1, 2**32]:
        assert loads(dumps(n)) == n
    assert loads(dumps(b"\xAA")) == b"\xAA"
    assert loads(dumps("hello world")) == "hello world"
    assert loads(dumps([1, 2, 3])) == [1, 2, 3]


# --------------------------------------------------------------------------- #
# M05b — decode_tx_envelope ignores extra keys / accepts non-canonical
# --------------------------------------------------------------------------- #
def test_M05b_default_off_ignores_extra_keys_but_txid_diverges():
    # Documents the current (lenient) behavior that the fix hardens: an extra
    # junk key changes the wire bytes but not the canonical txid.
    assert canonical.STRICT_CANONICAL is False
    env = _make_envelope()
    clean_bytes = dumps(_envelope_dict(env))
    junk_bytes = dumps(_envelope_dict(env, extra_body_key=True))
    assert clean_bytes != junk_bytes  # different wire encodings
    dec_clean = decode_tx_envelope(clean_bytes)
    dec_junk = decode_tx_envelope(junk_bytes)
    # same canonical txid — this is the raw-hash malleability window (H09)
    assert dec_clean.txid.bytes32 == dec_junk.txid.bytes32


def test_M05b_strict_rejects_extra_keys(monkeypatch):
    monkeypatch.setattr(canonical, "STRICT_CANONICAL", True)
    env = _make_envelope()
    junk_bytes = dumps(_envelope_dict(env, extra_body_key=True))
    with pytest.raises(ValueError, match="non-canonical"):
        decode_tx_envelope(junk_bytes)


def test_M05b_strict_rejects_nonminimal_envelope(monkeypatch):
    # Craft an envelope whose canonical bytes are then hand-mutated to carry a
    # non-minimal encoding of a small int field, and confirm strict decode
    # rejects it via the raw==re-encode identity check (independent of the
    # global STRICT_MINIMAL flag, which stays OFF here).
    monkeypatch.setattr(canonical, "STRICT_CANONICAL", True)
    assert cbor.STRICT_MINIMAL is False  # identity check must catch it regardless
    env = _make_envelope()
    canon_bytes = encode_tx_envelope(env)
    # nonce is 7 -> encoded as the single byte 0x07 somewhere in the stream.
    # Replace the first minimal 0x07 with the non-minimal 0x18 0x07.
    idx = canon_bytes.find(b"\x07")
    assert idx != -1
    mutated = canon_bytes[:idx] + bytes([0x18, 0x07]) + canon_bytes[idx + 1:]
    assert mutated != canon_bytes
    with pytest.raises(ValueError, match="non-canonical"):
        decode_tx_envelope(mutated)


def test_M05b_strict_accepts_canonical_envelope(monkeypatch):
    # Backward-compat: a genuinely canonical envelope must still decode fine and
    # round-trip when strict is ON.
    monkeypatch.setattr(canonical, "STRICT_CANONICAL", True)
    env = _make_envelope()
    enc = encode_tx_envelope(env)
    dec = decode_tx_envelope(enc)
    assert dec.txid.bytes32 == env.txid.bytes32
    assert encode_tx_envelope(dec) == enc


def test_M05b_strict_gives_single_txid_for_equivalent_encodings(monkeypatch):
    # The recommended-test from the report: two byte-different encodings of the
    # same logical tx must NOT both be accepted under strict mode; only the
    # canonical one survives, so it maps to exactly one txid.
    monkeypatch.setattr(canonical, "STRICT_CANONICAL", True)
    env = _make_envelope()
    canonical_bytes = encode_tx_envelope(env)
    junk_bytes = dumps(_envelope_dict(env, extra_body_key=True))
    accepted = []
    for blob in (canonical_bytes, junk_bytes):
        try:
            accepted.append(decode_tx_envelope(blob).txid.bytes32)
        except ValueError:
            pass
    assert accepted == [env.txid.bytes32]
