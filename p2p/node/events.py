from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Generic, List, Optional, Tuple, TypeVar, Union

log = logging.getLogger("animica.p2p.events")

T = TypeVar("T")

__all__ = [
    "NewPeerEvent",
    "NewHeadEvent",
    "NewTxEvent",
    "NewShareEvent",
    "BusEvent",
    "EventBus",
    "Subscription",
]

# -----------------------------
# Event payload dataclasses
# -----------------------------

@dataclass(frozen=True)
class NewPeerEvent:
    peer_id: str
    addr: str
    inbound: bool
    rtt_ms: Optional[float] = None
    agent: Optional[str] = None
    caps: Optional[List[str]] = None  # e.g. ["blocks", "txs", "shares"]


@dataclass(frozen=True)
class NewHeadEvent:
    height: int
    hash_hex: str
    parent_hex: Optional[str]
    timestamp: int  # unix seconds
    difficulty_theta: int  # Θ (µ-nats or policy-specific units)
    da_root: Optional[str] = None


@dataclass(frozen=True)
class NewTxEvent:
    tx_hash: str
    size_bytes: int
    sender: Optional[str] = None  # bech32m anim1…
    tip_gwei: Optional[int] = None
    gas_limit: Optional[int] = None


@dataclass(frozen=True)
class NewShareEvent:
    share_type: str  # "hash" | "ai" | "quantum" | "storage" | "vdf"
    nullifier: str
    d_ratio_ppm: Optional[int] = None   # share difficulty ratio in ppm
    psi_micro: Optional[int] = None     # ψ contribution in micro-units
    miner: Optional[str] = None         # coinbase / address


Payload = Union[NewPeerEvent, NewHeadEvent, NewTxEvent, NewShareEvent]

@dataclass(frozen=True)
class BusEvent(Generic[T]):
    topic: str
    payload: T
    ts: float  # monotonic time of enqueue


# -----------------------------
# EventBus implementation
# -----------------------------

class Subscription(Generic[T]):
    """Handle returned by EventBus.subscribe(); allows async iteration and manual close()."""

    __slots__ = ("_topic", "_queue", "_closed", "_close_evt")

    def __init__(self, topic: str, queue: "asyncio.Queue[BusEvent[T]]") -> None:
        self._topic = topic
        self._queue = queue
        self._closed = False
        self._close_evt = asyncio.Event()

    @property
    def topic(self) -> str:
        return self._topic

    def closed(self) -> bool:
        return self._closed

    async def __anext__(self) -> BusEvent[T]:
        if self._closed:
            raise StopAsyncIteration
        evt: BusEvent[T] = await self._queue.get()
        if evt.topic == "__CLOSE__":
            self._closed = True
            raise StopAsyncIteration
        return evt

    def __aiter__(self) -> AsyncIterator[BusEvent[T]]:
        async def gen() -> AsyncIterator[BusEvent[T]]:
            while not self._closed:
                try:
                    yield await self.__anext__()
                except StopAsyncIteration:
                    break
        return gen()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # push a sentinel to unblock waiters
        await self._queue.put(BusEvent(topic="__CLOSE__", payload=None, ts=time.monotonic()))  # type: ignore[arg-type]
        self._close_evt.set()


