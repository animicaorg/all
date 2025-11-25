# -*- coding: utf-8 -*-
"""
Local unit tests for the Multisig example (N-of-M, PQ-aware permits).

These tests are *VM-agnostic* and validate:
- Manifest/ABI presence & shape
- Deterministic "SignBytes" construction for off-chain permits
- Threshold/approval aggregation semantics (dedupe, insufficient approvals)
- Action-hash determinism (stable across repeated builds; changes when inputs change)

They do not require vm_py to be installed, so they can run fast and offline.
If vm_py is present later, we can extend with end-to-end simulation tests here.

Run:
    pytest -q contracts/examples/multisig/tests_local.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pytest
from hashlib import sha3_256


# ---------------------------------------------------------------------------
# Paths / fixtures
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"


# ---------------------------------------------------------------------------
# Helpers: deterministic, self-contained "SignBytes" builder
# ---------------------------------------------------------------------------

def _uvarint(n: int) -> bytes:
    """Unsigned varint (LE 7-bit) like protobuf/cborish style; simple and deterministic."""
    if n < 0:
        raise ValueError("uvarint cannot encode negative")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(0x80 | b)
        else:
            out.append(b)
            break
    return bytes(out)


def _enc_u64(n: int) -> bytes:
    return n.to_bytes(8, "little", signed=False)


def _enc_u128(n: int) -> bytes:
    return n.to_bytes(16, "little", signed=False)


def _enc_u16(n: int) -> bytes:
    return n.to_bytes(2, "little", signed=False)


def _enc_bytes(b: bytes) -> bytes:
    return _uvarint(len(b)) + b


def _enc_address(addr: bytes) -> bytes:
    """
    Animica addresses are 20 bytes at the core type level (bech32m is a view).
    For the permit domain we encode as raw 20-byte payload (no checksum).
    """
    if not isinstance(addr, (bytes, bytearray)) or len(addr) != 20:
        raise ValueError("address must be 20 bytes")
    return bytes(addr)


def build_permit_signbytes(
    *,
    domain: bytes,
    chain_id: int,
    contract_addr: bytes,
    to: bytes,
    value: int,
    data: bytes,
    gas_limit: int,
    nonce: int,
    expiry_height: int,
) -> bytes:
    """
    Deterministic encoding for the permit SignBytes (host-side).
    Layout (all little-endian integers; addresses = raw 20 bytes):
        domain:         32 bytes        (pre-hashed, see PERMIT_DOMAIN below)
        chain_id:       u64
        contract_addr:  20 bytes
        to:             20 bytes
        value:          u128
        gas_limit:      u64
        expiry_height:  u64
        nonce:          u128
        data:           varbytes
    Final digest = sha3_256(SignBytes)
    """
    if len(domain) != 32:
        raise ValueError("domain must be 32 bytes")
    sb = bytearray()
    sb += domain
    sb += _enc_u64(chain_id)
    sb += _enc_address(contract_addr)
    sb += _enc_address(to)
    sb += _enc_u128(value)
    sb += _enc_u64(gas_limit)
    sb += _enc_u64(expiry_height)
    sb += _enc_u128(nonce)
    sb += _enc_bytes(data)
    return bytes(sb)


def digest_signbytes(sb: bytes) -> bytes:
    return sha3_256(sb).digest()


# A canonical domain seed string for permits. In-chain code will typically hold the
# keccak/sha3 of a tagged domain string. We use sha3_256 of the ASCII tag.
PERMIT_DOMAIN = sha3_256(b"ANIMICA::MULTISIG::PERMIT::V1").digest()  # 32 bytes


# ---------------------------------------------------------------------------
# Approval aggregation helpers (host-side mirror of expected contract logic)
# ---------------------------------------------------------------------------

def aggregate_approvals(
    threshold: int,
    owners: Iterable[bytes],
    permits: Iterable[Dict[str, bytes]],
) -> Tuple[int, List[bytes]]:
    """
    Return (#unique_valid, unique_signers) counting unique owner addresses present in permits.

    We *do not* verify signatures here; this is just a structural property test that
    dedupes by signer address and checks against the owner-set. Caller can pass
    signer_addr and a dummy signature to model cardinality behavior.
    """
    owners_set = {o for o in owners}
    uniq: List[bytes] = []
    seen = set()
    for p in permits:
        signer = p.get("signer_addr", b"")
        if not isinstance(signer, (bytes, bytearray)) or len(signer) != 20:
            continue
        if signer in seen:
            continue
        if signer in owners_set:
            seen.add(signer)
            uniq.append(bytes(signer))
    return len(uniq), uniq


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_manifest_exists_and_has_required_abi():
    assert MANIFEST.is_file(), f"manifest not found at {MANIFEST}"
    manifest = json.loads(MANIFEST.read_text())

    # Basic keys
    for k in ("name", "version", "abi", "metadata"):
        assert k in manifest, f"missing '{k}' key in manifest"

    abi = manifest["abi"]
    assert isinstance(abi, list) and abi, "ABI must be a non-empty list"

    # Verify presence of a few canonical entries.
    names = {entry.get("name") for entry in abi if isinstance(entry, dict)}
    for required in ("get_config", "get_nonce", "propose", "execute_with_permits"):
        assert required in names, f"ABI missing function '{required}'"

    # Event sanity
    event_names = {e.get("name") for e in abi if e.get("type") == "event"}
    for ev in ("Proposed", "Approved", "Executed", "OwnersChanged"):
        assert ev in event_names, f"ABI missing event '{ev}'"


def test_signbytes_determinism_and_sensitivity():
    # Setup fake addresses (20 bytes) for contract & target
    contract_addr = bytes.fromhex("11" * 20)
    to_addr = bytes.fromhex("22" * 20)
    chain_id = 1337
    value = 123_456_789
    gas_limit = 250000
    nonce = 1
    expiry_height = 42
    data = b"\x01\x02hello"

    sb1 = build_permit_signbytes(
        domain=PERMIT_DOMAIN,
        chain_id=chain_id,
        contract_addr=contract_addr,
        to=to_addr,
        value=value,
        data=data,
        gas_limit=gas_limit,
        nonce=nonce,
        expiry_height=expiry_height,
    )
    sb2 = build_permit_signbytes(
        domain=PERMIT_DOMAIN,
        chain_id=chain_id,
        contract_addr=contract_addr,
        to=to_addr,
        value=value,
        data=data,
        gas_limit=gas_limit,
        nonce=nonce,
        expiry_height=expiry_height,
    )
    assert sb1 == sb2, "SignBytes must be identical for identical inputs"
    d1 = digest_signbytes(sb1)
    d2 = digest_signbytes(sb2)
    assert d1 == d2 and len(d1) == 32, "sha3_256 digest must be stable (32 bytes)"

    # Flip one field at a time → digest must change
    sb_nonce = build_permit_signbytes(
        domain=PERMIT_DOMAIN,
        chain_id=chain_id,
        contract_addr=contract_addr,
        to=to_addr,
        value=value,
        data=data,
        gas_limit=gas_limit,
        nonce=nonce + 1,
        expiry_height=expiry_height,
    )
    assert digest_signbytes(sb_nonce) != d1, "nonce must affect digest"

    sb_value = build_permit_signbytes(
        domain=PERMIT_DOMAIN,
        chain_id=chain_id,
        contract_addr=contract_addr,
        to=to_addr,
        value=value + 1,
        data=data,
        gas_limit=gas_limit,
        nonce=nonce,
        expiry_height=expiry_height,
    )
    assert digest_signbytes(sb_value) != d1, "value must affect digest"

    sb_data = build_permit_signbytes(
        domain=PERMIT_DOMAIN,
        chain_id=chain_id,
        contract_addr=contract_addr,
        to=to_addr,
        value=value,
        data=b"\xFF" + data,
        gas_limit=gas_limit,
        nonce=nonce,
        expiry_height=expiry_height,
    )
    assert digest_signbytes(sb_data) != d1, "data bytes must affect digest"


def test_threshold_and_approval_aggregation_dedupe():
    # Construct 3 owners (20-byte ids)
    owner1 = bytes.fromhex("aa" * 20)
    owner2 = bytes.fromhex("bb" * 20)
    owner3 = bytes.fromhex("cc" * 20)
    owners = [owner1, owner2, owner3]
    threshold = 2

    # Case 1: two distinct approvals → meets threshold
    permits = [
        {"signer_addr": owner1, "sig": b"fake1", "alg_id": (1).to_bytes(2, "little")},
        {"signer_addr": owner2, "sig": b"fake2", "alg_id": (1).to_bytes(2, "little")},
    ]
    count, uniq = aggregate_approvals(threshold, owners, permits)
    assert count == 2 and set(uniq) == {owner1, owner2}

    # Case 2: duplicate signer included twice → still counts once
    permits_dup = permits + [{"signer_addr": owner1, "sig": b"fakeX"}]
    count2, uniq2 = aggregate_approvals(threshold, owners, permits_dup)
    assert count2 == 2 and set(uniq2) == {owner1, owner2}

    # Case 3: include a non-owner → ignored
    not_owner = bytes.fromhex("dd" * 20)
    permits_bad = permits + [{"signer_addr": not_owner, "sig": b"nope"}]
    count3, uniq3 = aggregate_approvals(threshold, owners, permits_bad)
    assert count3 == 2 and set(uniq3) == {owner1, owner2}

    # Case 4: insufficient approvals
    only_one = [{"signer_addr": owner1, "sig": b"one"}]
    count4, _uniq4 = aggregate_approvals(threshold, owners, only_one)
    assert count4 == 1, "should not meet a 2-of-3 threshold with only one approval"


def test_action_hash_drift_resistance():
    """
    In a typical multisig, an action-hash (or tx-hash) pins the tuple
    (to, value, data, gas, nonce, expiry). We model it as sha3_256 of the SignBytes.
    This test validates that:
      - hash(to, value, data, ...) is stable for repeated builds
      - any field change flips the hash
    """
    contract_addr = bytes.fromhex("01" * 20)
    to_addr = bytes.fromhex("02" * 20)
    value = 42
    gas = 120000
    chain_id = 1337
    nonce = 5
    expiry = 9_999
    data = b"\x00payload"

    sb = build_permit_signbytes(
        domain=PERMIT_DOMAIN,
        chain_id=chain_id,
        contract_addr=contract_addr,
        to=to_addr,
        value=value,
        data=data,
        gas_limit=gas,
        nonce=nonce,
        expiry_height=expiry,
    )
    h = digest_signbytes(sb)

    # Stable on rebuild
    sb_again = build_permit_signbytes(
        domain=PERMIT_DOMAIN,
        chain_id=chain_id,
        contract_addr=contract_addr,
        to=to_addr,
        value=value,
        data=data,
        gas_limit=gas,
        nonce=nonce,
        expiry_height=expiry,
    )
    assert digest_signbytes(sb_again) == h

    # Flip a field
    sb_flip = build_permit_signbytes(
        domain=PERMIT_DOMAIN,
        chain_id=chain_id,
        contract_addr=contract_addr,
        to=to_addr,
        value=value,
        data=data + b"\x01",
        gas_limit=gas,
        nonce=nonce,
        expiry_height=expiry,
    )
    assert digest_signbytes(sb_flip) != h


@pytest.mark.parametrize("owners_count,threshold,valid", [
    (1, 1, True),
    (3, 2, True),
    (3, 3, True),
    (3, 4, False),
    (0, 0, False),
    (2, 0, False),
])
def test_threshold_bounds(owners_count: int, threshold: int, valid: bool):
    """Pure sanity: threshold must be in [1, len(owners)] under normal policy."""
    owners = [os.urandom(20) for _ in range(owners_count)]
    ok = (1 <= threshold <= len(owners))
    assert ok == valid


def test_manifest_metadata_tags_and_language():
    manifest = json.loads(MANIFEST.read_text())
    md = manifest.get("metadata", {})
    assert md.get("language") == "py"
    assert md.get("runtime") in ("vm_py", "python-vm", "pyvm")
    tags = set(md.get("tags", []))
    # Minimal expectations
    for t in ("multisig", "pq", "security"):
        assert t in tags, f"missing expected tag '{t}'"


