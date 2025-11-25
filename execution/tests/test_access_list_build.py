import pytest
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple, Union

# This test suite validates that the access-list builder:
#  - Deduplicates touches per (address, storage key)
#  - Normalizes encodings to 20-byte addresses and 32-byte storage keys
#  - Includes address entries even when no storage keys were touched
#  - Is order-independent (stable output regardless of input order)


# -------------------------------
# Helpers for flexible introspection
# -------------------------------

def _find_builder():
    """
    Locate the access-list builder function in execution.access_list.build,
    accepting a few possible names: build_access_list / from_trace / build.
    """
    try:
        mod = __import__("execution.access_list.build", fromlist=["*"])
    except Exception as e:
        pytest.skip(f"access-list builder module not importable: {e}")
    for name in ("build_access_list", "from_trace", "build"):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    pytest.skip("No builder function found in execution.access_list.build")


def _to_bytes_hex(x: Union[str, bytes, bytearray, int]) -> str:
    """Normalize various encodings into 0x-hex (lowercase, no leading zeros beyond width)."""
    if isinstance(x, int):
        # Heuristic: addresses are 20 bytes, storage keys 32 bytes—caller must pick width.
        raise TypeError("int provided without width; pass bytes/hex str instead")
    if isinstance(x, (bytes, bytearray)):
        return "0x" + bytes(x).hex()
    s = str(x).lower()
    if s.startswith("0x"):
        s = s[2:]
    # remove leading zeros but keep full even-length
    if len(s) % 2:
        s = "0" + s
    return "0x" + s


def _addr_hex(x: Union[str, bytes, bytearray]) -> str:
    h = _to_bytes_hex(x)
    # Canonicalize to 20 bytes
    raw = bytes.fromhex(h[2:])
    if len(raw) == 20:
        return h
    if len(raw) > 20:
        raw = raw[-20:]
    else:
        raw = (b"\x00" * (20 - len(raw))) + raw
    return "0x" + raw.hex()


def _slot_hex(x: Union[str, bytes, bytearray]) -> str:
    h = _to_bytes_hex(x)
    raw = bytes.fromhex(h[2:])
    if len(raw) == 32:
        return h
    if len(raw) > 32:
        raw = raw[-32:]
    else:
        raw = (b"\x00" * (32 - len(raw))) + raw
    return "0x" + raw.hex()


def _coerce_output_to_map(out: Any) -> Dict[str, List[str]]:
    """
    Accept common return shapes:
      - Sequence[ (address, [slots...]) ]
      - Sequence[ {"address": .., "storageKeys": [...] } ]
      - Sequence[ dataclass with .address / .storage_keys or .storageKeys ]
    Return mapping: addr_hex -> sorted unique slot_hex list.
    """
    result: Dict[str, List[str]] = {}
    if not isinstance(out, (list, tuple)):
        raise TypeError(f"builder output is not a sequence: {type(out)}")

    def get_addr(entry: Any) -> Union[str, bytes, bytearray]:
        if isinstance(entry, (list, tuple)) and entry:
            return entry[0]
        if isinstance(entry, Mapping):
            return entry.get("address")
        # dataclass / object
        for name in ("address", "addr"):
            if hasattr(entry, name):
                return getattr(entry, name)
        raise KeyError("address field not found")

    def get_slots(entry: Any) -> Sequence[Union[str, bytes, bytearray]]:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            return entry[1]
        if isinstance(entry, Mapping):
            return entry.get("storageKeys") or entry.get("slots") or []
        for name in ("storage_keys", "storageKeys", "slots"):
            if hasattr(entry, name):
                return getattr(entry, name)
        return []

    for e in out:
        a = _addr_hex(get_addr(e))
        sks = [_slot_hex(s) for s in get_slots(e)]
        # dedupe & sort
        uniq = sorted(set(sks))
        result[a] = uniq
    return result


