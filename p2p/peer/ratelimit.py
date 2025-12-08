from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

# ────────────────────────────────────────────────────────────────────────────────
# Simple token bucket (public API)
# ────────────────────────────────────────────────────────────────────────────────


@dataclass
class RateBucket:
    """Minimal deterministic token bucket used by callers that expect RateBucket."""

    capacity: float
    fill_rate: float
    tokens: float
    last_refill: float

    @classmethod
    def fresh(
        cls, capacity: float, fill_rate: float, now: Optional[float] = None
    ) -> "RateBucket":
        n = time.monotonic() if now is None else now
        return cls(
            capacity=capacity, fill_rate=fill_rate, tokens=capacity, last_refill=n
        )

    def refill(self, now: Optional[float] = None) -> None:
        n = time.monotonic() if now is None else now
        if n <= self.last_refill:
            return
        delta = (n - self.last_refill) * self.fill_rate
        self.tokens = min(self.capacity, self.tokens + delta)
        self.last_refill = n

    def try_consume(self, cost: float = 1.0, now: Optional[float] = None) -> bool:
        if cost <= 0:
            return True
        self.refill(now)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


# ────────────────────────────────────────────────────────────────────────────────
# Token-bucket primitives
# ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BucketSpec:
    """
    Token-bucket parameters.

    capacity:   maximum burst (tokens)
    refill_ps:  steady-state refill rate (tokens per second)
    """

    capacity: float
    refill_ps: float

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("BucketSpec.capacity must be > 0")
        if self.refill_ps < 0:
            raise ValueError("BucketSpec.refill_ps must be >= 0")


@dataclass
class Bucket:
    spec: BucketSpec
    tokens: float
    t_last: float

    @classmethod
    def fresh(cls, spec: BucketSpec, now: Optional[float] = None) -> "Bucket":
        n = time.monotonic() if now is None else now
        return cls(spec=spec, tokens=spec.capacity, t_last=n)

    def _refill(self, now: float) -> None:
        if now <= self.t_last:
            return
        dt = now - self.t_last
        self.tokens = min(self.spec.capacity, self.tokens + dt * self.spec.refill_ps)
        self.t_last = now

    def try_consume(
        self, cost: float, now: Optional[float] = None
    ) -> Tuple[bool, float]:
        """
        Attempt to consume 'cost' tokens.

        Returns (allowed, retry_after_seconds).
        If not allowed, retry_after_seconds is the time until enough tokens accrue.
        """
        if cost <= 0:
            return True, 0.0
        n = time.monotonic() if now is None else now
        self._refill(n)
        if self.tokens >= cost:
            self.tokens -= cost
            return True, 0.0
        deficit = cost - self.tokens
        # If no refill rate, tokens will never be available
        if self.spec.refill_ps == 0:
            retry = float('inf')
        else:
            retry = max(0.0, deficit / self.spec.refill_ps)
        return False, retry


# ────────────────────────────────────────────────────────────────────────────────
# Hierarchical limiter (global • topic • peer • peer×topic)
# ────────────────────────────────────────────────────────────────────────────────


@dataclass
class RatelimitConfig:
    """
    Limiter configuration. All specs are optional; unspecified tiers are skipped.
    """

    # global across all messages from/to this node
    global_spec: Optional[BucketSpec] = None

    # per-topic global bucket (topic -> spec)
    topic_specs: Dict[str, BucketSpec] = field(default_factory=dict)

    # per-peer bucket (peer_id -> spec). If unset, uses `per_peer_default` if provided.
    per_peer_default: Optional[BucketSpec] = None
    per_peer_specs: Dict[str, BucketSpec] = field(default_factory=dict)

    # per-peer×topic bucket (topic -> spec). Applies to every peer for that topic.
    per_peer_topic_specs: Dict[str, BucketSpec] = field(default_factory=dict)

    # cost weights per topic (e.g., blocks cost more than pings)
    topic_costs: Dict[str, float] = field(default_factory=dict)

    def cost_for(self, topic: str, base_cost: float) -> float:
        w = self.topic_costs.get(topic, 1.0)
        return max(0.0, base_cost * w)


