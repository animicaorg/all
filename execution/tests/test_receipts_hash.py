import hashlib
from dataclasses import dataclass
from typing import List, Sequence, Optional, Any, Dict

import pytest


# -----------------------------
# Minimal local data structures
# -----------------------------

@dataclass
class LogEvent:
    address: bytes        # 32 bytes
    topics: List[bytes]   # variable length; each topic is bytes
    data: bytes           # arbitrary bytes


@dataclass
class Receipt:
    status: int           # 0=SUCCESS, 1=REVERT, 2=OOG (example mapping)
    gas_used: int
    logs_bloom: bytes     # 2048-bit bloom, 256 bytes
    logs: List[LogEvent]


# -----------------------------
# Spec-aligned helper functions
# -----------------------------

def _sha3_256(x: bytes) -> bytes:
    return hashlib.sha3_256(x).digest()


def _encode_log_leaf(e: LogEvent) -> bytes:
    """
    Deterministic, order-preserving, CBOR-like but tiny encoding for logs.
    This mirrors the "canonical" spirit without depending on the project's encoder.
    """
    if len(e.address) != 32:
        raise ValueError("address must be 32 bytes")
    out = bytearray()
    out.extend(e.address)
    out.extend(len(e.topics).to_bytes(4, "big"))
    for t in e.topics:
        out.extend(len(t).to_bytes(2, "big"))
        out.extend(t)
    out.extend(len(e.data).to_bytes(4, "big"))
    out.extend(e.data)
    return bytes(out)


def _logs_merkle_root(logs: Sequence[LogEvent]) -> bytes:
    if not logs:
        return _sha3_256(b"")
    layer = [_sha3_256(_encode_log_leaf(e)) for e in logs]
    while len(layer) > 1:
        nxt = []
        for i in range(0, len(layer), 2):
            left = layer[i]
            right = layer[i + 1] if i + 1 < len(layer) else layer[i]
            nxt.append(_sha3_256(left + right))
        layer = nxt
    return layer[0]


def _compute_bloom(logs: Sequence[LogEvent]) -> bytes:
    """
    2048-bit bloom (256 bytes). Set bits for address and each topic of each log.
    Positions derived from SHA3-256 (three 11-bit slices).
    """
    bloom = 0
    def _add(x: bytes) -> None:
        nonlocal bloom
        h = _sha3_256(x)
        for off in (0, 2, 4):
            bit = ((h[off] << 8) | h[off + 1]) & 2047
            bloom |= (1 << bit)
    for e in logs:
        _add(e.address)
        for t in e.topics:
            _add(t)
    return bloom.to_bytes(256, "big")


def spec_encode_receipt(r: Receipt) -> bytes:
    """
    Canonical, deterministic encoding for a Receipt sufficient for hashing tests.
    Domain-separated and order-stable. Not a full CBOR implementation, but a
    compact, unambiguous byte layout aligned with the spec's goals.

    Layout:
      tag       = b"ANIMICA|RECEIPT|v1"
      status    = 1 byte
      gas_used  = 8 bytes big-endian
      logsBloom = 256 bytes
      n_logs    = u32 big-endian
      logs[i]   = _encode_log_leaf(log)
    """
    if not (0 <= r.status <= 255):
        raise ValueError("status out of range")
    if len(r.logs_bloom) != 256:
        raise ValueError("logs_bloom must be 256 bytes")
    out = bytearray()
    out.extend(b"ANIMICA|RECEIPT|v1")
    out.append(r.status & 0xFF)
    out.extend(int(r.gas_used).to_bytes(8, "big", signed=False))
    out.extend(r.logs_bloom)
    out.extend(len(r.logs).to_bytes(4, "big"))
    for lg in r.logs:
        out.extend(_encode_log_leaf(lg))
    return bytes(out)


def spec_receipt_hash(r: Receipt) -> bytes:
    """Spec hash = SHA3-256(spec_encode_receipt(r))."""
    return _sha3_256(spec_encode_receipt(r))


# ----------------------------------------
# Optional: hook into project implementation
# ----------------------------------------

def _try_module_hooks() -> Dict[str, Any]:
    """
    Attempt to import project-provided types/encoders.
    Returns a dict with optional 'ModReceipt' and 'encode' callables.
    """
    hooks: Dict[str, Any] = {}
    try:  # project types
        from execution.types.receipt import Receipt as ModReceipt  # type: ignore
        hooks["ModReceipt"] = ModReceipt
    except Exception:
        pass

    # encoding function (name may vary)
    for mod_name, fn_name in (
        ("execution.receipts.encoding", "encode_receipt"),
        ("execution.receipts.encoding", "dumps"),
        ("execution.receipts.encoding", "to_cbor"),
    ):
        try:
            mod = __import__(mod_name, fromlist=[fn_name])  # type: ignore
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                hooks["encode"] = fn
                break
        except Exception:
            continue

    return hooks


