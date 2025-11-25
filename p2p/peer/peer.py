from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, asdict
from enum import IntFlag, Enum
from typing import Dict, Optional, Set, Tuple


class PeerStatus(str, Enum):
    DIALING = "dialing"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    BANNED = "banned"


class PeerRole(IntFlag):
    """Bitflags describing peer roles/capabilities (can be combined)."""
    NONE = 0
    FULL = 1 << 0               # full node (validates/serves blocks)
    LIGHT = 1 << 1              # light client (headers/queries only)
    MINER = 1 << 2              # produces blocks / useful-work shares
    DA_ONLY = 1 << 3            # data-availability service focus
    PROVIDER_AI = 1 << 4        # AI compute provider (AICF)
    PROVIDER_QPU = 1 << 5       # Quantum provider (trap circuits)
    RELAY = 1 << 6              # high-fanout relay node


@dataclass
class ScoreParams:
    """
    Tunable weights for the peer scoring model. Keep this in-sync with p2p/gossip/engine.py
    and p2p/peer/ratelimit.py (topic caps & penalties).
    """
    # Base & time-decay
    base: float = 10.0
    decay_half_life_s: float = 120.0   # seconds to halve positive contributions

    # Latency (RTT) penalties (ms → score impact)
    rtt_ref_ms: float = 150.0          # reference RTT with zero penalty
    rtt_slope: float = 0.015           # score penalty per % over reference

    # Delivery & quality
    good_msg_weight: float = 0.002     # + score per valid gossip message delivered
    bad_msg_penalty: float = 0.2       # - score per invalid/ignored message
    dupe_penalty: float = 0.05         # - score for duplicate spam

    # Per-topic normalization cap (prevents single-topic gaming)
    topic_cap: float = 15.0

    # Explicit application penalties (DoS, rate, misbehavior)
    penalty_decay_half_life_s: float = 600.0
    ban_threshold: float = -10.0       # if total score dips below → move to BANNED

    # Uptime bonus (bounded)
    uptime_bonus_max: float = 20.0
    uptime_bonus_rate: float = 0.002   # per-second small bonus, bounded

    # Recent disconnect penalty (short-term instability)
    flap_penalty: float = 2.0
    flap_window_s: float = 300.0


@dataclass
class TopicScore:
    """Rolling per-topic counters used to compute quality contributions."""
    valid_msgs: int = 0
    invalid_msgs: int = 0
    duplicate_msgs: int = 0
    bytes_in: int = 0
    bytes_out: int = 0

    def quality_score(self, params: ScoreParams) -> float:
        s = (self.valid_msgs * params.good_msg_weight
             - self.invalid_msgs * params.bad_msg_penalty
             - self.duplicate_msgs * params.dupe_penalty)
        # Bound per topic to prevent gaming through a single hot topic.
        return max(-params.topic_cap, min(params.topic_cap, s))


@dataclass
class TokenBucket:
    """Simple per-topic token bucket used to throttle publish rate."""
    rate_per_s: float
    burst: float
    tokens: float = field(default=0.0)
    last_refill_s: float = field(default_factory=lambda: time.time())

    def allow(self, cost: float, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        elapsed = max(0.0, now - self.last_refill_s)
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate_per_s)
        self.last_refill_s = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


