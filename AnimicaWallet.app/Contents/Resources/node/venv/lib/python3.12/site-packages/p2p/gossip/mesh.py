from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, MutableMapping, Optional, Set, Tuple

from .topics import Topic, is_valid_topic, topic_id_from_path

# -----------------------------------------------------------------------------
# Parameters
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class MeshParams:
    """
    Tunables for the GossipSub-like mesh. Defaults aim to be conservative and
    test-friendly. See comments for guidance.
    """
    D: int = 6                 # Target mesh degree per topic
    D_low: int = 4             # If under this, GRAFT more peers
    D_high: int = 12           # If above this, PRUNE down to D
    D_out: int = 2             # Opportunistic outbound graft (poor mesh refresh)
    gossip_factor: float = 0.25  # % of non-mesh peers to gossip IHAVE to
    heartbeat_interval_s: float = 1.0  # Heartbeat tick cadence
    prune_backoff_s: float = 60.0      # Do not re-graft a pruned peer until backoff elapses
    opportunistic_graft_ticks: int = 15  # Every N ticks consider opportunistic graft
    opportunistic_graft_threshold: float = 0.0  # If mesh median score <= threshold → graft
    fanout_ttl_s: float = 60.0         # Fanout set lifetime for publish-without-subscribe
    seen_cache_cap: int = 8192         # Track recently seen msg ids (per mesh)
    max_ihave_length: int = 64         # Max msg ids to gossip in a single IHAVE


# -----------------------------------------------------------------------------
# Seen cache (LRU-ish with O(1) membership)
# -----------------------------------------------------------------------------

class SeenCache:
    def __init__(self, cap: int) -> None:
        self._cap = int(max(64, cap))
        self._deque: deque[int] = deque(maxlen=self._cap)
        self._set: Set[int] = set()

    def add(self, mid: int) -> None:
        if mid in self._set:
            return
        if len(self._deque) == self._deque.maxlen:
            old = self._deque.popleft()
            self._set.discard(old)
        self._deque.append(mid)
        self._set.add(mid)

    def __contains__(self, mid: int) -> bool:
        return mid in self._set


# -----------------------------------------------------------------------------
# Peer & topic state
# -----------------------------------------------------------------------------

@dataclass
class PeerInfo:
    peer_id: str
    score: float = 0.0
    subscribed: Set[str] = field(default_factory=set)  # topic paths
    last_seen: float = field(default_factory=lambda: time.time())
    # Delivery stats (very simple scoring inputs)
    deliveries: int = 0
    rejects: int = 0

    def touch(self) -> None:
        self.last_seen = time.time()


@dataclass
class TopicState:
    mesh: Set[str] = field(default_factory=set)  # peer_ids in mesh
    backoff_until: Dict[str, float] = field(default_factory=dict)  # peer_id -> ts
    fanout: Set[str] = field(default_factory=set)  # peers used for publish when not subscribed
    fanout_expire_at: float = 0.0
    seen: SeenCache = field(default_factory=lambda: SeenCache(cap=8192))

    def in_backoff(self, peer_id: str, now: float) -> bool:
        ts = self.backoff_until.get(peer_id)
        return ts is not None and ts > now

    def set_backoff(self, peer_id: str, until: float) -> None:
        self.backoff_until[peer_id] = until

    def clear_expired_backoff(self, now: float) -> None:
        expired = [p for p, t in self.backoff_until.items() if t <= now]
        for p in expired:
            self.backoff_until.pop(p, None)


# -----------------------------------------------------------------------------
# Control-plane results
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ControlCommand:
    kind: str  # "GRAFT" | "PRUNE"
    topic: str
    peer_id: str
    reason: Optional[str] = None


@dataclass
class HeartbeatResult:
    grafts: List[ControlCommand] = field(default_factory=list)
    prunes: List[ControlCommand] = field(default_factory=list)

    def extend(self, other: "HeartbeatResult") -> None:
        self.grafts.extend(other.grafts)
        self.prunes.extend(other.prunes)