def _maybe_build_mod_receipt(hooks: Dict[str, Any], r: Receipt) -> Optional[Any]:
    """
    Best-effort adapter: if the project's Receipt dataclass exists and appears to
    accept similarly named fields, instantiate it. Otherwise return None.
    """
    ModReceipt = hooks.get("ModReceipt")
    if ModReceipt is None:
        return None

    # Try common field names; fall back if constructor raises.
    candidates = [
        dict(status=r.status, gas_used=r.gas_used, logs_bloom=r.logs_bloom, logs=r.logs),
        dict(status=r.status, gasUsed=r.gas_used, logsBloom=r.logs_bloom, logs=r.logs),
    ]
    for kwargs in candidates:
        try:
            return ModReceipt(**kwargs)
        except Exception:
            continue
    return None


# -----------------
# Test fixtures
# -----------------

def _addr(byte: int) -> bytes:
    return bytes([byte]) * 32


def _ev(ab: int, topics: Sequence[bytes], data: bytes) -> LogEvent:
    return LogEvent(address=_addr(ab), topics=list(topics), data=data)


@pytest.fixture
def receipt_vectors() -> List[Receipt]:
    # Vector 1: no logs
    logs1: List[LogEvent] = []
    bloom1 = _compute_bloom(logs1)
    r1 = Receipt(status=0, gas_used=21_000, logs_bloom=bloom1, logs=logs1)

    # Vector 2: two logs with varied topics/data
    logs2 = [
        _ev(0xAA, [b"topic/A", b"k1"], b"payload-alpha"),
        _ev(0xBB, [b"topic/B"], b"payload-beta"),
    ]
    bloom2 = _compute_bloom(logs2)
    r2 = Receipt(status=0, gas_used=50_000, logs_bloom=bloom2, logs=logs2)

    # Vector 3: same as 2 but different gas_used and status → different hash
    r3 = Receipt(status=2, gas_used=50_001, logs_bloom=bloom2, logs=logs2)

    return [r1, r2, r3]


# ------------
# The tests
# ------------

def test_spec_encoding_is_stable_and_ordered(receipt_vectors: List[Receipt]) -> None:
    # Encode twice → identical bytes and hash; changing any field → hash changes.
    r1, r2, r3 = receipt_vectors

    enc1_a = spec_encode_receipt(r1)
    enc1_b = spec_encode_receipt(r1)
    assert enc1_a == enc1_b
    assert spec_receipt_hash(r1) == spec_receipt_hash(r1)

    # Logs Merkle root must be order-sensitive; bloom order-insensitive (sanity).
    # (We don't store the root in the receipt bytes, but we assert the helper behavior.)
    logs_root_forward = _logs_merkle_root(r2.logs)
    logs_root_reverse = _logs_merkle_root(list(reversed(r2.logs)))
    assert logs_root_forward != logs_root_reverse

    bloom_forward = _compute_bloom(r2.logs)
    bloom_reverse = _compute_bloom(list(reversed(r2.logs)))
    assert bloom_forward == bloom_reverse

    # Changing status or gas_used must alter the receipt hash
    assert spec_receipt_hash(r2) != spec_receipt_hash(r3)


def test_module_encoding_matches_spec_if_available(receipt_vectors: List[Receipt]) -> None:
    hooks = _try_module_hooks()
    mod_encode = hooks.get("encode")
    if mod_encode is None:
        pytest.skip("Project receipt encoder not available; spec-only checks executed")

    # Try vectors 1 & 2 for a reasonable cross-section
    for base in receipt_vectors[:2]:
        mod_rcpt = _maybe_build_mod_receipt(hooks, base)
        if mod_rcpt is None:
            pytest.skip("Project Receipt type not constructible with expected fields")

        try:
            mod_bytes: bytes = mod_encode(mod_rcpt)  # type: ignore[arg-type]
        except Exception as e:
            pytest.fail(f"Project encoder raised unexpectedly: {e!r}")

        spec_bytes = spec_encode_receipt(base)
        spec_hash = spec_receipt_hash(base)

        # Primary assertion: bytes exactly match our spec encoding
        assert isinstance(mod_bytes, (bytes, bytearray))
        mod_bytes = bytes(mod_bytes)
        assert mod_bytes == spec_bytes, "Project encoding must match spec-defined layout"

        # Hash must also match (domain separated by our encoder)
        assert hashlib.sha3_256(mod_bytes).digest() == spec_hash


def test_hash_changes_when_logs_change() -> None:
    # Two receipts identical except a single topic byte → hashes differ.
    logs_a = [_ev(0xCC, [b"K"], b"D")]
    logs_b = [_ev(0xCC, [b"L"], b"D")]  # topic differs
    r_a = Receipt(status=0, gas_used=30_000, logs_bloom=_compute_bloom(logs_a), logs=logs_a)
    r_b = Receipt(status=0, gas_used=30_000, logs_bloom=_compute_bloom(logs_b), logs=logs_b)

    h_a = spec_receipt_hash(r_a)
    h_b = spec_receipt_hash(r_b)
    assert h_a != h_b


def test_reencode_produces_identical_bytes(receipt_vectors: List[Receipt]) -> None:
    r = receipt_vectors[1]
    b1 = spec_encode_receipt(r)
    # Simulate "round-trip": we can't truly decode without a full decoder,
    # but re-encoding the same object must be bit-for-bit identical.
    b2 = spec_encode_receipt(r)
    assert b1 == b2
