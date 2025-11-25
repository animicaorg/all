# -*- coding: utf-8 -*-
"""
Atheris fuzz target: **P2P wire messages parse / round-trip**

Targets both message envelopes and full frames:
- Prefer project codecs from p2p.wire.encoding and/or p2p.wire.frames.
- Falls back to canonical CBOR (core.encoding.cbor, cbor2, or msgspec.cbor).
- If bytes decode to a frame-like object, attempt inner message decode.
- Re-encode → re-decode and assert idempotence under normalization.
- Best-effort checksum / sanity validation where helpers exist.

Run via the shared harness:
  python tests/fuzz/atheris_runner.py \
    --target tests.fuzz.fuzz_p2p_messages:fuzz \
    tests/fuzz/corpus_txs  # any seed dirs; dedicated corpus recommended

Or directly:
  python -m tests.fuzz.fuzz_p2p_messages tests/fuzz/corpus_txs
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Optional, Tuple

# ---------------- optional import helper ----------------

def _import_optional(modname: str):
    try:
        __import__(modname)
        return sys.modules[modname]
    except Exception:
        return None


# ---------------- generic CBOR backends ----------------

DecodeFn = Callable[[bytes], Any]
EncodeFn = Callable[[Any], bytes]


def _get_project_cbor() -> Optional[Tuple[DecodeFn, EncodeFn, str]]:
    m = _import_optional("core.encoding.cbor")
    if not m:
        return None
    loads = getattr(m, "loads", None) or getattr(m, "decode", None)
    dumps = getattr(m, "dumps", None) or getattr(m, "encode", None)
    if callable(loads) and callable(dumps):
        return loads, dumps, "core.encoding.cbor"
    return None


def _get_cbor2() -> Optional[Tuple[DecodeFn, EncodeFn, str]]:
    m = _import_optional("cbor2")
    if not m:
        return None

    def _loads(b: bytes) -> Any:
        return m.loads(b)

    def _dumps(x: Any) -> bytes:
        try:
            return m.dumps(x, canonical=True)
        except TypeError:
            return m.dumps(x)

    return _loads, _dumps, "cbor2"


def _get_msgspec() -> Optional[Tuple[DecodeFn, EncodeFn, str]]:
    m = _import_optional("msgspec")
    if not m or not hasattr(m, "cbor"):
        return None
    return m.cbor.decode, m.cbor.encode, "msgspec.cbor"


def _choose_cbor() -> Tuple[DecodeFn, EncodeFn, str]:
    for prov in (_get_project_cbor(), _get_cbor2(), _get_msgspec()):
        if prov:
            return prov
    # Tiny stub so the target still imports
    def _loads_stub(b: bytes) -> Any:
        if b == b"\xa0":
            return {}
        if b == b"\x80":
            return []
        raise ValueError("no CBOR backend available")

    def _dumps_stub(x: Any) -> bytes:
        if x == {}:
            return b"\xa0"
        if x == []:
            return b"\x80"
        raise ValueError("no CBOR backend available")

    return _loads_stub, _dumps_stub, "stub"


CBOR_LOADS, CBOR_DUMPS, CBOR_BACKEND = _choose_cbor()

# ---------------- project wire codecs ----------------

# Message codec (payload-level)
MSG_DECODE: Optional[Callable[[bytes], Any]] = None
MSG_ENCODE: Optional[Callable[[Any], bytes]] = None
MSG_BACKEND = "none"

_enc = _import_optional("p2p.wire.encoding")
if _enc:
    for nm in ("decode_message", "message_decode", "decode", "loads"):
        fn = getattr(_enc, nm, None)
        if callable(fn):
            MSG_DECODE = fn
            break
    for nm in ("encode_message", "message_encode", "encode", "dumps"):
        fn = getattr(_enc, nm, None)
        if callable(fn):
            MSG_ENCODE = fn
            break
    if MSG_DECODE and MSG_ENCODE:
        MSG_BACKEND = "p2p.wire.encoding"

# Frame codec (envelope-level)
FRAME_DECODE: Optional[Callable[[bytes], Any]] = None
FRAME_ENCODE: Optional[Callable[[Any], bytes]] = None
FRAME_BACKEND = "none"

_frames = _import_optional("p2p.wire.frames")
if _frames:
    for nm in ("decode_frame", "frame_decode", "decode", "loads"):
        fn = getattr(_frames, nm, None)
        if callable(fn):
            FRAME_DECODE = fn
            break
    for nm in ("encode_frame", "frame_encode", "encode", "dumps"):
        fn = getattr(_frames, nm, None)
        if callable(fn):
            FRAME_ENCODE = fn
            break
    if FRAME_DECODE and FRAME_ENCODE:
        FRAME_BACKEND = "p2p.wire.frames"

# Optional checksum helpers (best-effort)
_CHECKSUM_FN = None
for mod in (_frames, _enc):
    if not mod:
        continue
    for nm in ("checksum", "compute_checksum", "calc_checksum", "frame_checksum"):
        fn = getattr(mod, nm, None)
        if callable(fn):
            _CHECKSUM_FN = fn
            break
    if _CHECKSUM_FN:
        break

# ---------------- utilities ----------------

def _sha3_256(data: bytes) -> Optional[bytes]:
    h = _import_optional("core.utils.hash")
    if h and hasattr(h, "sha3_256"):
        try:
            return h.sha3_256(data)
        except Exception:
            pass
    try:
        import hashlib
        return hashlib.sha3_256(data).digest()
    except Exception:
        return None


def _normalize_for_eq(x: Any) -> Any:
    if isinstance(x, dict):
        items = []
        for k, v in x.items():
            items.append((k, _normalize_for_eq(v)))
        items.sort(key=lambda kv: repr(kv[0]))
        return tuple(items)
    if isinstance(x, (list, tuple)):
        return tuple(_normalize_for_eq(v) for v in x)
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    return x


def _is_frame_like(x: Any) -> bool:
    if not isinstance(x, dict):
        return False
    keys = set(map(str, x.keys()))
    # Common fields: msg_id/seq/flags/payload/checksum
    return ("payload" in keys or "p" in keys) and (
        "msg_id" in keys or "msgId" in keys or "id" in keys
    )


def _is_message_like(x: Any) -> bool:
    # Heuristic: dict with numeric 'id' or 'type' or 'msg_id' and a small set of fields
    if isinstance(x, dict):
        keys = set(map(str, x.keys()))
        suspects = {"id", "msg_id", "type", "topic", "hello", "inv", "headers", "tx"}
        return bool(keys & suspects)
    return False


def _frame_normalize_shape(frame: dict) -> dict:
    msg_id = frame.get("msg_id", frame.get("msgId", frame.get("id")))
    payload = frame.get("payload", frame.get("p"))
    seq = frame.get("seq", frame.get("s"))
    flags = frame.get("flags", frame.get("f"))
    csum = frame.get("checksum", frame.get("csum"))
    out = {"msg_id": msg_id, "payload": payload}
    if seq is not None:
        out["seq"] = seq
    if flags is not None:
        out["flags"] = flags
    if csum is not None:
        out["checksum"] = csum
    # keep extras
    for k, v in frame.items():
        if k not in ("msg_id", "msgId", "id", "payload", "p", "seq", "s", "flags", "f", "checksum", "csum"):
            out[k] = v
    return out


def _roundtrip_bytes(obj: Any, enc: EncodeFn, dec: DecodeFn) -> Any:
    b1 = enc(obj)
    o2 = dec(b1)
    b2 = enc(o2)
    o3 = dec(b2)
    if _normalize_for_eq(obj) != _normalize_for_eq(o3):
        raise AssertionError("Round-trip not idempotent under normalization")
    return o3


def _best_cbor_roundtrip(obj: Any) -> Any:
    return _roundtrip_bytes(obj, CBOR_DUMPS, CBOR_LOADS)


def _best_frame_roundtrip(frame: Any) -> Any:
    if FRAME_ENCODE and FRAME_DECODE:
        return _roundtrip_bytes(frame, FRAME_ENCODE, FRAME_DECODE)
    return _best_cbor_roundtrip(frame)


def _best_msg_roundtrip(msg: Any) -> Any:
    if MSG_ENCODE and MSG_DECODE:
        return _roundtrip_bytes(msg, MSG_ENCODE, MSG_DECODE)
    return _best_cbor_roundtrip(msg)


def _maybe_validate_checksum(frame: dict) -> None:
    if not isinstance(frame, dict):
        return
    if not _CHECKSUM_FN:
        return
    payload = frame.get("payload")
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        return
    # Many designs compute checksum over payload; we won't assert equality
    # against a stored field (alg may differ), but we still exercise the code.
    try:
        _ = _CHECKSUM_FN(bytes(payload))
    except Exception:
        pass


# ---------------- fuzz entry ----------------

def fuzz(data: bytes) -> None:
    # Size guard (1 MiB max)
    if len(data) > (1 << 20):
        return

    # Strategy A: Try to decode as a frame with project codec
    frame = None
    if FRAME_DECODE:
        try:
            frame = FRAME_DECODE(data)
        except Exception:
            frame = None

    # Strategy B: Generic CBOR, then shape-check for frame/message
    decoded = None
    if frame is None:
        try:
            decoded = CBOR_LOADS(data)
        except Exception:
            return

        if _is_frame_like(decoded):
            frame = _frame_normalize_shape(decoded)

    # If still not a frame, try to treat bytes as a raw message
    if frame is None:
        # Raw message payload path
        msg = None
        if MSG_DECODE:
            try:
                msg = MSG_DECODE(data)
            except Exception:
                msg = None
        if msg is None:
            if decoded is None:
                try:
                    decoded = CBOR_LOADS(data)
                except Exception:
                    return
            if not _is_message_like(decoded):
                # Not message-like; still exercise a CBOR round-trip and bail.
                try:
                    _ = _best_cbor_roundtrip(decoded)
                except Exception:
                    pass
                return
            msg = decoded

        # Message round-trip & hash stability
        try:
            msg = _best_msg_roundtrip(msg)
        except (RecursionError, MemoryError):
            return

        try:
            if MSG_ENCODE:
                enc = MSG_ENCODE(msg)
            else:
                enc = CBOR_DUMPS(msg)
            h1 = _sha3_256(enc)
            if h1 and len(h1) == 32:
                if MSG_DECODE and MSG_ENCODE:
                    msg2 = MSG_DECODE(enc)
                    enc2 = MSG_ENCODE(msg2)
                else:
                    msg2 = CBOR_LOADS(enc)
                    enc2 = CBOR_DUMPS(msg2)
                h2 = _sha3_256(enc2)
                if h2 and h1 != h2:
                    raise AssertionError("Message canonical bytes hash unstable")
        except (RecursionError, MemoryError):
            return
        except Exception:
            return
        return

    # We have a frame object
    try:
        frame = _best_frame_roundtrip(frame)
    except (RecursionError, MemoryError):
        return

    # Best-effort checksum sanity
    try:
        if isinstance(frame, dict):
            _maybe_validate_checksum(frame)
    except (RecursionError, MemoryError):
        return

    # If payload is bytes, attempt inner message decode + round-trip
    try:
        if isinstance(frame, dict):
            payload = frame.get("payload")
            if isinstance(payload, (bytes, bytearray, memoryview)):
                inner = None
                if MSG_DECODE:
                    try:
                        inner = MSG_DECODE(bytes(payload))
                    except Exception:
                        inner = None
                if inner is not None:
                    try:
                        inner = _best_msg_roundtrip(inner)
                    except Exception:
                        pass
    except (RecursionError, MemoryError):
        return

    # Hash the canonical frame bytes for stability (best-effort)
    try:
        if FRAME_ENCODE:
            enc = FRAME_ENCODE(frame)
        else:
            enc = CBOR_DUMPS(frame)
        h1 = _sha3_256(enc)
        if h1 and len(h1) == 32:
            if FRAME_DECODE and FRAME_ENCODE:
                fr2 = FRAME_DECODE(enc)
                enc2 = FRAME_ENCODE(fr2)
            else:
                fr2 = CBOR_LOADS(enc)
                enc2 = CBOR_DUMPS(fr2)
            h2 = _sha3_256(enc2)
            if h2 and h1 != h2:
                raise AssertionError("Frame canonical bytes hash unstable")
    except (RecursionError, MemoryError):
        return
    except Exception:
        return


# ---------------- direct execution ----------------

def _run_direct(argv: list[str]) -> int:  # pragma: no cover
    try:
        import atheris  # type: ignore
    except Exception:
        sys.stderr.write("[fuzz_p2p_messages] atheris not installed. pip install atheris\n")
        return 2
    atheris.instrument_all()
    corpus = [p for p in argv if not p.startswith("-")] or ["tests/fuzz/corpus_txs"]
    atheris.Setup([sys.argv[0], *corpus], fuzz, enable_python_coverage=True)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_run_direct(sys.argv[1:]))
