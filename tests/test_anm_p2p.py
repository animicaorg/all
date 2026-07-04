"""
ANM-C09/C10/H03/H04 — P2P DoS caps + peer hardening.

Covers:
  * ANM-H03  bounded decode: unpack_frame() must reject zlib decompression
             bombs (small compressed body inflating past the cap) and oversized
             declared plain_len — WITHOUT breaking legitimate compressed frames.
  * ANM-H04  per-peer inbound message-rate limiting: P2PService._inbound_rate_check
             sheds momentary over-budget messages and disconnects sustained
             floods, while never throttling legitimate / exempt peers and staying
             a no-op when disabled (backward-compatible default behavior).

Run:
  export PYTHONPATH=/root/animica-sec-6.0.0/python:/root/animica-sec-6.0.0
  /root/animica/.venv/bin/python -m pytest tests/test_anm_p2p.py -v
"""

from __future__ import annotations

import asyncio
import struct
import zlib

import pytest

from p2p.wire import frames
from p2p.wire.frames import (
    DEFAULT_MAX_PLAINTEXT_BYTES,
    Framer,
    FrameFlags,
    pack_frame,
    unpack_frame,
)


# ─────────────────────────────────────────────────────────────────────────────
# ANM-H03 — bounded decode / anti-decompression-bomb
# ─────────────────────────────────────────────────────────────────────────────


def _hand_frame(*, flags: int, plain_len: int, body: bytes) -> bytes:
    """Build a raw frame with attacker-chosen header fields (bad csum ok)."""
    header = struct.pack(
        frames.HEADER_FMT,
        frames.MAGIC,
        frames.VERSION,
        int(flags),
        1,  # msg_id
        0,  # seq
        0,  # nonce
        int(plain_len),
        len(body),  # wire_len must match actual body length
        b"\x00" * 8,  # checksum (unchecked — we blow up before reaching it)
    )
    return header + body


def test_default_plaintext_cap_is_sane():
    # 16 MiB by default: above the 8 MiB logical/frame ceiling, below any bomb.
    assert DEFAULT_MAX_PLAINTEXT_BYTES == 16 * 1024 * 1024


def test_decompression_bomb_with_honest_plain_len_rejected():
    # Attacker declares the true (huge) size; early plain_len check trips.
    huge = 20 * 1024 * 1024
    body = zlib.compress(b"\x00" * huge)
    assert len(body) < 64 * 1024  # tiny on the wire, but inflates to 20 MiB
    framed = _hand_frame(
        flags=int(FrameFlags.COMPRESSED), plain_len=huge, body=body
    )
    with pytest.raises(ValueError):
        unpack_frame(framed, max_plaintext=1 * 1024 * 1024)
    # Also rejected under the production default cap (no explicit arg).
    with pytest.raises(ValueError):
        unpack_frame(framed)


def test_decompression_bomb_with_lying_plain_len_rejected():
    # Attacker LIES: claims a tiny plain_len so the early check passes, but the
    # body still inflates far past the cap. The bounded decompressor must catch
    # it instead of allocating gigabytes.
    huge = 20 * 1024 * 1024
    body = zlib.compress(b"\x00" * huge)
    framed = _hand_frame(
        flags=int(FrameFlags.COMPRESSED), plain_len=100, body=body
    )
    with pytest.raises(ValueError):
        unpack_frame(framed, max_plaintext=1 * 1024 * 1024)


def test_bounded_decompress_helper_never_over_allocates():
    huge = 20 * 1024 * 1024
    body = zlib.compress(b"\x00" * huge)
    with pytest.raises(ValueError):
        frames._bounded_decompress(body, 1 * 1024 * 1024)
    # A legitimate small stream inflates fine.
    small = zlib.compress(b"hello" * 100)
    assert frames._bounded_decompress(small, 1 * 1024 * 1024) == b"hello" * 100


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat: legitimate frames still round-trip
# ─────────────────────────────────────────────────────────────────────────────


def test_legit_compressed_frame_roundtrips():
    # Payload > compress_threshold(1024) and compressible → COMPRESSED flag set.
    payload = b"the quick brown fox " * 200
    framed = pack_frame(msg_id=7, seq=3, payload=payload)
    f = unpack_frame(framed)
    assert f.flags & FrameFlags.COMPRESSED
    assert f.payload == payload
    assert f.msg_id == 7 and f.seq == 3


def test_legit_small_frame_roundtrips():
    payload = b"ping"
    framed = pack_frame(msg_id=1, seq=0, payload=payload)
    f = unpack_frame(framed)
    assert f.payload == payload


