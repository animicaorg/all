from __future__ import annotations

"""Regression vectors for PoW header hashing (SHA256d)."""

import binascii

from mining.hash_search import _make_hasher


def _sha256d(payload: bytes) -> bytes:
    """Compute SHA256^2 using the mining hash factory."""

    first = _make_hasher("sha256", payload).digest()
    return _make_hasher("sha256", first).digest()


def _build_genesis_header() -> bytes:
    """Construct the Bitcoin genesis block header (80 bytes)."""

    version = (1).to_bytes(4, "little")
    prev_hash = bytes.fromhex("00" * 32)[::-1]
    merkle_root = bytes.fromhex(
        "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b"
    )[::-1]
    timestamp = (1231006505).to_bytes(4, "little")
    bits = (0x1D00FFFF).to_bytes(4, "little")
    nonce = (2083236893).to_bytes(4, "little")

    header = version + prev_hash + merkle_root + timestamp + bits + nonce
    assert len(header) == 80
    return header


def test_pow_header_hashing_regression_vector():
    header = _build_genesis_header()
    digest = _sha256d(header)

    # Expected double-SHA256 digest (raw byte order from hashing loop).
    expected = binascii.unhexlify(
        "6fe28c0ab6f1b372c1a6a246ae63f74f931e8365e15a089c68d6190000000000"
    )

    assert digest == expected
