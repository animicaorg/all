from __future__ import annotations

"""
Gossip engine
=============

High-level subscribe/publish API on top of the mesh and fast pre-validators.

Responsibilities
- Track local subscriptions and peer meshes per topic.
- Fast ingress prefilter (size/CBOR top-level sniff) before decoding.
- Message de-duplication (per-topic LRU of msg_ids).
- Lightweight scoring (delivery, invalid, time-in-mesh).
- GRAFT/PRUNE helpers for handlers (policy-aware accept/deny).
- Fanout publishing to the current mesh with backoff-friendly hints.

This module is *transport-agnostic*: callers inject `send_gossip(peer, topic, payload)`
which the protocol/router layer binds to an encrypted stream writer.

Threading model: asyncio-only, single-threaded; internal locks guard maps.
"""

import asyncio
import hashlib
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import (Awaitable, Callable, Deque, Dict, Iterable, List, Optional,
                    Set, Tuple)

try:
    # Preferred canonical topic helpers
    from .topics import is_valid_topic as _is_valid_topic  # type: ignore
except Exception:  # pragma: no cover

    def _is_valid_topic(topic: str) -> bool:
        return 1 <= len(topic) <= 256 and all(32 <= ord(c) <= 126 for c in topic)


from .mesh import Mesh  # simple GossipSub-like mesh
from .validator import prefilter as fast_prefilter

# --------------------------------------------------------------------------------------
# Token bucket (tiny, allocation-free hot path)
# --------------------------------------------------------------------------------------


@dataclass
class TokenBucket:
    capacity: int
    refill_per_sec: float
    tokens: float = field(default=0.0)
    last: float = field(default_factory=time.monotonic)

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        dt = now - self.last
        self.last = now
        if dt > 0:
            self.tokens = min(self.capacity, self.tokens + dt * self.refill_per_sec)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreParams:
    first_delivery: float = 0.3
    deliver: float = 0.1
    invalid: float = -1.0
    mesh_time: float = 0.001  # per second while in mesh
    graft_penalty: float = -0.2
    prune_bonus: float = 0.05
    floor: float = -2.0
    ceil: float = 2.0
    decay: float = 0.0005  # per second decay toward zero


@dataclass
class PeerScore:
    score: float = 0.0
    in_mesh_since: Dict[str, float] = field(default_factory=dict)  # topic -> ts
    last_update: float = field(default_factory=time.monotonic)

    def _decay_to_now(self, params: ScoreParams) -> None:
        now = time.monotonic()
        dt = now - self.last_update
        if dt <= 0:
            return
        # Exponential-like linear decay toward 0
        if self.score > 0:
            self.score = max(0.0, self.score - params.decay * dt)
        elif self.score < 0:
            self.score = min(0.0, self.score + params.decay * dt)
        self.last_update = now

    def add(self, delta: float, params: ScoreParams) -> None:
        self._decay_to_now(params)
        self.score = max(params.floor, min(params.ceil, self.score + delta))

    def on_join_mesh(self, topic: str, params: ScoreParams) -> None:
        self.in_mesh_since[topic] = time.monotonic()

    def on_leave_mesh(self, topic: str, params: ScoreParams) -> None:
        self.in_mesh_since.pop(topic, None)

    def accrue_mesh_time(self, params: ScoreParams) -> None:
        # Called periodically to add small positives for stable mesh peers
        now = time.monotonic()
        for _topic, ts in list(self.in_mesh_since.items()):
            dt = max(0.0, now - ts)
            if dt > 0:
                self.add(params.mesh_time * dt, params)
                # reset anchor
                self.in_mesh_since[_topic] = now


# --------------------------------------------------------------------------------------
# Message table (dedupe)
# --------------------------------------------------------------------------------------


