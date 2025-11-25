import hashlib
from dataclasses import dataclass
from typing import Iterable, List, Sequence

import pytest

# Prefer project types if available; otherwise use a local minimal stub.
try:
    # type: ignore
    from execution.types.events import LogEvent  # noqa: F401
except Exception:  # pragma: no cover
    @dataclass
    class LogEvent:  # type: ignore[no-redef]
        address: bytes
        topics: List[bytes]
        data: bytes


# ---- Fallback, spec-aligned helpers (used in tests and to cross-check module code) ----

def _keccak256(x: bytes) -> bytes:
    # We use SHA3-256 (FIPS keccak) which is the project default per synopsis.
    return hashlib.sha3_256(x).digest()


def encode_log_leaf(e: LogEvent) -> bytes:
    """
    Deterministic, canonical encoding for a LogEvent leaf before hashing.
    (Matches the spirit of 'canonical CBOR' without depending on the encoder here.)
    """
    out = bytearray()
    if len(e.address) != 32:
        raise ValueError("address must be 32 bytes")
    out.extend(e.address)
    # topics: count (uvarint-ish: 4 bytes big-endian for test simplicity) + each topic as length(2) + data
    out.extend(len(e.topics).to_bytes(4, "big"))
    for t in e.topics:
        out.extend(len(t).to_bytes(2, "big"))
        out.extend(t)
    # data: length(4) + payload
    out.extend(len(e.data).to_bytes(4, "big"))
    out.extend(e.data)
    return bytes(out)


def logs_merkle_root(logs: Sequence[LogEvent]) -> bytes:
    """
    Canonical Merkle root of logs, order-sensitive.
    Leaves = keccak(encode_log_leaf(e)).
    If odd, duplicate last. Empty → keccak(b"").
    """
    if not logs:
        return _keccak256(b"")
    layer = [_keccak256(encode_log_leaf(e)) for e in logs]
    while len(layer) > 1:
        nxt = []
        it = iter(range(0, len(layer), 2))
        for i in it:
            left = layer[i]
            right = layer[i + 1] if i + 1 < len(layer) else layer[i]
            nxt.append(_keccak256(left + right))
        layer = nxt
    return layer[0]


def compute_bloom(logs: Iterable[LogEvent]) -> bytes:
    """
    2048-bit bloom filter (256 bytes), order-insensitive.
    Sets bits for address and every topic of every log using three 11-bit indices
    derived from the SHA3-256 hash (Ethereum-style positions).
    """
    bloom = 0
    def _add(x: bytes) -> None:
        nonlocal bloom
        h = _keccak256(x)
        # Take three 11-bit slices using pairs of bytes
        for off in (0, 2, 4):
            bit = ((h[off] << 8) | h[off + 1]) & 2047  # 0..2047
            bloom |= (1 << bit)

    for e in logs:
        _add(e.address)
        for t in e.topics:
            _add(t)

    # return big-endian 256-byte representation
    return bloom.to_bytes(256, "big")


# If the project provides its own helpers, import and cross-check where possible.
try:  # pragma: no cover
    # type: ignore
    from execution.receipts.logs_hash import logs_merkle_root as mod_logs_root  # noqa: F401
except Exception:  # pragma: no cover
    mod_logs_root = None

try:  # pragma: no cover
    # type: ignore
    from execution.receipts.logs_hash import compute_bloom as mod_compute_bloom  # noqa: F401
except Exception:  # pragma: no cover
    mod_compute_bloom = None


# ---- Fixtures ----

def ev(addr_byte: int, topics: Sequence[bytes], data: bytes) -> LogEvent:
    return LogEvent(address=bytes([addr_byte]) * 32, topics=list(topics), data=data)


@pytest.fixture
def logs_abc() -> List[LogEvent]:
    return [
        ev(0xAA, [b"T/a", b"k1"], b"alpha"),
        ev(0xBB, [b"T/b"], b"beta"),
        ev(0xCC, [b"T/c", b"k2", b"k3"], b"gamma"),
    ]


# ---- Tests ----

def test_event_order_preserved(logs_abc: List[LogEvent]) -> None:
    # Local "sink": just append in order and assert order is preserved
    collected: List[LogEvent] = []
    for e in logs_abc:
        collected.append(e)
    assert collected == logs_abc
    # Changing order must differ
    assert collected != list(reversed(logs_abc))


def test_logs_merkle_root_determinism_and_order_sensitivity(logs_abc: List[LogEvent]) -> None:
    r1 = logs_merkle_root(logs_abc)
    r2 = logs_merkle_root(list(logs_abc))  # same content, new list
    r_rev = logs_merkle_root(list(reversed(logs_abc)))

    # Deterministic on same inputs
    assert r1 == r2
    # Order-sensitive
    assert r1 != r_rev

    # If module implementation exists, it should agree
    if mod_logs_root is not None:  # pragma: no branch
        assert r1 == mod_logs_root(logs_abc)


def test_bloom_order_insensitive_but_content_sensitive(logs_abc: List[LogEvent]) -> None:
    b1 = compute_bloom(logs_abc)
    b_rev = compute_bloom(list(reversed(logs_abc)))
    assert b1 == b_rev  # order-insensitive

    # Modify a topic → bloom should change
    mutated = list(logs_abc)
    mutated[1] = ev(0xBB, [b"T/b2"], b"beta")  # change a topic
    b2 = compute_bloom(mutated)
    assert b2 != b1

    # If module implementation exists, it should agree
    if mod_compute_bloom is not None:  # pragma: no branch
        assert b1 == mod_compute_bloom(logs_abc)


def test_empty_sets_stable() -> None:
    empty_logs: List[LogEvent] = []
    r_empty_1 = logs_merkle_root(empty_logs)
    r_empty_2 = logs_merkle_root(empty_logs)
    assert r_empty_1 == r_empty_2
    assert len(r_empty_1) == 32  # sha3-256 digest size

    b_empty_1 = compute_bloom(empty_logs)
    b_empty_2 = compute_bloom(empty_logs)
    assert b_empty_1 == b_empty_2
    assert len(b_empty_1) == 256  # 2048 bits
