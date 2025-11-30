"""
omni_sdk.contracts.events
=========================

Helpers to work with contract events:
- Build an event selector/topic
- Decode logs from a receipt using the contract ABI
- Filter decoded events by name

This module intentionally delegates ABI-specific details to `omni_sdk.types.abi`
when available (recommended), but contains careful fallbacks for selector
computation and basic matching.

Public API
----------
- build_event_index(abi) -> Dict[bytes, (name, event_def)]
- topic_for_event(abi, name) -> bytes
- decode_logs(abi, logs) -> List[DecodedEvent]
- decode_receipt_events(abi, logs) -> List[DecodedEvent]    # alias
- filter_logs_by_event(abi, logs, name) -> List[DecodedEvent]
- find_first_event(abi, receipt_or_logs, name) -> Optional[DecodedEvent]

Where a DecodedEvent is a dict with keys:
    {
      "name": str,
      "args": dict | list,     # ABI-decoded arguments
      "address": str | None,   # if present in the log
      "logIndex": int | None,  # if present in the log
      "topics": [ "0x…" , ... ],
      "data": "0x…",
    }
"""

from __future__ import annotations

from typing import (Any, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Tuple)

# --- Utilities ----------------------------------------------------------------

try:
    from omni_sdk.utils.bytes import from_hex as _from_hex
    from omni_sdk.utils.bytes import to_hex as _to_hex  # type: ignore
except Exception:

    def _to_hex(b: bytes) -> str:
        return "0x" + bytes(b).hex()

    def _from_hex(s: str) -> bytes:
        s = s[2:] if isinstance(s, str) and s.startswith("0x") else s
        return bytes.fromhex(s)


try:
    from omni_sdk.utils.hash import keccak256 as _keccak256  # type: ignore
except Exception:
    import hashlib

    def _keccak256(b: bytes) -> bytes:
        try:
            # pysha3 provides keccak_256 on hashlib in many envs
            return hashlib.new("keccak256", b).digest()  # type: ignore[arg-type]
        except Exception:
            # Fallback to sha3_256 (not identical but better than nothing for fallback path)
            if not hasattr(hashlib, "sha3_256"):
                raise RuntimeError(
                    "Need keccak256 or sha3_256 in hashlib for event topics"
                )
            return hashlib.sha3_256(b).digest()


# --- ABI helpers ---------------------------------------------------------------

# We rely on the ABI module for normalization and decoding when available.
try:
    from omni_sdk.types.abi import \
        decode_event as _abi_decode_event  # preferred if present
    from omni_sdk.types.abi import \
        normalize_abi as _normalize_abi  # type: ignore
except Exception as _e:  # pragma: no cover
    raise RuntimeError(
        "omni_sdk.types.abi is required for robust event decoding"
    ) from _e

# Optional helper (if the ABI module exposes it)
try:
    from omni_sdk.types.abi import \
        event_selector as _abi_event_selector  # type: ignore
except Exception:
    _abi_event_selector = None  # type: ignore

JsonDict = Dict[str, Any]


def _iter_events(
    abi_norm: Mapping[str, Any],
) -> Iterable[Tuple[str, Mapping[str, Any]]]:
    """
    Yield (name, event_def) from a normalized ABI. Supports either:
      - dict with "events" as {name: def} or [defs]
      - list of entries with {"type": "event", "name": "...", ...}
    """
    if "events" in abi_norm:
        evs = abi_norm["events"]
        if isinstance(evs, Mapping):
            for name, ev in evs.items():
                if isinstance(ev, Mapping):
                    yield str(name), ev
        elif isinstance(evs, (list, tuple)):
            for ev in evs:
                if isinstance(ev, Mapping) and str(ev.get("type", "event")) == "event":
                    name = str(ev.get("name", ""))
                    if name:
                        yield name, ev
    elif isinstance(abi_norm, (list, tuple)):
        for item in abi_norm:
            if isinstance(item, Mapping) and str(item.get("type", "event")) == "event":
                name = str(item.get("name", ""))
                if name:
                    yield name, item
    # else: empty