@dataclass
class SeenTable:
    """
    Per-topic LRU of recently seen msg_ids. O(1) membership & eviction.
    """

    capacity: int = 4096

    def __post_init__(self) -> None:
        self._lru: "OrderedDict[bytes, float]" = OrderedDict()

    def add(self, mid: bytes) -> bool:
        """
        Returns True if this is a new message (inserted), False if already seen.
        """
        if mid in self._lru:
            # move to end
            self._lru.move_to_end(mid, last=True)
            return False
        self._lru[mid] = time.monotonic()
        if len(self._lru) > self.capacity:
            self._lru.popitem(last=False)
        return True

    def contains(self, mid: bytes) -> bool:
        return mid in self._lru


# --------------------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------------------

MsgSender = Callable[[str, str, bytes], Awaitable[None]]
# signature: await send(peer_id, topic, payload)

OnMessage = Callable[[str, str, bytes], Awaitable[None]]
# signature: await on_message(peer_id, topic, payload) — after prefilter & dedupe


class GossipEngine:
    """
    The orchestrator: holds the local topic set, meshes, scoring state, and
    policy controls. All network I/O goes through injected `send`.
    """

    def __init__(
        self,
        mesh: Mesh,
        send: MsgSender,
        *,
        score_params: Optional[ScoreParams] = None,
        ingress_bucket_capacity: int = 200,
        ingress_refill_per_sec: float = 300.0,
        egress_bucket_capacity: int = 400,
        egress_refill_per_sec: float = 600.0,
        lru_capacity: int = 8192,
    ) -> None:
        self.mesh = mesh
        self._send = send
        self.params = score_params or ScoreParams()

        self._subs: Set[str] = set()
        self._seen_by_topic: Dict[str, SeenTable] = defaultdict(
            lambda: SeenTable(lru_capacity)
        )
        self._peer_scores: Dict[str, PeerScore] = defaultdict(PeerScore)
        self._ingress_buckets: Dict[Tuple[str, str], TokenBucket] = defaultdict(
            lambda: TokenBucket(ingress_bucket_capacity, ingress_refill_per_sec)
        )
        self._egress_buckets: Dict[Tuple[str, str], TokenBucket] = defaultdict(
            lambda: TokenBucket(egress_bucket_capacity, egress_refill_per_sec)
        )
        self._lock = asyncio.Lock()
        self._on_message: Optional[OnMessage] = None

        # Background accrual for mesh time
        self._task_accrue: Optional[asyncio.Task] = None

    # -------- lifecycle --------

    async def start(self) -> None:
        if self._task_accrue is None:
            self._task_accrue = asyncio.create_task(self._accrue_loop())

    async def stop(self) -> None:
        if self._task_accrue:
            self._task_accrue.cancel()
            try:
                await self._task_accrue
            except asyncio.CancelledError:
                pass
            self._task_accrue = None

    async def _accrue_loop(self) -> None:
        while True:
            await asyncio.sleep(5.0)
            for ps in list(self._peer_scores.values()):
                ps.accrue_mesh_time(self.params)

    # -------- callbacks --------

    def on_message(self, cb: OnMessage) -> None:
        """Register a coroutine called *after* prefilter+dedupe succeeded."""
        self._on_message = cb

    # -------- local subscribe/publish --------

    async def subscribe(self, topic: str) -> None:
        if not _is_valid_topic(topic):
            raise ValueError(f"invalid topic: {topic!r}")
        async with self._lock:
            if topic in self._subs:
                return
            self._subs.add(topic)
            # Ask mesh to (re)form with current peers (mesh handles fanout/backoff).
            await self.mesh.join(topic)

    async def unsubscribe(self, topic: str) -> None:
        async with self._lock:
            if topic not in self._subs:
                return
            self._subs.remove(topic)
            await self.mesh.leave(topic)

    async def publish(self, topic: str, payload: bytes) -> bytes:
        """
        Publish a message to the current mesh for `topic`.
        Returns the msg_id (sha3-256 digest).
        """
        if topic not in self._subs:
            raise RuntimeError(f"not subscribed to topic: {topic}")
        # Fast local prefilter (drop obviously-bad without network I/O)
        ok, reason = fast_prefilter(topic, payload)
        if not ok:
            raise ValueError(f"gossip prefilter reject: {reason}")

        mid = self._msg_id(topic, payload)
        if not self._seen_by_topic[topic].add(mid):
            # Already seen locally: treat as success but don't spam peers.
            return mid

        peers = await self.mesh.peers(topic)
        # best-effort send with per-peer egress throttling
        await asyncio.gather(*(self._safe_send(peer, topic, payload) for peer in peers))
        return mid

    # -------- ingress from network (protocol/router calls these) --------

    async def receive_gossip(self, from_peer: str, topic: str, payload: bytes) -> None:
        """
        Called by the protocol layer when a GOSSIP frame is received.
        Performs fast prefilter + dedupe, updates scores, forwards to other peers,
        and invokes on_message callback.
        """
        # Ingress token bucket (peer+topic)
        if not self._ingress_buckets[(from_peer, topic)].allow(1.0):
            # soft-drop without penalizing (up to router to log)
            return

        ok, reason = fast_prefilter(topic, payload)
        if not ok:
            # Penalize invalid sender
            self._peer_scores[from_peer].add(self.params.invalid, self.params)
            return

        mid = self._msg_id(topic, payload)
        if not self._seen_by_topic[topic].add(mid):
            # Duplicate from this peer (neither penalize nor reward)
            return

        # Reward delivery (first delivery gets a small bonus)
        self._peer_scores[from_peer].add(self.params.first_delivery, self.params)

        # Fanout to other peers in mesh (exclude source)
        peers = await self.mesh.peers(topic)
        tasks = []
        for p in peers:
            if p == from_peer:
                continue
            tasks.append(self._safe_send(p, topic, payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # App-level callback
        if self._on_message is not None:
            await self._on_message(from_peer, topic, payload)

    # -------- graft/prune hooks (protocol can call these) --------

    async def on_graft(self, from_peer: str, topic: str) -> bool:
        """
        Decide whether to accept a peer into our mesh for `topic`.
        Returns True (accept) / False (deny + PRUNE should be sent by caller).
        """
        # Basic policy: allow if we are subscribed and mesh size under target.
        if topic not in self._subs:
            # not a member; politely deny
            self._peer_scores[from_peer].add(self.params.graft_penalty, self.params)
            return False

        size = await self.mesh.size(topic)
        target = await self.mesh.target(topic)
        if size >= target:
            # mesh at capacity: deny; small penalty to discourage greedy grafts
            self._peer_scores[from_peer].add(self.params.graft_penalty, self.params)
            return False

        await self.mesh.add_peer(topic, from_peer)
        self._peer_scores[from_peer].on_join_mesh(topic, self.params)
        return True

    async def on_prune(self, from_peer: str, topic: str) -> None:
        """Peer is pruning us from its mesh; reciprocate and grant a tiny bonus for explicit flow-control."""
        await self.mesh.remove_peer(topic, from_peer)
        self._peer_scores[from_peer].on_leave_mesh(topic, self.params)
        self._peer_scores[from_peer].add(self.params.prune_bonus, self.params)

    # -------- utilities --------

    def _msg_id(self, topic: str, payload: bytes) -> bytes:
        h = hashlib.sha3_256()
        h.update(topic.encode("utf-8"))
        h.update(b"|")
        h.update(payload)
        return h.digest()

    async def _safe_send(self, peer_id: str, topic: str, payload: bytes) -> None:
        if not self._egress_buckets[(peer_id, topic)].allow(1.0):
            return
        try:
            await self._send(peer_id, topic, payload)
            # Successful delivery earns a tiny score
            self._peer_scores[peer_id].add(self.params.deliver, self.params)
        except Exception:
            # Do not penalize harshly on transport errors; the transport handles backoff.
            pass

    # -------- inspection --------

    def peer_score(self, peer_id: str) -> float:
        return self._peer_scores[peer_id].score

    async def mesh_peers(self, topic: str) -> List[str]:
        return list(await self.mesh.peers(topic))


__all__ = [
    "GossipEngine",
    "ScoreParams",
    "PeerScore",
    "TokenBucket",
]