class EventBus:
    """
    Lightweight in-process pub/sub with per-topic fanout queues.

    Topics (string):
      - "newPeer"   : NewPeerEvent
      - "newHead"   : NewHeadEvent
      - "newTx"     : NewTxEvent
      - "newShare"  : NewShareEvent
    Special:
      - "*"         : wildcard subscriber receives ALL topics

    Backpressure: each subscriber has a bounded queue (default 512). If a queue is full,
    the publish() will drop the event for that subscriber and log a debug line.
    """

    def __init__(self, *, loop: Optional[asyncio.AbstractEventLoop] = None, queue_size: int = 512) -> None:
        self._loop = loop or asyncio.get_event_loop()
        self._subs: Dict[str, List[Tuple[asyncio.Queue, Subscription]]] = {}
        self._wildcards: List[Tuple[asyncio.Queue, Subscription]] = []
        self._queue_size = max(1, queue_size)
        self._closed = False
        # metrics
        self._published = 0
        self._dropped = 0

    # ---- subscribe / unsubscribe -------------------------------------------

    def subscribe(self, topic: str, *, max_queue: Optional[int] = None) -> Subscription:
        """
        Subscribe to a topic or "*" (wildcard). Returns a Subscription handle.

        Usage:
            sub = bus.subscribe("newHead")
            async for evt in sub:
                handle(evt.payload)
        """
        if self._closed:
            raise RuntimeError("EventBus is closed")

        q: asyncio.Queue = asyncio.Queue(maxsize=max_queue or self._queue_size)
        sub = Subscription(topic, q)
        if topic == "*":
            self._wildcards.append((q, sub))
        else:
            self._subs.setdefault(topic, []).append((q, sub))
        return sub

    async def unsubscribe(self, sub: Subscription) -> None:
        await sub.close()
        if sub.topic == "*":
            self._wildcards = [(q, s) for (q, s) in self._wildcards if s is not sub]
        else:
            if sub.topic in self._subs:
                self._subs[sub.topic] = [(q, s) for (q, s) in self._subs[sub.topic] if s is not sub]
                if not self._subs[sub.topic]:
                    self._subs.pop(sub.topic, None)

    # ---- publish ------------------------------------------------------------

    async def publish(self, topic: str, payload: Payload) -> None:
        if self._closed:
            return
        evt = BusEvent(topic=topic, payload=payload, ts=time.monotonic())
        targets: List[Tuple[asyncio.Queue, Subscription]] = []
        targets.extend(self._wildcards)
        targets.extend(self._subs.get(topic, []))

        # prune closed
        if targets:
            alive: List[Tuple[asyncio.Queue, Subscription]] = []
            for q, s in targets:
                if not s.closed():
                    alive.append((q, s))
            # rewrite lists (cheap & safe)
            if topic in self._subs:
                self._subs[topic] = [(q, s) for (q, s) in self._subs[topic] if not s.closed()]
            self._wildcards = [(q, s) for (q, s) in self._wildcards if not s.closed()]
            targets = alive

        self._published += 1
        for q, _ in targets:
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                self._dropped += 1
                log.debug("event drop: topic=%s queue_full size=%s", topic, q.maxsize)

    # ---- helpers for common topics -----------------------------------------

    async def emit_new_peer(self, **kwargs: Any) -> None:
        await self.publish("newPeer", NewPeerEvent(**kwargs))

    async def emit_new_head(self, **kwargs: Any) -> None:
        await self.publish("newHead", NewHeadEvent(**kwargs))

    async def emit_new_tx(self, **kwargs: Any) -> None:
        await self.publish("newTx", NewTxEvent(**kwargs))

    async def emit_new_share(self, **kwargs: Any) -> None:
        await self.publish("newShare", NewShareEvent(**kwargs))

    # ---- lifecycle & metrics ------------------------------------------------

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Close all subscriptions
        for _, sub in list(self._wildcards):
            await sub.close()
        for _, sub in [pair for pairs in self._subs.values() for pair in pairs]:
            await sub.close()
        self._wildcards.clear()
        self._subs.clear()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "topics": {topic: len(lst) for topic, lst in self._subs.items()},
            "wildcards": len(self._wildcards),
            "published": self._published,
            "dropped": self._dropped,
            "closed": self._closed,
        }


# -----------------------------
# Example (for developers)
# Run this file directly to test the bus quickly.
# -----------------------------
if __name__ == "__main__":
    async def _demo() -> None:
        bus = EventBus(queue_size=2)
        sub_all = bus.subscribe("*")
        sub_heads = bus.subscribe("newHead")

        async def printer(label: str, sub: Subscription) -> None:
            async for evt in sub:
                log.info("%s got %s %s", label, evt.topic, evt.payload)

        t1 = asyncio.create_task(printer("ALL", sub_all))
        t2 = asyncio.create_task(printer("HEAD", sub_heads))

        await bus.emit_new_peer(peer_id="p1", addr="127.0.0.1:1234", inbound=True)
        await bus.emit_new_head(height=1, hash_hex="0xabc", parent_hex=None, timestamp=int(time.time()), difficulty_theta=1_000_000)
        await bus.emit_new_tx(tx_hash="0xdead", size_bytes=120, sender="anim1xyz")
        await bus.emit_new_share(share_type="hash", nullifier="n1", d_ratio_ppm=500_000, psi_micro=12_345)

        await asyncio.sleep(0.1)
        await bus.close()
        await asyncio.gather(t1, t2, return_exceptions=True)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_demo())