def _event_signature(name: str, ev: Mapping[str, Any]) -> str:
    """
    Compute the canonical signature string `Name(type1,type2,...)` for topic hashing.
    """
    inputs = ev.get("inputs", [])
    if not isinstance(inputs, (list, tuple)):
        inputs = []
    types: List[str] = []
    for arg in inputs:
        if isinstance(arg, Mapping) and "type" in arg:
            types.append(str(arg["type"]))
    return f"{name}(" + ",".join(types) + ")"


def _event_selector_from_sig(sig: str) -> bytes:
    """
    Compute 32-byte selector from signature string (keccak256(sig) like EVM).
    """
    return _keccak256(sig.encode("utf-8"))


def _count_indexed(ev: Mapping[str, Any]) -> int:
    inputs = ev.get("inputs", [])
    if not isinstance(inputs, (list, tuple)):
        return 0
    n = 0
    for arg in inputs:
        if isinstance(arg, Mapping) and bool(arg.get("indexed", False)):
            n += 1
    return n


# -----------------------------------------------------------------------------


def build_event_index(
    abi: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> Dict[bytes, Tuple[str, Mapping[str, Any]]]:
    """
    Build a selector → (name, event_def) index for fast matching.
    """
    abi_norm = _normalize_abi(abi)  # type: ignore[arg-type]
    index: Dict[bytes, Tuple[str, Mapping[str, Any]]] = {}
    for name, ev in _iter_events(abi_norm):
        if bool(ev.get("anonymous", False)):
            # Anonymous events have no selector topic; skip in this index.
            continue
        if _abi_event_selector is not None:
            try:
                sel = _abi_event_selector(ev)  # type: ignore[misc]
                if isinstance(sel, str):
                    sel = _from_hex(sel)
                index[bytes(sel)] = (name, ev)
                continue
            except Exception:
                pass  # fall back to local computation
        sig = _event_signature(name, ev)
        index[_event_selector_from_sig(sig)] = (name, ev)
    return index


def topic_for_event(
    abi: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    name: str,
) -> bytes:
    """
    Compute the selector topic for the named (non-anonymous) event.
    """
    abi_norm = _normalize_abi(abi)  # type: ignore[arg-type]
    for ev_name, ev in _iter_events(abi_norm):
        if ev_name != name or bool(ev.get("anonymous", False)):
            continue
        if _abi_event_selector is not None:
            try:
                sel = _abi_event_selector(ev)  # type: ignore[misc]
                return _from_hex(sel) if isinstance(sel, str) else bytes(sel)
            except Exception:
                pass
        sig = _event_signature(ev_name, ev)
        return _event_selector_from_sig(sig)
    raise KeyError(f"event not found or is anonymous: {name!r}")


def _norm_topic_bytes_list(topics: Any) -> List[bytes]:
    out: List[bytes] = []
    if not isinstance(topics, (list, tuple)):
        return out
    for t in topics:
        if isinstance(t, (bytes, bytearray)):
            out.append(bytes(t))
        elif isinstance(t, str):
            out.append(_from_hex(t))
        else:
            # ignore unknown types
            pass
    return out


def _norm_data_bytes(data: Any) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        return _from_hex(data)
    return b""


def _match_anonymous_candidates(
    abi_norm: Mapping[str, Any],
    topic_count: int,
) -> List[Tuple[str, Mapping[str, Any]]]:
    """
    Return candidate (name, event) for anonymous events with the right number of indexed args.
    """
    cands: List[Tuple[str, Mapping[str, Any]]] = []
    for name, ev in _iter_events(abi_norm):
        if not bool(ev.get("anonymous", False)):
            continue
        if _count_indexed(ev) == topic_count:
            cands.append((name, ev))
    return cands


def _decode_event_with_abi(
    abi_norm: Mapping[str, Any],
    name: str,
    ev: Mapping[str, Any],
    topics_b: List[bytes],
    data_b: bytes,
) -> Any:
    """
    Delegate to ABI decoder. The ABI module may accept different call signatures;
    try a couple of common ones.
    """
    # Preferred: decode_event(abi, name, topics, data)
    try:
        return _abi_decode_event(abi_norm, name, topics_b, data_b)  # type: ignore[misc]
    except TypeError:
        # Alternate: decode_event(event_def, topics, data)
        return _abi_decode_event(ev, topics_b, data_b)  # type: ignore[misc]


def decode_logs(
    abi: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    logs: Sequence[Mapping[str, Any]],
) -> List[JsonDict]:
    """
    Decode a list of raw log dicts using the provided ABI.

    Each input log is expected to look like:
        { "address": "anim1…", "topics": ["0x..", ...], "data": "0x..", "logIndex": 0, ... }
    but bytes are also accepted for `topics` and `data`.

    Returns a list of decoded event dicts, preserving per-log metadata.
    Logs that cannot be matched/decoded are omitted.
    """
    abi_norm = _normalize_abi(abi)  # type: ignore[arg-type]
    index = build_event_index(abi_norm)

    decoded: List[JsonDict] = []

    for lg in logs:
        if not isinstance(lg, Mapping):
            continue

        topics_b = _norm_topic_bytes_list(lg.get("topics"))
        data_b = _norm_data_bytes(lg.get("data"))
        address = lg.get("address")
        log_index = lg.get("logIndex", lg.get("index"))

        name: Optional[str] = None
        ev_def: Optional[Mapping[str, Any]] = None

        # 1) Non-anonymous path: first topic is selector
        if topics_b:
            head = topics_b[0]
            if head in index:
                name, ev_def = index[head]

        # 2) Anonymous fallback: match by indexed count and attempt decode
        if ev_def is None:
            cands = _match_anonymous_candidates(abi_norm, topic_count=len(topics_b))
            for cand_name, cand_ev in cands:
                try:
                    _ = _decode_event_with_abi(
                        abi_norm, cand_name, cand_ev, topics_b, data_b
                    )
                    name, ev_def = cand_name, cand_ev
                    break
                except Exception:
                    continue  # try next candidate

        if name is None or ev_def is None:
            # Could not match this log against ABI — skip it.
            continue

        # Decode arguments via ABI
        try:
            args = _decode_event_with_abi(abi_norm, name, ev_def, topics_b, data_b)
        except Exception:
            # If decoding fails, skip the log for safety
            continue

        decoded.append(
            {
                "name": name,
                "args": args,
                "address": address if isinstance(address, str) else None,
                "logIndex": int(log_index) if isinstance(log_index, int) else None,
                "topics": [_to_hex(t) for t in topics_b],
                "data": _to_hex(data_b),
            }
        )

    return decoded


def decode_receipt_events(
    abi: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    logs: Sequence[Mapping[str, Any]],
) -> List[JsonDict]:
    """
    Alias for decode_logs(); named for ergonomic use with tx receipts.
    """
    return decode_logs(abi, logs)


def filter_logs_by_event(
    abi: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    logs: Sequence[Mapping[str, Any]],
    name: str,
) -> List[JsonDict]:
    """
    Decode and keep only events with the given name.
    """
    all_decoded = decode_logs(abi, logs)
    return [e for e in all_decoded if e.get("name") == name]


def find_first_event(
    abi: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    receipt_or_logs: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    name: str,
) -> Optional[JsonDict]:
    """
    Convenience to locate the first occurrence of an event in a receipt or raw log list.
    Returns the decoded event dict or None if not found.
    """
    logs: Sequence[Mapping[str, Any]]
    if isinstance(receipt_or_logs, Mapping) and "logs" in receipt_or_logs:
        logs = receipt_or_logs["logs"]  # type: ignore[index]
    elif isinstance(receipt_or_logs, (list, tuple)):
        logs = receipt_or_logs  # type: ignore[assignment]
    else:
        return None

    for ev in decode_logs(abi, logs):
        if ev.get("name") == name:
            return ev
    return None


__all__ = [
    "build_event_index",
    "topic_for_event",
    "decode_logs",
    "decode_receipt_events",
    "filter_logs_by_event",
    "find_first_event",
]