# -----------------------------------------------------------------------------
# Gossip mesh core
# -----------------------------------------------------------------------------

class GossipMesh:
    """
    A lightweight, test-friendly GossipSub-ish mesh manager. It does not do
    network I/O; instead it returns control commands (GRAFT/PRUNE) and lists
    of peers to gossip to. A higher-level engine integrates this with the
    transport and wire formats.
    """

    def __init__(self, params: Optional[MeshParams] = None) -> None:
        self.params = params or MeshParams()
        self._peers: Dict[str, PeerInfo] = {}
        self._topics: Dict[str, TopicState] = {}
        self._tick: int = 0
        self._rng = random.Random(0xA11M1CA)  # deterministic by default

    # ---- Peer lifecycle -----------------------------------------------------

    def add_peer(self, peer_id: str, initial_score: float = 0.0) -> None:
        if peer_id not in self._peers:
            self._peers[peer_id] = PeerInfo(peer_id=peer_id, score=initial_score)

    def remove_peer(self, peer_id: str) -> None:
        if peer_id in self._peers:
            del self._peers[peer_id]
        for ts in self._topics.values():
            ts.mesh.discard(peer_id)
            ts.fanout.discard(peer_id)
            ts.backoff_until.pop(peer_id, None)

    def set_score(self, peer_id: str, score: float) -> None:
        self._require_peer(peer_id).score = score

    def bump_score(self, peer_id: str, delta: float) -> None:
        p = self._require_peer(peer_id)
        p.score += delta

    # ---- Subscriptions ------------------------------------------------------

    def on_peer_subscribe(self, peer_id: str, topic_path: str) -> None:
        self._require_peer(peer_id).subscribed.add(topic_path)

    def on_peer_unsubscribe(self, peer_id: str, topic_path: str) -> None:
        self._require_peer(peer_id).subscribed.discard(topic_path)
        ts = self._topics.get(topic_path)
        if ts:
            ts.mesh.discard(peer_id)
            ts.fanout.discard(peer_id)

    # ---- Local participation ------------------------------------------------

    def join(self, topic_path: str, now: Optional[float] = None) -> List[ControlCommand]:
        """
        Local node subscribes to a topic: create mesh and graft up to D peers.
        """
        self._require_topic(topic_path)
        return self._ensure_degree(topic_path, now or time.time())

    def leave(self, topic_path: str, reason: str = "leave") -> List[ControlCommand]:
        """
        Local node unsubscribes: prune everyone in the mesh.
        """
        ts = self._topics.get(topic_path)
        if not ts:
            return []
        cmds: List[ControlCommand] = []
        for pid in list(ts.mesh):
            ts.mesh.discard(pid)
            until = time.time() + self.params.prune_backoff_s
            ts.set_backoff(pid, until)
            cmds.append(ControlCommand("PRUNE", topic_path, pid, reason=reason))
        ts.fanout.clear()
        ts.fanout_expire_at = 0.0
        return cmds

    # ---- Publishing / gossip selection -------------------------------------

    def publish_select(self, topic_path: str, *, include_mesh: bool = True) -> Tuple[Set[str], Set[str]]:
        """
        Returns (eager, lazy) peer sets:
          - eager: peers in the mesh (or fanout) for full message push
          - lazy : peers to send IHAVE for gossip (subset of non-mesh subscribers)
        """
        now = time.time()
        ts = self._require_topic(topic_path)
        subs = self._subscribers(topic_path)

        # If we're not subscribed locally, use fanout peers
        if include_mesh and len(ts.mesh) > 0:
            eager = set(ts.mesh)
        else:
            # Refresh / build fanout
            if now >= ts.fanout_expire_at:
                candidates = [p for p in subs if p not in ts.mesh]
                eager = self._pick_best(topic_path, candidates, self.params.D)
                ts.fanout = set(eager)
                ts.fanout_expire_at = now + self.params.fanout_ttl_s
            else:
                eager = set(ts.fanout)

        # Lazy gossip to a fraction of non-eager subscribers
        not_eager = [p for p in subs if p not in eager]
        k = max(0, int(self.params.gossip_factor * len(not_eager)))
        lazy: Set[str] = set(self._rng.sample(not_eager, k)) if k else set()

        return eager, lazy

    # ---- Message accounting / scoring --------------------------------------

    def on_message_delivery(self, from_peer: str, topic_path: str, msg_id: int, accepted: bool) -> None:
        """
        Track delivery & scoring inputs for peer scoring. Call this after the
        application-level validator runs.
        """
        ts = self._require_topic(topic_path)
        if msg_id in ts.seen:
            # Duplicate; small penalty to discourage spamming duplicates.
            self.bump_score(from_peer, -0.05)
            return
        ts.seen.add(msg_id)
        p = self._require_peer(from_peer)
        if accepted:
            p.deliveries += 1
            self.bump_score(from_peer, +0.1)
        else:
            p.rejects += 1
            self.bump_score(from_peer, -0.2)

    # ---- Heartbeat: graft/prune/backoff maintenance -------------------------

    def heartbeat(self, now: Optional[float] = None) -> HeartbeatResult:
        """
        Periodic maintenance: keep mesh degrees in bounds, handle backoff,
        opportunistic graft, and fanout expiry.
        """
        now = now or time.time()
        self._tick += 1
        result = HeartbeatResult()

        for topic_path, ts in self._topics.items():
            ts.clear_expired_backoff(now)
            # Size corrections
            if len(ts.mesh) < self.params.D_low:
                result.grafts.extend(self._graft_more(topic_path, now, self.params.D - len(ts.mesh)))
            elif len(ts.mesh) > self.params.D_high:
                # Prune lowest-scoring peers down to D
                extra = len(ts.mesh) - self.params.D
                to_prune = self._lowest_scoring(list(ts.mesh), extra)
                for pid in to_prune:
                    ts.mesh.discard(pid)
                    ts.set_backoff(pid, now + self.params.prune_backoff_s)
                    result.prunes.append(ControlCommand("PRUNE", topic_path, pid, reason="over-degree"))

            # Opportunistic graft: if mesh looks weak, graft a few good outsiders
            if self._tick % self.params.opportunistic_graft_ticks == 0 and len(ts.mesh) > 0:
                median = self._median_score(ts.mesh)
                if median <= self.params.opportunistic_graft_threshold:
                    outsiders = [p for p in self._subscribers(topic_path)
                                 if p not in ts.mesh and not ts.in_backoff(p, now)]
                    # Prefer above-median peers
                    above = [p for p in outsiders if self._peers[p].score > median]
                    add = self._pick_best(topic_path, above if above else outsiders, self.params.D_out)
                    for pid in add:
                        ts.mesh.add(pid)
                        result.grafts.append(ControlCommand("GRAFT", topic_path, pid, reason="opportunistic"))

            # Fanout expiry is handled lazily in publish_select()

        return result

    # ---- Utilities ----------------------------------------------------------

    def _require_peer(self, peer_id: str) -> PeerInfo:
        p = self._peers.get(peer_id)
        if not p:
            raise KeyError(f"unknown peer {peer_id!r}")
        return p

    def _require_topic(self, topic_path: str) -> TopicState:
        if not is_valid_topic(topic_path):
            raise ValueError(f"invalid topic path {topic_path!r}")
        ts = self._topics.get(topic_path)
        if not ts:
            ts = TopicState(seen=SeenCache(self.params.seen_cache_cap))
            self._topics[topic_path] = ts
        return ts

    def _subscribers(self, topic_path: str) -> List[str]:
        # All peers that are subscribed to this topic.
        return [pid for pid, p in self._peers.items() if topic_path in p.subscribed]

    def _ensure_degree(self, topic_path: str, now: float) -> List[ControlCommand]:
        ts = self._require_topic(topic_path)
        need = max(0, self.params.D - len(ts.mesh))
        if need == 0:
            return []
        candidates = [p for p in self._subscribers(topic_path) if p not in ts.mesh and not ts.in_backoff(p, now)]
        chosen = self._pick_best(topic_path, candidates, need)
        cmds: List[ControlCommand] = []
        for pid in chosen:
            ts.mesh.add(pid)
            cmds.append(ControlCommand("GRAFT", topic_path, pid, reason="join/ensure-degree"))
        return cmds

    def _pick_best(self, topic_path: str, candidate_peers: Iterable[str], k: int) -> List[str]:
        # Deterministic selection by (score desc, peer_id asc), then take top-k
        scored = [(self._peers[p].score, p) for p in candidate_peers if p in self._peers]
        scored.sort(key=lambda t: (-t[0], t[1]))
        top = [p for _, p in scored[:k]]
        # Shuffle slightly but deterministically among equal scores to avoid lockstep meshes.
        # We do this by grouping equal-score peers and shuffling within groups using a seeded RNG.
        i = 0
        out: List[str] = []
        while i < len(top):
            j = i + 1
            while j < len(top) and self._peers[top[j]].score == self._peers[top[i]].score:
                j += 1
            group = top[i:j]
            self._rng.shuffle(group)
            out.extend(group)
            i = j
        return out[:k]

    def _lowest_scoring(self, peer_ids: List[str], count: int) -> List[str]:
        peer_ids.sort(key=lambda pid: (self._peers.get(pid, PeerInfo(pid)).score, pid))
        return peer_ids[:max(0, count)]

    def _median_score(self, peer_ids: Iterable[str]) -> float:
        arr = sorted(self._peers[p].score for p in peer_ids if p in self._peers)
        if not arr:
            return 0.0
        n = len(arr)
        if n % 2 == 1:
            return arr[n // 2]
        return 0.5 * (arr[n // 2 - 1] + arr[n // 2])

    # ---- Introspection ------------------------------------------------------

    def mesh_view(self) -> Dict[str, List[Tuple[str, float]]]:
        """
        Returns {topic_path: [(peer_id, score), ...]} useful for tests and metrics.
        """
        view: Dict[str, List[Tuple[str, float]]] = {}
        for t, ts in self._topics.items():
            view[t] = sorted(((pid, self._peers.get(pid, PeerInfo(pid)).score) for pid in ts.mesh),
                             key=lambda x: (-x[1], x[0]))
        return view

    def topic_stats(self, topic_path: str) -> Dict[str, int]:
        ts = self._topics.get(topic_path)
        if not ts:
            return {"mesh": 0, "fanout": 0, "backoff": 0}
        now = time.time()
        backoff_live = sum(1 for _, until in ts.backoff_until.items() if until > now)
        return {"mesh": len(ts.mesh), "fanout": len(ts.fanout), "backoff": backoff_live}


# -----------------------------------------------------------------------------
# Helpers for engines (optional)
# -----------------------------------------------------------------------------

def compute_msg_id(topic: str, payload_hash32: bytes) -> int:
    """
    Deterministic 64-bit message id from topic path and a 32-byte content hash.
    Engines typically call this with SHA3-256(payload)[:32].
    """
    if len(payload_hash32) < 8:
        raise ValueError("payload_hash32 must be >= 8 bytes")
    # Mix in the topic id to avoid cross-topic collisions
    tid = topic_id_from_path(topic).to_bytes(8, "big")
    mid = bytes(a ^ b for a, b in zip(tid, payload_hash32[:8]))
    return int.from_bytes(mid, "big")


__all__ = [
    "MeshParams",
    "GossipMesh",
    "ControlCommand",
    "HeartbeatResult",
    "SeenCache",
    "compute_msg_id",
]