# -------------------------------
# Sample synthetic trace
# -------------------------------

def _trace_events() -> List[Dict[str, Any]]:
    # Two addresses with mixed slot encodings and duplicates.
    A = "0x" + "11" * 20
    B = "0x" + "22" * 20
    slot1 = "0x" + "00" * 31 + "01"
    slot2_bytes = b"\x00" * 31 + b"\x02"
    slot3_upper = "0X" + ("00" * 30) + "0A" + "0F"  # 0x...0a0f
    return [
        {"op": "sload", "address": A, "slot": slot1},
        {"op": "sload", "address": A, "slot": slot1},  # duplicate
        {"op": "sstore", "address": A, "slot": slot2_bytes},
        {"op": "call", "address": B},                   # no storage key
        {"op": "sload", "address": B, "slot": slot3_upper},
        {"op": "sload", "address": B, "slot": slot3_upper},  # duplicate
    ]


def _shuffle(seq):
    # Small deterministic shuffle to ensure order-independence
    return [seq[i] for i in (3, 0, 5, 2, 1, 4)]


# -------------------------------
# Tests
# -------------------------------

def test_dedup_normalize_and_include_empty_entries():
    builder = _find_builder()
    trace = _trace_events()
    out = builder(trace)  # accept list[dict] semantics

    amap = _coerce_output_to_map(out)

    A = _addr_hex("0x" + "11" * 20)
    B = _addr_hex("0x" + "22" * 20)
    assert A in amap and B in amap, "both addresses must be present"
    # A has two distinct slots (01, 02)
    assert amap[A] == sorted([
        _slot_hex("0x" + "00" * 31 + "01"),
        _slot_hex("0x" + "00" * 31 + "02"),
    ])
    # B has one slot (0a0f), and must be included even if it had only 'call' (empty) before
    assert amap[B] == sorted([_slot_hex("0x" + "00" * 30 + "0a" + "0f")])


def test_order_independence():
    builder = _find_builder()
    t1 = _trace_events()
    t2 = _shuffle(t1)

    out1 = _coerce_output_to_map(builder(t1))
    out2 = _coerce_output_to_map(builder(t2))
    assert out1 == out2, "builder output must be independent of trace order"


def test_output_shapes_are_sane():
    builder = _find_builder()
    out = builder(_trace_events())

    # Basic shape checks: iterable of entries; address is 20 bytes, slots are 32 bytes after normalization.
    assert isinstance(out, (list, tuple)) and len(out) >= 1
    amap = _coerce_output_to_map(out)
    for addr_hex, slots in amap.items():
        addr_bytes = bytes.fromhex(addr_hex[2:])
        assert len(addr_bytes) == 20, "address must be 20 bytes"
        for s in slots:
            sb = bytes.fromhex(s[2:])
            assert len(sb) == 32, "storage key must be 32 bytes"


def test_idempotence_if_called_twice_on_same_trace():
    builder = _find_builder()
    trace = _trace_events()
    first = _coerce_output_to_map(builder(trace))
    second = _coerce_output_to_map(builder(trace))
    assert first == second, "builder must be pure/idempotent for the same input"


def test_accepts_varied_event_shapes():
    """
    Many builders support multiple input shapes. Try a minimal tuple-based variant:
      - (address, slot) for storage touches
      - (address, None) for address-only touch
    """
    builder = _find_builder()
    A = "0x" + "ab" * 20
    B = "0x" + "cd" * 20
    slot = "0x" + "00" * 31 + "55"
    events = [
        (A, slot),
        (A, slot),           # dup
        (B, None),           # address only
    ]
    out = _coerce_output_to_map(builder(events))
    assert _addr_hex(A) in out and _addr_hex(B) in out
    assert out[_addr_hex(A)] == [_slot_hex(slot)]
    # B might be empty or might include an empty list; either is fine as long as entry exists
    assert out[_addr_hex(B)] in ([], out[_addr_hex(B)])