def test_framer_roundtrip_unaffected():
    fr_tx = Framer(aead=None)
    fr_rx = Framer(aead=None)
    payload = b"animica" * 500
    framed = fr_tx.pack(42, payload)
    f = fr_rx.unpack(framed)
    assert f.payload == payload
    assert f.msg_id == 42


def test_custom_small_cap_still_allows_normal_payloads():
    payload = b"x" * 2048
    framed = pack_frame(msg_id=2, seq=1, payload=payload)
    # 8 MiB cap: normal payload well within bounds.
    f = unpack_frame(framed, max_plaintext=8 * 1024 * 1024)
    assert f.payload == payload


# ─────────────────────────────────────────────────────────────────────────────
# ANM-H04 — per-peer inbound message-rate limiting
# ─────────────────────────────────────────────────────────────────────────────

from p2p.node.p2p_service import P2PService, _PeerState  # noqa: E402


def _make_peer(remote: str = "203.0.113.7:31000") -> _PeerState:
    return _PeerState(
        session_id="sess-1",
        remote=remote,
        direction="inbound",
        conn=object(),
        stream=object(),
        framer=Framer(aead=None),
        write_lock=asyncio.Lock(),
    )


class _FakeService:
    """Minimal stand-in exposing exactly what _inbound_rate_check touches."""

    def __init__(self, **cfg):
        self._peer_msg_rate_enabled = cfg.get("enabled", True)
        # Near-zero refill so token accrual during a fast test is negligible.
        self._peer_msg_rate_per_s = cfg.get("rate", 0.001)
        self._peer_msg_rate_burst = cfg.get("burst", 3.0)
        self._peer_msg_rate_max_strikes = cfg.get("max_strikes", 4)
        self._exempt = cfg.get("exempt", False)
        self._docker = cfg.get("docker", False)

    def _extract_host(self, remote: str) -> str:
        return remote.split(":")[0]

    def _is_peer_exempt(self, key: str) -> bool:
        return self._exempt

    def _is_docker_local(self, host: str) -> bool:
        return self._docker


def _check(svc: _FakeService, peer: _PeerState) -> str:
    # Call the real method with our lightweight self.
    return P2PService._inbound_rate_check(svc, peer)


def test_rate_limiter_allows_burst_then_sheds_then_bans():
    svc = _FakeService(rate=0.001, burst=3.0, max_strikes=4)
    peer = _make_peer()
    # First `burst` messages are allowed (priming fills the bucket to burst,
    # then each allowed call consumes one token).
    assert _check(svc, peer) == "ok"  # tokens 3 -> 2
    assert _check(svc, peer) == "ok"  # 2 -> 1
    assert _check(svc, peer) == "ok"  # 1 -> 0
    # Now over budget: shed until strikes reach the ban threshold.
    assert _check(svc, peer) == "shed"  # strike 1
    assert _check(svc, peer) == "shed"  # strike 2
    assert _check(svc, peer) == "shed"  # strike 3
    assert _check(svc, peer) == "ban"   # strike 4 == max_strikes


def test_rate_limiter_disabled_is_noop():
    svc = _FakeService(enabled=False, burst=1.0, max_strikes=1)
    peer = _make_peer()
    # Even far beyond any budget, disabled limiter always allows.
    for _ in range(1000):
        assert _check(svc, peer) == "ok"


def test_rate_limiter_never_throttles_exempt_peer():
    svc = _FakeService(exempt=True, burst=1.0, max_strikes=1)
    peer = _make_peer()
    for _ in range(1000):
        assert _check(svc, peer) == "ok"


def test_rate_limiter_never_throttles_docker_local_peer():
    svc = _FakeService(docker=True, burst=1.0, max_strikes=1)
    peer = _make_peer(remote="172.17.0.5:31000")
    for _ in range(1000):
        assert _check(svc, peer) == "ok"


def test_rate_limiter_refill_restores_budget():
    # With a real (fast) refill rate, a peer that pauses regains tokens and is
    # not banned — proving a legitimate steady sender is unaffected.
    svc = _FakeService(rate=1000.0, burst=5.0, max_strikes=3)
    peer = _make_peer()
    # Drain the burst.
    for _ in range(5):
        assert _check(svc, peer) == "ok"
    # Let the bucket refill well past 1 token (5000/s * 0.02s = 100 tokens).
    import time as _t

    _t.sleep(0.02)
    assert _check(svc, peer) == "ok"


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
