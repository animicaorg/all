"""
Randomness P2P gossip: commitments & reveals.

This module wires the randomness commit–reveal protocol into a generic P2P mesh:
- Publishes local Commit/Reveal messages onto canonical topics.
- Subscribes to those topics, performs light validation, and deduplicates.
- Hands accepted messages to user-provided sinks (e.g., stores or round managers).

Design notes
------------
* Lightweight validation only:
  - Shape & length checks (round id, hex prefixes, sizes).
  - Optional window checks via a RoundChecker (commit/reveal windows).
  - Optional recomputation of the commitment for Reveal (if builder is available).

* Dedupe:
  - LRU + TTL cache keyed by commitment hash (commits) and (round, addr) for reveals.
  - Prevents re-processing and re-broadcast storms.

* Encoding:
  - CBOR via `msgspec` if available, else `cbor2` if available, else JSON.
  - All binary fields are transmitted as 0x-prefixed hex to stay codec-agnostic.

Integrations
------------
Provide a `Mesh` and sinks:

    from randomness.adapters.p2p_gossip import RandomnessGossip, Mesh
    from randomness.commit_reveal.round_manager import RoundManager   # optional

    class MyMesh(Mesh): ...
    class MySinks:
        async def on_commit(self, msg: CommitMsg) -> None: ...
        async def on_reveal(self, msg: RevealMsg) -> None: ...

    mesh = MyMesh(...)
    sinks = MySinks()
    rg = RandomnessGossip(mesh=mesh, sinks=sinks, round_checker=my_round_manager)
    await rg.start()

    # Locally announce:
    await rg.announce_commit(round_id=42, addr=b"\x01"*32, commitment=b"\xAA"*32)
    await rg.announce_reveal(round_id=42, addr=b"\x01"*32, salt=b"\x02"*32, payload=b"\x03"*32)

"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, asdict
from typing import Any, Awaitable, Callable, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)

# ---- Topics ----
TOPIC_COMMIT = "animica/rand/commit/1"
TOPIC_REVEAL = "animica/rand/reveal/1"

# ---- Optional metrics (no-op if prometheus_client absent) ----
try:  # pragma: no cover - optional
    from prometheus_client import Counter

    GOSSIP_SEEN = Counter("rand_gossip_seen_total", "Incoming gossip frames", ["topic", "result"])
    GOSSIP_PUBLISHED = Counter("rand_gossip_published_total", "Outgoing gossip frames", ["topic"])
except Exception:  # pragma: no cover - fallback

    class _Noop:
        def labels(self, *_, **__):  # type: ignore[no-redef]
            return self

        def inc(self, *_: Any, **__: Any) -> None:
            pass

    GOSSIP_SEEN = _Noop()
    GOSSIP_PUBLISHED = _Noop()


# ---- Encoding (msgspec -> cbor2 -> json) ----
_encoder = _decoder = None

def _setup_codec() -> None:
    global _encoder, _decoder
    try:  # msgspec CBOR (fast)
        import msgspec  # type: ignore

        _encoder = lambda obj: msgspec.encode(obj, type=Any, enc_hook=None, use_annotated=False)  # type: ignore[assignment]
        _decoder = lambda b: msgspec.decode(b, type=Any)  # type: ignore[assignment]
        return
    except Exception:
        pass
    try:  # cbor2
        import cbor2  # type: ignore

        _encoder = lambda obj: cbor2.dumps(obj)  # type: ignore[assignment]
        _decoder = lambda b: cbor2.loads(b)  # type: ignore[assignment]
        return
    except Exception:
        pass

    import json

    _encoder = lambda obj: json.dumps(obj, separators=(",", ":")).encode("utf-8")  # type: ignore[assignment]
    _decoder = lambda b: json.loads(b.decode("utf-8"))  # type: ignore[assignment]


_setup_codec()


# ---- Utility: hex <-> bytes ----
def _b2h(b: bytes) -> str:
    return "0x" + b.hex()

def _h2b(h: str) -> bytes:
    if not isinstance(h, str) or not h.startswith("0x"):
        raise ValueError("expected 0x-prefixed hex string")
    return bytes.fromhex(h[2:])


# ---- Message types ----
@dataclass
class CommitMsg:
    type: str  # "commit"
    v: int     # version
    round: int
    addr: str          # 0x-hex (address bytes as produced by identity layer)
    commitment: str    # 0x-hex (32 bytes)
    ts: int            # unix seconds (sender clock; non-consensus)

@dataclass
class RevealMsg:
    type: str  # "reveal"
    v: int     # version
    round: int
    addr: str          # 0x-hex
    salt: str          # 0x-hex
    payload: str       # 0x-hex
    commitment: str    # 0x-hex (should match H(domain|addr|salt|payload))
    ts: int            # unix seconds (sender clock; non-consensus)


# ---- Protocols for external deps ----
class Mesh(Protocol):
    async def publish(self, topic: str, data: bytes) -> None: ...
    def subscribe(self, topic: str, handler: Callable[[bytes, str], Awaitable[None]]) -> None: ...


class RoundChecker(Protocol):
    def is_commit_open(self, round_id: int) -> bool: ...
    def is_reveal_open(self, round_id: int) -> bool: ...


class Sinks(Protocol):
    async def on_commit(self, msg: CommitMsg) -> None: ...
    async def on_reveal(self, msg: RevealMsg) -> None: ...


# ---- Dedupe TTL-LRU ----
class _TTLSet:
    def __init__(self, maxsize: int = 8192, ttl_sec: int = 300) -> None:
        self._max = maxsize
        self._ttl = ttl_sec
        self._data: dict[str, float] = {}
        self._order: list[str] = []
        self._lock = asyncio.Lock()

    async def add_if_new(self, key: str) -> bool:
        now = time.time()
        async with self._lock:
            # purge expired
            cutoff = now - self._ttl
            if len(self._order) > 0:
                # Fast-incremental purge from front
                i = 0
                for k in list(self._order):
                    if self._data.get(k, 0) >= cutoff:
                        break
                    self._data.pop(k, None)
                    i += 1
                if i:
                    del self._order[:i]
            if key in self._data:
                return False
            # insert
            self._data[key] = now
            self._order.append(key)
            # evict LRU if needed
            while len(self._order) > self._max:
                k = self._order.pop(0)
                self._data.pop(k, None)
            return True


# ---- Optional verification import ----
_commit_func = None
try:  # pragma: no cover - import glue
    from randomness.commit_reveal.commit import build_commitment as _commit_func  # type: ignore[attr-defined]
except Exception:
    try:
        from randomness.commit_reveal.commit import commit as _commit_func  # type: ignore[attr-defined]
    except Exception:
        _commit_func = None  # fallback to domain-naive hash


def _recompute_commitment(addr_b: bytes, salt_b: bytes, payload_b: bytes) -> bytes:
    """
    Try to recompute commitment using the provided builder if available; else
    fall back to a conservative SHA3-256 transcript (domain-agnostic).
    """
    if _commit_func is not None:
        try:
            c = _commit_func(addr_b, salt_b, payload_b)  # type: ignore[misc]
            if isinstance(c, bytes):
                return c
            if isinstance(c, str) and c.startswith("0x"):
                return _h2b(c)
            if isinstance(c, str):
                return bytes.fromhex(c)
        except Exception:
            logger.debug("commit builder failed; falling back", exc_info=True)
    # Fallback: SHA3-256(addr||salt||payload) (NOTE: domain separation may differ!)
    h = hashlib.sha3_256()
    h.update(addr_b)
    h.update(salt_b)
    h.update(payload_b)
    return h.digest()


# ---- Validator ----
class _Validator:
    def __init__(self, round_checker: Optional[RoundChecker] = None) -> None:
        self.round_checker = round_checker

    def _check_hex_len(self, label: str, hx: str, expect_len: Optional[int] = None) -> None:
        if not isinstance(hx, str) or not hx.startswith("0x"):
            raise ValueError(f"{label}: expected 0x-hex string")
        raw_len = (len(hx) - 2) // 2
        if expect_len is not None and raw_len != expect_len:
            raise ValueError(f"{label}: expected {expect_len} bytes, got {raw_len}")

    def validate_commit(self, msg: CommitMsg) -> None:
        if msg.v != 1:
            raise ValueError("unsupported commit message version")
        if msg.round < 0:
            raise ValueError("round must be non-negative")
        self._check_hex_len("addr", msg.addr)  # address size is chain-defined
        self._check_hex_len("commitment", msg.commitment, 32)
        if self.round_checker and not self.round_checker.is_commit_open(msg.round):
            # Only *warn* (gossip may carry late/early messages across mesh)
            logger.debug("commit for closed window (round=%s)", msg.round)

    def validate_reveal(self, msg: RevealMsg) -> None:
        if msg.v != 1:
            raise ValueError("unsupported reveal message version")
        if msg.round < 0:
            raise ValueError("round must be non-negative")
        self._check_hex_len("addr", msg.addr)
        self._check_hex_len("salt", msg.salt)
        self._check_hex_len("payload", msg.payload)
        self._check_hex_len("commitment", msg.commitment, 32)
        # Optional window check
        if self.round_checker and not self.round_checker.is_reveal_open(msg.round):
            logger.debug("reveal for closed window (round=%s)", msg.round)
        # Lightweight link: recompute commitment and compare
        addr_b, salt_b, payload_b = _h2b(msg.addr), _h2b(msg.salt), _h2b(msg.payload)
        expect_c = _recompute_commitment(addr_b, salt_b, payload_b)
        got_c = _h2b(msg.commitment)
        if expect_c != got_c:
            raise ValueError("reveal does not match commitment")


# ---- Main gossip adapter ----
class RandomnessGossip:
    """
    P2P gossip adapter for randomness commit–reveal messages.

    Parameters
    ----------
    mesh: Mesh
        Transport implementing publish/subscribe hooks.
    sinks: Sinks
        Async callbacks for accepted Commit/Reveal messages.
    round_checker: Optional[RoundChecker]
        Provides window checks (soft validation).
    dedupe_size: int
        Max entries retained in dedupe caches.
    dedupe_ttl_sec: int
        TTL for dedupe entries in seconds.
    """

    def __init__(
        self,
        *,
        mesh: Mesh,
        sinks: Sinks,
        round_checker: Optional[RoundChecker] = None,
        dedupe_size: int = 8192,
        dedupe_ttl_sec: int = 300,
    ) -> None:
        self._mesh = mesh
        self._sinks = sinks
        self._validator = _Validator(round_checker)
        self._seen_commits = _TTLSet(maxsize=dedupe_size, ttl_sec=dedupe_ttl_sec)
        self._seen_reveals = _TTLSet(maxsize=dedupe_size, ttl_sec=dedupe_ttl_sec)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._mesh.subscribe(TOPIC_COMMIT, self._handle_commit_frame)
        self._mesh.subscribe(TOPIC_REVEAL, self._handle_reveal_frame)
        self._started = True

    # ---- Local announce (publish) ----

    async def announce_commit(self, *, round_id: int, addr: bytes, commitment: bytes) -> None:
        msg = CommitMsg(
            type="commit",
            v=1,
            round=round_id,
            addr=_b2h(addr),
            commitment=_b2h(commitment),
            ts=int(time.time()),
        )
        await self._publish(TOPIC_COMMIT, msg)

    async def announce_reveal(
        self, *, round_id: int, addr: bytes, salt: bytes, payload: bytes
    ) -> None:
        commitment = _recompute_commitment(addr, salt, payload)
        msg = RevealMsg(
            type="reveal",
            v=1,
            round=round_id,
            addr=_b2h(addr),
            salt=_b2h(salt),
            payload=_b2h(payload),
            commitment=_b2h(commitment),
            ts=int(time.time()),
        )
        await self._publish(TOPIC_REVEAL, msg)

    async def _publish(self, topic: str, msg_obj: CommitMsg | RevealMsg) -> None:
        frame = _encoder(asdict(msg_obj))
        await self._mesh.publish(topic, frame)
        GOSSIP_PUBLISHED.labels(topic=topic).inc()

    # ---- Inbound handlers ----

    async def _handle_commit_frame(self, frame: bytes, peer_id: str) -> None:
        try:
            obj = _decoder(frame)
            msg = CommitMsg(**obj)  # type: ignore[arg-type]
            self._validator.validate_commit(msg)
            # Dedupe by commitment hash (unique)
            if not await self._seen_commits.add_if_new(msg.commitment):
                GOSSIP_SEEN.labels(topic=TOPIC_COMMIT, result="dupe").inc()
                return
            GOSSIP_SEEN.labels(topic=TOPIC_COMMIT, result="ok").inc()
            await self._sinks.on_commit(msg)
        except Exception as e:
            logger.debug("drop commit from peer %s: %s", peer_id, e)
            GOSSIP_SEEN.labels(topic=TOPIC_COMMIT, result="bad").inc()

    async def _handle_reveal_frame(self, frame: bytes, peer_id: str) -> None:
        try:
            obj = _decoder(frame)
            msg = RevealMsg(**obj)  # type: ignore[arg-type]
            self._validator.validate_reveal(msg)
            # Dedupe by (round, addr) — one reveal per address per round
            key = f"{msg.round}:{msg.addr.lower()}"
            if not await self._seen_reveals.add_if_new(key):
                GOSSIP_SEEN.labels(topic=TOPIC_REVEAL, result="dupe").inc()
                return
            GOSSIP_SEEN.labels(topic=TOPIC_REVEAL, result="ok").inc()
            await self._sinks.on_reveal(msg)
        except Exception as e:
            logger.debug("drop reveal from peer %s: %s", peer_id, e)
            GOSSIP_SEEN.labels(topic=TOPIC_REVEAL, result="bad").inc()


__all__ = [
    "TOPIC_COMMIT",
    "TOPIC_REVEAL",
    "CommitMsg",
    "RevealMsg",
    "Mesh",
    "RoundChecker",
    "Sinks",
    "RandomnessGossip",
]
