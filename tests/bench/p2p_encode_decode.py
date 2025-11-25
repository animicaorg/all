# -*- coding: utf-8 -*-
"""
p2p_encode_decode.py
====================

Benchmark wire-frame encode/decode throughput.

- If the real P2P wire codec exists (p2p.wire.encoding / p2p.wire.frames),
  this uses it directly.
- Otherwise, it falls back to a fast generic codec:
    1) msgspec   (preferred)
    2) cbor2
    3) json (base64 payload)  ← slowest, last resort

Outputs one JSON line with medians and throughputs so tests/bench/runner.py
can ingest it.

Examples:
    python tests/bench/p2p_encode_decode.py
    python tests/bench/p2p_encode_decode.py --frames 200000 --payload 96
    python tests/bench/p2p_encode_decode.py --codec msgspec --repeat 9
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time
from dataclasses import dataclass
from importlib import import_module
from typing import Callable, List, Optional, Tuple


# --------------------------------------------------------------------------- #
# Frame model (portable for fallbacks)
# --------------------------------------------------------------------------- #

@dataclass
class Frame:
    msg_id: int   # e.g., HELLO, INV, ...
    seq: int      # per-connection sequence
    flags: int    # bitfield
    payload: bytes


def _rng(seed: int) -> int:
    # Tiny LCG for reproducible bytes
    a = 6364136223846793005
    c = 1442695040888963407
    return (a * seed + c) & ((1 << 64) - 1)


def gen_frames(n: int, payload_size: int, seed: Optional[int]) -> List[Frame]:
    s = seed if (seed is not None) else int(os.environ.get("PYTHONHASHSEED", "0") or "1337")
    x = (s & ((1 << 64) - 1)) or 1
    out: List[Frame] = []
    for i in range(1, n + 1):
        # pseudo-random but deterministic payload
        buf = bytearray(payload_size)
        for j in range(payload_size):
            x = _rng(x)
            buf[j] = (x >> 32) & 0xFF
        # cycle a few message ids and flags
        msg_id = (i % 32) + 1
        flags = (i % 4)
        out.append(Frame(msg_id=msg_id, seq=i, flags=flags, payload=bytes(buf)))
    return out


# --------------------------------------------------------------------------- #
# Codec adapters
# --------------------------------------------------------------------------- #

Encoder = Callable[[Frame], bytes]
Decoder = Callable[[bytes], Frame]

def _get_p2p_codec() -> Tuple[str, Encoder, Decoder]:
    """
    Try to bind to real p2p.wire encoding. We adapt to several plausible APIs:
      - encoding.dumps_frame / loads_frame
      - encoding.encode_frame / decode_frame
      - encoding.dumps / loads
    Frames class may live at p2p.wire.frames.Frame or similar.
    """
    enc_mod = import_module("p2p.wire.encoding")
    frames_mod = import_module("p2p.wire.frames")

    FrameCls = getattr(frames_mod, "Frame", None)
    if FrameCls is None:
        # Fall back: if there's an Envelope/Frame-like class, try that
        for cand in ("Envelope", "WireFrame"):
            FrameCls = getattr(frames_mod, cand, None)
            if FrameCls is not None:
                break
    if FrameCls is None:
        raise RuntimeError("No Frame class found in p2p.wire.frames")

    # find dumps/loads
    enc_fn = None
    dec_fn = None
    for name in ("dumps_frame", "encode_frame", "dumps", "encode"):
        fn = getattr(enc_mod, name, None)
        if callable(fn):
            enc_fn = fn
            break
    for name in ("loads_frame", "decode_frame", "loads", "decode"):
        fn = getattr(enc_mod, name, None)
        if callable(fn):
            dec_fn = fn
            break
    if enc_fn is None or dec_fn is None:
        raise RuntimeError("Usable encode/decode functions not found in p2p.wire.encoding")

    def _mk(frame: Frame):
        # Best effort mapping (common field names)
        try:
            return FrameCls(msg_id=frame.msg_id, seq=frame.seq, flags=frame.flags, payload=frame.payload)
        except TypeError:
            # try positional
            return FrameCls(frame.msg_id, frame.seq, frame.flags, frame.payload)

    def _enc(frame: Frame) -> bytes:
        return enc_fn(_mk(frame))  # type: ignore[misc]

    def _dec(b: bytes) -> Frame:
        obj = dec_fn(b)  # type: ignore[misc]
        # Map back to our portable Frame
        msg_id = getattr(obj, "msg_id", getattr(obj, "id", 0))
        seq = getattr(obj, "seq", getattr(obj, "sequence", 0))
        flags = getattr(obj, "flags", 0)
        payload = getattr(obj, "payload", getattr(obj, "data", b""))
        return Frame(int(msg_id), int(seq), int(flags), bytes(payload))

    return "p2p-wire", _enc, _dec


def _get_msgspec_codec() -> Tuple[str, Encoder, Decoder]:
    import msgspec

    class F(msgspec.Struct, frozen=True):
        msg_id: int
        seq: int
        flags: int
        payload: bytes

    enc = msgspec.Encoder()
    dec = msgspec.Decoder(F)

    def _enc(frame: Frame) -> bytes:
        return enc.encode(F(frame.msg_id, frame.seq, frame.flags, frame.payload))

    def _dec(b: bytes) -> Frame:
        obj = dec.decode(b)
        return Frame(obj.msg_id, obj.seq, obj.flags, obj.payload)

    return "msgspec", _enc, _dec


def _get_cbor2_codec() -> Tuple[str, Encoder, Decoder]:
    import cbor2

    def _enc(frame: Frame) -> bytes:
        # Compact tuple layout
        return cbor2.dumps((frame.msg_id, frame.seq, frame.flags, frame.payload))

    def _dec(b: bytes) -> Frame:
        msg_id, seq, flags, payload = cbor2.loads(b)
        return Frame(int(msg_id), int(seq), int(flags), bytes(payload))

    return "cbor2", _enc, _dec


def _get_json_codec() -> Tuple[str, Encoder, Decoder]:
    # JSON needs base64 for bytes
    def _enc(frame: Frame) -> bytes:
        d = {
            "msg_id": frame.msg_id,
            "seq": frame.seq,
            "flags": frame.flags,
            "payload_b64": base64.b64encode(frame.payload).decode("ascii"),
        }
        return json.dumps(d, separators=(",", ":"), sort_keys=False).encode("utf-8")

    def _dec(b: bytes) -> Frame:
        d = json.loads(b.decode("utf-8"))
        payload = base64.b64decode(d["payload_b64"])
        return Frame(int(d["msg_id"]), int(d["seq"]), int(d["flags"]), payload)

    return "json-b64", _enc, _dec


def get_codec(preference: str) -> Tuple[str, Encoder, Decoder]:
    pref = preference.lower()
    if pref == "p2p":
        return _get_p2p_codec()
    if pref == "msgspec":
        return _get_msgspec_codec()
    if pref == "cbor2":
        return _get_cbor2_codec()
    if pref == "json":
        return _get_json_codec()
    # auto: try p2p → msgspec → cbor2 → json
    try:
        return _get_p2p_codec()
    except Exception:
        pass
    try:
        return _get_msgspec_codec()
    except Exception:
        pass
    try:
        return _get_cbor2_codec()
    except Exception:
        pass
    return _get_json_codec()


# --------------------------------------------------------------------------- #
# Bench core
# --------------------------------------------------------------------------- #

def _encode_all(frames: List[Frame], enc: Encoder) -> Tuple[float, List[bytes], int]:
    t0 = time.perf_counter()
    out: List[bytes] = []
    total = 0
    for fr in frames:
        b = enc(fr)
        out.append(b)
        total += len(b)
    dt = time.perf_counter() - t0
    return dt, out, total


def _decode_all(blobs: List[bytes], dec: Decoder) -> Tuple[float, int]:
    t0 = time.perf_counter()
    cnt = 0
    for b in blobs:
        _ = dec(b)
        cnt += 1
    dt = time.perf_counter() - t0
    return dt, cnt


def percentiles(samples: List[float]) -> Tuple[float, float]:
    if not samples:
        return 0.0, 0.0
    samples_sorted = sorted(samples)
    mid = len(samples_sorted) // 2
    if len(samples_sorted) % 2:
        median = samples_sorted[mid]
    else:
        median = 0.5 * (samples_sorted[mid - 1] + samples_sorted[mid])
    # coarse p90
    idx90 = max(0, min(len(samples_sorted) - 1, int(round(0.90 * (len(samples_sorted) - 1)))))
    p90 = samples_sorted[idx90]
    return median, p90


def run_bench(
    frames: int,
    payload_size: int,
    seed: Optional[int],
    warmup: int,
    repeat: int,
    codec_pref: str,
) -> dict:
    label, enc, dec = get_codec(codec_pref)
    dataset = gen_frames(frames, payload_size, seed)

    # Warmup
    for _ in range(max(0, warmup)):
        _, blobs, _ = _encode_all(dataset, enc)
        _decode_all(blobs, dec)

    enc_times: List[float] = []
    dec_times: List[float] = []
    byte_totals: List[int] = []

    for _ in range(repeat):
        enc_dt, blobs, total_bytes = _encode_all(dataset, enc)
        dec_dt, _ = _decode_all(blobs, dec)
        enc_times.append(enc_dt)
        dec_times.append(dec_dt)
        byte_totals.append(total_bytes)

    enc_med, enc_p90 = percentiles(enc_times)
    dec_med, dec_p90 = percentiles(dec_times)

    bytes_med = int(sorted(byte_totals)[len(byte_totals) // 2])
    frames_per_s_enc = frames / enc_med if enc_med > 0 else float("inf")
    frames_per_s_dec = frames / dec_med if dec_med > 0 else float("inf")
    mib = 1024.0 * 1024.0
    mibps_enc = (bytes_med / mib) / enc_med if enc_med > 0 else float("inf")
    mibps_dec = (bytes_med / mib) / dec_med if dec_med > 0 else float("inf")

    return {
        "case": "p2p.encode_decode",
        "params": {
            "frames": frames,
            "payload_size": payload_size,
            "codec": label,
            "seed": seed if seed is not None else int(os.environ.get("PYTHONHASHSEED", "0") or "1337"),
            "warmup": warmup,
            "repeat": repeat,
        },
        "result": {
            "bytes_total_median": bytes_med,
            "encode_median_s": enc_med,
            "encode_p90_s": enc_p90,
            "decode_median_s": dec_med,
            "decode_p90_s": dec_p90,
            "encode_frames_per_s": frames_per_s_enc,
            "decode_frames_per_s": frames_per_s_dec,
            "encode_mib_per_s": mibps_enc,
            "decode_mib_per_s": mibps_dec,
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Wire frames encode/decode throughput benchmark.")
    ap.add_argument("--frames", type=int, default=100_000, help="Number of frames per run (default: 100k)")
    ap.add_argument("--payload", type=int, default=64, help="Payload size (bytes) per frame (default: 64)")
    ap.add_argument("--seed", type=int, default=None, help="Deterministic frame generator seed")
    ap.add_argument("--warmup", type=int, default=1, help="Warmup iterations (default: 1)")
    ap.add_argument("--repeat", type=int, default=5, help="Measured iterations (default: 5)")
    ap.add_argument("--codec", choices=("auto", "p2p", "msgspec", "cbor2", "json"), default="auto",
                    help="Codec preference (default: auto)")
    args = ap.parse_args(argv)

    payload = run_bench(
        frames=args.frames,
        payload_size=args.payload,
        seed=args.seed,
        warmup=args.warmup,
        repeat=args.repeat,
        codec_pref=args.codec,
    )
    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