@dataclass
class Peer:
    """
    Runtime state for a connected (or known) peer.

    This object is intentionally self-contained (pure-Python, no asyncio),
    so it can live in routing and scoring paths without event-loop coupling.
    Transport/connection-specific details are carried externally and referenced
    here as opaque ids if needed.
    """
    # Identity & negotiated handshake
    peer_id: str
    address: str                                    # multiaddr-like string
    roles: PeerRole
    chain_id: int
    alg_policy_root: bytes                          # expected alg-policy root
    head_height: int = 0
    caps: Set[str] = field(default_factory=set)     # string capability flags

    # Lifecycle
    status: PeerStatus = PeerStatus.DIALING
    connected_at_s: Optional[float] = None
    last_seen_s: float = field(default_factory=lambda: time.time())
    last_disconnect_s: Optional[float] = None

    # Traffic & counters
    bytes_in: int = 0
    bytes_out: int = 0
    msgs_in: int = 0
    msgs_out: int = 0

    # Latency (EWMA)
    rtt_ms_ewma: Optional[float] = None
    rtt_alpha: float = 0.2
    rtt_samples: int = 0

    # Scoring
    topic_scores: Dict[str, TopicScore] = field(default_factory=dict)
    penalties: Dict[str, float] = field(default_factory=dict)  # reason -> points (negative)
    uptime_origin_s: float = field(default_factory=lambda: time.time())
    score_params: ScoreParams = field(default_factory=ScoreParams)

    # Per-topic rate limiters (cost ~= message bytes or 1 per msg)
    buckets: Dict[str, TokenBucket] = field(default_factory=dict)

    # Connection/bookkeeping (opaque identifiers, optional)
    conn_id: Optional[str] = None
    transport: Optional[str] = None  # "tcp", "quic", "ws"

    # ----- Lifecycle -----------------------------------------------------

    def on_connected(self, now: Optional[float] = None) -> None:
        if now is None:
            now = time.time()
        self.status = PeerStatus.CONNECTED
        self.connected_at_s = now
        self.last_seen_s = now
        # Small flap penalty if this peer reconnected within the recent window.
        if self.last_disconnect_s and (now - self.last_disconnect_s) < self.score_params.flap_window_s:
            self.apply_penalty("flap", self.score_params.flap_penalty)

    def on_disconnected(self, now: Optional[float] = None) -> None:
        if now is None:
            now = time.time()
        self.status = PeerStatus.DISCONNECTED
        self.last_disconnect_s = now

    def seen_now(self, now: Optional[float] = None) -> None:
        self.last_seen_s = time.time() if now is None else now

    # ----- RTT / Latency -------------------------------------------------

    def update_rtt_ms(self, sample_ms: float) -> None:
        if sample_ms <= 0 or not math.isfinite(sample_ms):
            return
        if self.rtt_ms_ewma is None:
            self.rtt_ms_ewma = sample_ms
        else:
            self.rtt_ms_ewma = (1.0 - self.rtt_alpha) * self.rtt_ms_ewma + self.rtt_alpha * sample_ms
        self.rtt_samples += 1

    # ----- Traffic accounting -------------------------------------------

    def record_in(self, topic: str, nbytes: int, valid: bool, duplicate: bool = False) -> None:
        self.seen_now()
        self.bytes_in += max(0, nbytes)
        self.msgs_in += 1
        ts = self.topic_scores.setdefault(topic, TopicScore())
        ts.bytes_in += max(0, nbytes)
        if duplicate:
            ts.duplicate_msgs += 1
        elif valid:
            ts.valid_msgs += 1
        else:
            ts.invalid_msgs += 1

    def record_out(self, topic: str, nbytes: int) -> None:
        self.seen_now()
        self.bytes_out += max(0, nbytes)
        self.msgs_out += 1
        ts = self.topic_scores.setdefault(topic, TopicScore())
        ts.bytes_out += max(0, nbytes)

    # ----- Rate limiting -------------------------------------------------

    def ensure_bucket(self, topic: str, rate_per_s: float, burst: float) -> None:
        if topic not in self.buckets:
            self.buckets[topic] = TokenBucket(rate_per_s=rate_per_s, burst=burst)

    def can_publish(self, topic: str, cost: float = 1.0) -> bool:
        """
        Cheap local guard; global/topic-wide policies should also be enforced
        by p2p/peer/ratelimit.py. This is a best-effort fast check.
        """
        b = self.buckets.get(topic)
        if b is None:
            # default permissive small bucket if not configured
            b = TokenBucket(rate_per_s=20.0, burst=40.0)
            self.buckets[topic] = b
        return b.allow(cost)

    # ----- Penalties & decay --------------------------------------------

    def apply_penalty(self, reason: str, points: float) -> None:
        """
        Apply a penalty (negative points). Positive values are allowed but should be rare
        (e.g., manual forgiveness). Penalties decay over time.
        """
        if not math.isfinite(points):
            return
        self.penalties[reason] = self.penalties.get(reason, 0.0) + points

    def _decay(self, value: float, half_life_s: float, dt_s: float) -> float:
        if half_life_s <= 0:
            return 0.0
        # Exponential decay: v * 0.5^(dt / half_life)
        return value * math.pow(0.5, dt_s / half_life_s)

    def decay_scores(self, now: Optional[float] = None) -> None:
        """
        Decay topic contributions and penalties over time. Topic-level counters
        remain (for stats), but their effect on the final score is re-weighted
        each time compute_score() is called.
        """
        # No internal mutation needed here for topic counters; decay is applied at read time.
        # We *do* decay penalties (they are stored as current effective values).
        if now is None:
            now = time.time()
        # Penalties are tracked as present-time magnitudes; decay in place.
        for k in list(self.penalties.keys()):
            self.penalties[k] = self._decay(
                self.penalties[k],
                self.score_params.penalty_decay_half_life_s,
                dt_s=1.0  # minor smoothing each call; larger dt handled by compute_score()
            )
            if abs(self.penalties[k]) < 1e-6:
                del self.penalties[k]

    # ----- Score ---------------------------------------------------------

    def compute_score(self, now: Optional[float] = None) -> float:
        """
        Compute the peer's current gossip/application score.

        Components:
          - base
          - sum over topics (quality, bounded per topic)
          - latency penalty vs reference RTT
          - uptime bounded bonus
          - decayed penalties (misbehavior, DoS, flaps)
        """
        if now is None:
            now = time.time()

        p = self.score_params
        score = p.base

        # Per-topic bounded quality
        topic_sum = 0.0
        for ts in self.topic_scores.values():
            topic_sum += ts.quality_score(p)
        score += topic_sum

        # Latency penalty
        if self.rtt_ms_ewma is not None:
            over = max(0.0, self.rtt_ms_ewma - p.rtt_ref_ms) / max(1.0, p.rtt_ref_ms)
            score -= over * (100.0 * p.rtt_slope)  # normalize to %
        # Uptime bounded bonus
        uptime_s = max(0.0, now - self.uptime_origin_s)
        score += min(p.uptime_bonus_max, uptime_s * p.uptime_bonus_rate)

        # Add (decayed) penalties
        # Apply real-time decay by scaling penalties according to elapsed time since connect.
        if self.connected_at_s:
            dt_s = max(0.0, now - self.connected_at_s)
        else:
            dt_s = 0.0

        penalties_total = 0.0
        for val in self.penalties.values():
            # Apply decay based on *real* dt_s (not small incremental).
            penalties_total += self._decay(val, p.penalty_decay_half_life_s, dt_s=dt_s)
        score -= abs(penalties_total)

        # If score falls below threshold, mark for ban.
        if score < p.ban_threshold:
            self.status = PeerStatus.BANNED

        return score

    # ----- Helpers & introspection --------------------------------------

    def gossip_health(self) -> Dict[str, float]:
        """
        Produce a small health snapshot useful for logs/metrics.
        """
        return {
            "score": self.compute_score(),
            "rtt_ms": float(self.rtt_ms_ewma or 0.0),
            "bytes_in": float(self.bytes_in),
            "bytes_out": float(self.bytes_out),
            "msgs_in": float(self.msgs_in),
            "msgs_out": float(self.msgs_out),
        }

    def set_topic_bucket(self, topic: str, rate_per_s: float, burst: float) -> None:
        self.buckets[topic] = TokenBucket(rate_per_s=rate_per_s, burst=burst)

    def snapshot(self) -> Dict[str, object]:
        """
        Return a JSON-serializable snapshot for debugging or metrics exporters.
        """
        return {
            "peer_id": self.peer_id,
            "address": self.address,
            "roles": int(self.roles),
            "status": self.status.value,
            "chain_id": self.chain_id,
            "head_height": self.head_height,
            "caps": sorted(self.caps),
            "last_seen_s": self.last_seen_s,
            "connected_at_s": self.connected_at_s,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "msgs_in": self.msgs_in,
            "msgs_out": self.msgs_out,
            "rtt_ms_ewma": self.rtt_ms_ewma,
            "topic_scores": {k: asdict(v) for k, v in self.topic_scores.items()},
            "penalties": dict(self.penalties),
            "score": self.compute_score(),
        }

    # ----- Policy checks -------------------------------------------------

    def supports_topic(self, topic: str) -> bool:
        """
        Lightweight check: whether this peer *claims* to support a topic,
        based on role/caps heuristics. Detailed subscription tracking is done
        by the gossip mesh; this is a conservative filter.
        """
        if "blocks" in topic or "headers" in topic:
            return bool(self.roles & (PeerRole.FULL | PeerRole.RELAY))
        if "txs" in topic:
            return bool(self.roles & (PeerRole.FULL | PeerRole.LIGHT | PeerRole.RELAY))
        if "shares" in topic:
            return bool(self.roles & (PeerRole.MINER | PeerRole.RELAY))
        if "da" in topic:
            return bool(self.roles & (PeerRole.DA_ONLY | PeerRole.FULL | PeerRole.RELAY))
        return True

    # ----- Validation hooks ---------------------------------------------

    def expect_chain(self, chain_id: int) -> bool:
        return self.chain_id == chain_id

    def expect_alg_policy_root(self, root: bytes) -> bool:
        return self.alg_policy_root == root


# Convenience constructor from a HELLO/IDENTIFY handshake summary.
def peer_from_handshake(
    *,
    peer_id: str,
    address: str,
    roles: PeerRole,
    chain_id: int,
    alg_policy_root: bytes,
    head_height: int,
    caps: Optional[Set[str]] = None,
    conn_id: Optional[str] = None,
    transport: Optional[str] = None,
) -> Peer:
    p = Peer(
        peer_id=peer_id,
        address=address,
        roles=roles,
        chain_id=chain_id,
        alg_policy_root=alg_policy_root,
        head_height=head_height,
        caps=caps or set(),
        conn_id=conn_id,
        transport=transport,
    )
    p.on_connected()
    return p
