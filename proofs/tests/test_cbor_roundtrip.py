from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, Tuple

import pytest

import proofs.cbor as pcbor
from proofs.tests import schema_path
from proofs.utils import hash as phash
from proofs import types as ptypes


# -------- helpers: locate encode/decode & optional schema validator ----------

def find_encode_decode(mod) -> Tuple[Callable[[Any], bytes], Callable[[bytes], Any]]:
    """
    Be tolerant to naming: try (encode, decode) then (dumps, loads) then (cbor_encode, cbor_decode).
    """
    candidates = [
        ("encode", "decode"),
        ("dumps", "loads"),
        ("cbor_encode", "cbor_decode"),
        ("to_bytes", "from_bytes"),
    ]
    for en, de in candidates:
        if hasattr(mod, en) and hasattr(mod, de):
            enc = getattr(mod, en)
            dec = getattr(mod, de)
            # basic sanity
            assert callable(enc) and callable(dec)
            return enc, dec
    raise AssertionError("proofs.cbor must expose encode/decode (or dumps/loads, cbor_encode/cbor_decode, to_bytes/from_bytes)")

def find_schema_validator(mod) -> Callable[[Any, str], None] | None:
    """
    Try to find a schema validator accepting (obj, schema_filename or id).
    Accept names: validate, assert_schema, check_schema, validate_against.
    """
    for name in ("validate", "assert_schema", "check_schema", "validate_against"):
        fn = getattr(mod, name, None)
        if callable(fn):
            sig = inspect.signature(fn)
            if len(sig.parameters) >= 2:
                return fn  # type: ignore[return-value]
    return None


ENCODE, DECODE = find_encode_decode(pcbor)
VALIDATE = find_schema_validator(pcbor)


# ----------------------------- domain hashing -------------------------------

def domain_hash(domain: str, payload: bytes) -> bytes:
    """
    Normalize across potential helpers in proofs.utils.hash. Prefer explicit domain/tagged helpers,
    otherwise emulate a conservative tagged hash with a clear separator.
    """
    # Preferred names
    for name in ("domain_hash", "tagged_hash", "sha3_256_tagged", "hash_domain"):
        fn = getattr(phash, name, None)
        if callable(fn):
            try:
                out = fn(domain, payload)  # type: ignore[misc]
                assert isinstance(out, (bytes, bytearray))
                return bytes(out)
            except TypeError:
                # Some variants might be (payload, domain)
                out = fn(payload, domain)  # type: ignore[misc]
                return bytes(out)
    # Fallback: SHA3-256 over a simple transcript with domain separation.
    sha3 = getattr(phash, "sha3_256")
    sep = b"\x19animica-domain\x00"
    return sha3(sep + domain.encode("utf-8") + b"\x00" + payload)


# ------------------------------- test cases ---------------------------------

def test_roundtrip_primitives_and_nested() -> None:
    obj = {
        b"a": 1,
        b"b": True,
        b"c": None,
        b"d": [1, 2, 3, {b"x": b"\x00\x01"}],
    }
    enc1 = ENCODE(obj)
    dec = DECODE(enc1)
    enc2 = ENCODE(dec)
    assert dec == obj, "decode(encode(obj)) must equal obj"
    assert enc1 == enc2, "canonical encoding must be stable on re-encode"


def test_canonical_map_key_ordering() -> None:
    # Same logical map, different Python insertion orders → identical canonical CBOR bytes.
    obj1 = {b"b": 1, b"a": 2}
    obj2 = {b"a": 2, b"b": 1}
    e1 = ENCODE(obj1)
    e2 = ENCODE(obj2)
    assert e1 == e2, "canonical CBOR must produce identical bytes regardless of insertion order"


def test_envelope_roundtrip_and_domain_hashing() -> None:
    # Minimal, valid-looking envelope (not verifying semantics, only structure/CBOR/hash stability)
    env = {
        "type_id": int(ptypes.ProofType.HASHSHARE),
        "body": { "header_hash": b"\xaa"*32, "nonce": 42, "d_ratio": 0.5 },
        "nullifier": b"\xbb"*32,
    }
    # Optional schema validation if exposed
    if VALIDATE is not None:
        VALIDATE(env, "proof_envelope.cddl")
        # also ensure schema file exists
        assert schema_path("proof_envelope.cddl").exists()

    # Round-trip stability
    cb = ENCODE(env)
    env2 = DECODE(cb)
    assert env2 == env
    assert ENCODE(env2) == cb

    # Hashing domains must differ if domain strings differ
    h_env = domain_hash("proof:envelope", cb)
    h_body = domain_hash("proof:body", ENCODE(env["body"]))
    assert h_env != h_body, "different domains must yield different hashes even for related payloads"
    # Re-hashing must be stable
    assert h_env == domain_hash("proof:envelope", cb)


def test_hash_stability_over_reencode() -> None:
    # Any re-encoding of the same logical value must produce identical bytes → identical digest.
    payload = {b"k1": b"v1", b"k2": [0, 1, 2, 3]}
    enc_first = ENCODE(payload)
    enc_second = ENCODE(DECODE(enc_first))
    sha = getattr(phash, "sha3_256")
    assert enc_first == enc_second, "re-encode must be byte-identical"
    assert sha(enc_first) == sha(enc_second), "digest must be stable if bytes are identical"


def test_schema_validator_rejects_shape_if_available() -> None:
    if VALIDATE is None:
        pytest.skip("no schema validator exported by proofs.cbor")
    bad_env = {
        # missing type_id, wrong field names
        "typ": 0,
        "body": {},
        "nullifier": b"\x00"*16,  # wrong length too (but schema layer may not check size)
    }
    with pytest.raises(Exception):
        VALIDATE(bad_env, "proof_envelope.cddl")


def test_encode_bytes_type_and_strictness() -> None:
    # Verify that encoding bytes vs str behaves as expected (CBOR text vs byte string).
    # We expect utilities to require bytes for binary fields; ensure we can encode both,
    # but decoding retains the type used by the encoder.
    obj_bytes = {b"k": b"\xff\x00"}
    obj_text  = {"k": "hello"}
    eb = ENCODE(obj_bytes)
    et = ENCODE(obj_text)
    db = DECODE(eb)
    dt = DECODE(et)
    assert isinstance(list(db.keys())[0], (bytes, bytearray)), "binary map key should remain bytes after round-trip"
    assert isinstance(list(dt.keys())[0], str), "text map key should remain str after round-trip"
    # Encodings should differ because major types differ (byte string vs text string)
    assert eb != et, "CBOR must distinguish byte-string maps from text maps"


def test_float_normalization_if_present() -> None:
    # If the encoder normalizes floats (e.g., forbids NaN payload drift or encodes half/float consistently),
    # we at least assert that canonical re-encode is stable.
    obj = {b"r": 1.25}
    e1 = ENCODE(obj)
    e2 = ENCODE(DECODE(e1))
    assert e1 == e2, "floating-point canonical form must be stable on re-encode"