class RateLimiter:
    """
    Token-bucket limiter with 4 independent tiers checked in this order:

        1) Global
        2) Topic
        3) Per-Peer (all topics)
        4) Per-Peer×Topic

    A request is allowed iff *all* applicable buckets allow it.

    Thread-safe for asyncio via an internal lock. Designed to be very cheap on the hot path.
    """

    def __init__(
        self,
        cfg: Optional[RatelimitConfig] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> None:
        """
        Initialize RateLimiter with configuration.
        
        Args:
            cfg: RatelimitConfig object (preferred)
            config: Dict configuration (alternative)
            **kwargs: Individual configuration parameters for flexible construction
        """
        # Extract clock function if provided
        self._clock_fn = kwargs.pop("time_fn", None) or kwargs.pop("now_fn", None) or kwargs.pop("clock_fn", None) or kwargs.pop("clock", None)
        
        # Accept both 'cfg' and 'config' for compatibility
        if cfg is None and config is not None:
            cfg = self._config_from_dict(config)
        elif cfg is None and kwargs:
            cfg = self._config_from_kwargs(kwargs)
        elif cfg is None:
            # Default empty config
            cfg = RatelimitConfig()
            
        self.cfg = cfg
        # Use threading lock for sync tests, can be converted to asyncio.Lock when needed
        self._lock = threading.Lock()
        self._buckets_global: Optional[Bucket] = (
            Bucket.fresh(cfg.global_spec, now=self._now()) if cfg.global_spec else None
        )
        self._buckets_topic: Dict[str, Bucket] = {}
        self._buckets_peer: Dict[str, Bucket] = {}
        self._buckets_peer_topic: Dict[Tuple[str, str], Bucket] = {}
    
    def _now(self) -> float:
        """Get current time using configured clock function or default."""
        if self._clock_fn is not None:
            return float(self._clock_fn())
        return time.monotonic()
    
    def _config_from_dict(self, config_dict: Dict[str, Any]) -> RatelimitConfig:
        """Convert dict config to RatelimitConfig."""
        cfg = RatelimitConfig()
        
        # Handle per_peer configuration
        if "per_peer" in config_dict:
            pp = config_dict["per_peer"]
            if isinstance(pp, dict):
                cfg.per_peer_default = BucketSpec(
                    capacity=pp.get("capacity", 10.0),
                    refill_ps=pp.get("fill_rate", 0.0)
                )
        
        # Handle per_topic configuration
        if "per_topic" in config_dict:
            pt = config_dict["per_topic"]
            if isinstance(pt, dict):
                for topic, spec in pt.items():
                    if isinstance(spec, dict):
                        cfg.topic_specs[topic] = BucketSpec(
                            capacity=spec.get("capacity", 10.0),
                            refill_ps=spec.get("fill_rate", 0.0)
                        )
        
        # Handle global_limit configuration
        if "global_limit" in config_dict and config_dict["global_limit"] is not None:
            gl = config_dict["global_limit"]
            if isinstance(gl, dict):
                cfg.global_spec = BucketSpec(
                    capacity=gl.get("capacity", 100.0),
                    refill_ps=gl.get("fill_rate", 0.0)
                )
        
        return cfg
    
    def _config_from_kwargs(self, kwargs: Dict[str, Any]) -> RatelimitConfig:
        """Convert keyword arguments to RatelimitConfig."""
        # This handles test patterns like:
        # RateLimiter(per_peer_capacity=10, per_peer_fill_rate=1.0, per_topic={...})
        cfg = RatelimitConfig()
        
        # Per-peer configuration
        if "per_peer_capacity" in kwargs or "per_peer_fill_rate" in kwargs:
            cfg.per_peer_default = BucketSpec(
                capacity=kwargs.get("per_peer_capacity", 10.0),
                refill_ps=kwargs.get("per_peer_fill_rate", 0.0)
            )
        elif "per_peer" in kwargs:
            pp = kwargs["per_peer"]
            if isinstance(pp, dict):
                cfg.per_peer_default = BucketSpec(
                    capacity=pp.get("capacity", 10.0),
                    refill_ps=pp.get("fill_rate", 0.0)
                )
        
        # Per-topic configuration
        if "per_topic" in kwargs or "topics" in kwargs:
            topics_dict = kwargs.get("per_topic") or kwargs.get("topics", {})
            if isinstance(topics_dict, dict):
                for topic, spec in topics_dict.items():
                    if isinstance(spec, dict):
                        cfg.topic_specs[topic] = BucketSpec(
                            capacity=spec.get("capacity", 10.0),
                            refill_ps=spec.get("fill_rate", 0.0)
                        )
        
        # Global limit configuration
        if "global_capacity" in kwargs or "global_limit" in kwargs:
            gl = kwargs.get("global_limit")
            if gl is not None and isinstance(gl, dict):
                cfg.global_spec = BucketSpec(
                    capacity=gl.get("capacity", 100.0),
                    refill_ps=gl.get("fill_rate", 0.0)
                )
            elif "global_capacity" in kwargs:
                cfg.global_spec = BucketSpec(
                    capacity=kwargs.get("global_capacity", 100.0),
                    refill_ps=kwargs.get("global_fill_rate", 0.0)
                )
        
        return cfg

    # ── Public API ──────────────────────────────────────────────────────────────

    def allow(
        self,
        *,
        peer_id: Optional[str] = None,
        topic: Optional[str] = None,
        cost: float = 1.0,
        now: Optional[float] = None,
    ) -> Tuple[bool, float, Tuple[str, ...]]:
        """
        Attempt to consume tokens for an action.

        Returns (allowed, retry_after_seconds, limiting_keys)
          - allowed: True if permitted
          - retry_after_seconds: 0 if allowed; otherwise the max wait across violated buckets
          - limiting_keys: tuple of bucket keys that blocked (e.g., ('global', 'topic:blocks'))

        Keys format:
          'global'
          'topic:<topic>'
          'peer:<peer_id>'
          'peer_topic:<peer_id>:<topic>'
        """
        c = max(0.0, float(cost))
        # Apply topic cost weights
        if topic is not None:
            c = self.cfg.cost_for(topic, c)

        with self._lock:
            violated: Dict[str, float] = {}
            n = self._now() if now is None else now

            # 1) global
            if self._buckets_global is not None:
                ok, wait = self._buckets_global.try_consume(c, n)
                if not ok:
                    violated["global"] = wait

            # 2) topic
            if topic is not None and topic in self.cfg.topic_specs:
                bt = self._buckets_topic.get(topic)
                if bt is None:
                    bt = self._buckets_topic.setdefault(
                        topic, Bucket.fresh(self.cfg.topic_specs[topic], n)
                    )
                ok, wait = bt.try_consume(c, n)
                if not ok:
                    violated[f"topic:{topic}"] = wait

            # 3) per-peer
            if peer_id is not None:
                spec = self.cfg.per_peer_specs.get(peer_id, self.cfg.per_peer_default)
                if spec is not None:
                    bp = self._buckets_peer.get(peer_id)
                    if bp is None:
                        bp = self._buckets_peer.setdefault(
                            peer_id, Bucket.fresh(spec, n)
                        )
                    ok, wait = bp.try_consume(c, n)
                    if not ok:
                        violated[f"peer:{peer_id}"] = wait

            # 4) per-peer×topic
            if (
                peer_id is not None
                and topic is not None
                and topic in self.cfg.per_peer_topic_specs
            ):
                key = (peer_id, topic)
                spec = self.cfg.per_peer_topic_specs[topic]
                bpt = self._buckets_peer_topic.get(key)
                if bpt is None:
                    bpt = self._buckets_peer_topic.setdefault(
                        key, Bucket.fresh(spec, n)
                    )
                ok, wait = bpt.try_consume(c, n)
                if not ok:
                    violated[f"peer_topic:{peer_id}:{topic}"] = wait

            if violated:
                # roll back prior consumes? We intentionally **do not** roll back to avoid
                # side-channels on partial success and to stay conservative. Because all
                # buckets used the same 'now' snapshot, observed fairness holds.
                max_wait = max(violated.values())
                return False, max_wait, tuple(sorted(violated.keys()))
            return True, 0.0, tuple()

    async def wait(
        self, *, peer_id: Optional[str], topic: Optional[str], cost: float = 1.0
    ) -> None:
        """
        Sleep until the action would be allowed. Uses exponential backoff caps.
        """
        backoff = 0.01
        while True:
            ok, retry, _ = await self.allow(peer_id=peer_id, topic=topic, cost=cost)
            if ok:
                return
            await asyncio.sleep(min(max(0.001, retry), 1.0) + backoff)
            backoff = min(backoff * 1.6, 0.25)

    def snapshot(self) -> Dict[str, Any]:
        """Return a lightweight snapshot of internal bucket levels for observability."""
        snap: Dict[str, Any] = {}
        if self._buckets_global is not None:
            snap["global"] = _bview(self._buckets_global)
        if self._buckets_topic:
            snap["topic"] = {k: _bview(v) for k, v in self._buckets_topic.items()}
        if self._buckets_peer:
            snap["peer"] = {k: _bview(v) for k, v in self._buckets_peer.items()}
        if self._buckets_peer_topic:
            snap["peer_topic"] = {
                f"{p}:{t}": _bview(b) for (p, t), b in self._buckets_peer_topic.items()
            }
        return snap

    # ── Admin helpers ───────────────────────────────────────────────────────────

    async def set_global(self, spec: Optional[BucketSpec]) -> None:
        async with self._lock:
            self.cfg.global_spec = spec
            self._buckets_global = Bucket.fresh(spec) if spec else None

    async def set_topic(self, topic: str, spec: Optional[BucketSpec]) -> None:
        async with self._lock:
            if spec is None:
                self.cfg.topic_specs.pop(topic, None)
                self._buckets_topic.pop(topic, None)
            else:
                self.cfg.topic_specs[topic] = spec
                self._buckets_topic[topic] = Bucket.fresh(spec)

    async def set_peer(self, peer_id: str, spec: Optional[BucketSpec]) -> None:
        async with self._lock:
            if spec is None:
                self.cfg.per_peer_specs.pop(peer_id, None)
                self._buckets_peer.pop(peer_id, None)
            else:
                self.cfg.per_peer_specs[peer_id] = spec
                self._buckets_peer[peer_id] = Bucket.fresh(spec)

    async def set_peer_topic(self, topic: str, spec: Optional[BucketSpec]) -> None:
        async with self._lock:
            if spec is None:
                self.cfg.per_peer_topic_specs.pop(topic, None)
                for k in list(self._buckets_peer_topic.keys()):
                    if k[1] == topic:
                        self._buckets_peer_topic.pop(k, None)
            else:
                self.cfg.per_peer_topic_specs[topic] = spec
                # reset all existing peer×topic buckets for this topic
                for k in list(self._buckets_peer_topic.keys()):
                    if k[1] == topic:
                        self._buckets_peer_topic[k] = Bucket.fresh(spec)

    async def prune(self, peers_alive: Optional[Iterable[str]] = None) -> None:
        """
        Optional GC to drop buckets for peers that disappeared.
        """
        async with self._lock:
            if peers_alive is not None:
                alive = set(peers_alive)
                for pid in list(self._buckets_peer.keys()):
                    if pid not in alive:
                        self._buckets_peer.pop(pid, None)
                for pid, topic in list(self._buckets_peer_topic.keys()):
                    if pid not in alive:
                        self._buckets_peer_topic.pop((pid, topic), None)


class PeerRateLimiter(RateLimiter):
    """
    Compatibility wrapper expected by older callers.

    Accepts a handful of relaxed constructor shapes and normalizes them into a
    RatelimitConfig. Only the per-peer and per-topic tiers are wired here since
    that covers the majority of use cases in the Animica stack.
    """

    def __init__(
        self,
        *,
        per_peer: Optional[Dict[str, float]] = None,
        per_topic: Optional[Dict[str, Dict[str, float]]] = None,
        global_limits: Optional[Dict[str, float]] = None,
    ) -> None:
        def _spec(maybe: Optional[Dict[str, float]]) -> Optional[BucketSpec]:
            if not maybe:
                return None
            cap = maybe.get("capacity") or maybe.get("cap") or maybe.get("burst")
            rate = maybe.get("fill_rate") or maybe.get("rate")
            if cap is None or rate is None:
                return None
            return BucketSpec(float(cap), float(rate))

        topic_specs: Dict[str, BucketSpec] = {}
        if per_topic:
            for topic, cfg in per_topic.items():
                spec = _spec(cfg)
                if spec:
                    topic_specs[topic] = spec

        cfg = RatelimitConfig(
            global_spec=_spec(global_limits),
            topic_specs=topic_specs,
            per_peer_default=_spec(per_peer),
        )
        super().__init__(cfg)


# ────────────────────────────────────────────────────────────────────────────────
# Utilities & sensible defaults
# ────────────────────────────────────────────────────────────────────────────────


def _bview(b: Bucket) -> Dict[str, float]:
    now = time.monotonic()
    # project a view after refill to avoid confusing snapshots
    b._refill(now)
    return {
        "capacity": b.spec.capacity,
        "refill_ps": b.spec.refill_ps,
        "tokens": b.tokens,
        "t_last": b.t_last,
    }


def default_config() -> RatelimitConfig:
    """
    Conservative defaults:
      - global: 200 msgs/s, burst 400
      - topic weights: blocks=10, headers=2, tx=1, ping=0.5
      - per-peer: 50 msgs/s, burst 100
      - per-peer×topic: blocks=2/s burst 4, headers=10/s burst 20, tx=30/s burst 60, ping=5/s burst 10
    """
    return RatelimitConfig(
        global_spec=BucketSpec(capacity=400.0, refill_ps=200.0),
        topic_specs={},  # we rely mainly on peer×topic below
        per_peer_default=BucketSpec(capacity=100.0, refill_ps=50.0),
        per_peer_specs={},
        per_peer_topic_specs={
            "blocks": BucketSpec(capacity=4.0, refill_ps=2.0),
            "headers": BucketSpec(capacity=20.0, refill_ps=10.0),
            "tx": BucketSpec(capacity=60.0, refill_ps=30.0),
            "shares": BucketSpec(capacity=40.0, refill_ps=20.0),
            "ping": BucketSpec(capacity=10.0, refill_ps=5.0),
        },
        topic_costs={
            "blocks": 10.0,
            "headers": 2.0,
            "tx": 1.0,
            "shares": 1.0,
            "ping": 0.5,
        },
    )


# ────────────────────────────────────────────────────────────────────────────────
# Example usage (for tests / reference)
# ────────────────────────────────────────────────────────────────────────────────


async def _example() -> None:  # pragma: no cover
    limiter = RateLimiter(default_config())

    peer = "animica:peer:abc"
    topic = "tx"

    for i in range(5):
        ok, retry, keys = await limiter.allow(peer_id=peer, topic=topic, cost=1.0)
        if ok:
            print(
                f"[{i}] allowed; snapshot={limiter.snapshot()['peer_topic'][f'{peer}:{topic}']}"
            )
        else:
            print(f"[{i}] limited by {keys}, retry in {retry:.3f}s")
            await asyncio.sleep(retry)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_example())
