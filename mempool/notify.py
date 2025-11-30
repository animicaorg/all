"""
mempool.notify
==============

A minimal, dependency-light event bus for mempool notifications plus an
optional bridge to the RPC WebSocket hub. It emits three topics:

  - "pendingTx"   : a transaction admitted into the mempool
  - "droppedTx"   : a transaction removed/evicted from the mempool
  - "replacedTx"  : a transaction replaced by a higher-fee one (RBF)

The bus is synchronous, thread-safe, and tolerant of subscriber errors.

WebSocket Bridge
----------------
If your FastAPI app mounts the WS hub (see `rpc/ws.py`), you can forward
events to clients by installing the bridge:

    from mempool.notify import default_bus, WSBridge

    ws_bridge = WSBridge.from_hub(ws_hub)     # ws_hub.broadcast(topic, payload)
    ws_bridge.attach(default_bus)

Payload Conventions
-------------------
All events are JSON-serializable dictionaries. Common fields:

- "hash":  0x-prefixed hex string of tx hash (required where applicable)
- "ts":    unix timestamp (float)
- "meta":  optional dict with implementation-specific details

Event-specific fields:

pendingTx
    {
      "hash": "0x..",
      "sender": "0x..",          # optional
      "nonce":  7,               # optional
      "effectiveFee": 1234,      # optional (tip or 1559-style effective)
      "size":  192,              # optional bytes
      "meta": {...}
    }

droppedTx
    {
      "hash": "0x..",
      "reason": "fee_too_low|ttl|evicted|reorg|ban|other",
      "meta": {...}
    }

replacedTx
    {
      "old":  "0x..",
      "new":  "0x..",
      "reason": "rbf|duplicate|better_fee|policy",
      "meta": {...}
    }

Notes
-----
- This module does not depend on Pydantic/FastAPI.
- The WS bridge looks for .broadcast(topic, payload) on the hub; if missing,
  it falls back to .publish(...) or .send(...).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import (Any, Callable, Dict, Iterable, List, Optional, Tuple,
                    TypedDict)

logger = logging.getLogger(__name__)

# Type aliases
JSONDict = Dict[str, Any]
Subscriber = Callable[[str, JSONDict], None]


# --------------------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------------------


def _to_hex(v: Any) -> Optional[str]:
    """
    Convert bytes/bytearray/int/hexstr to 0x-prefixed hex string.
    Returns None if conversion is not possible or input is None.
    """
    if v is None:
        return None
    try:
        if isinstance(v, (bytes, bytearray)):
            return "0x" + bytes(v).hex()
        if isinstance(v, int):
            # width-agnostic
            if v < 0:
                v = (1 << (v.bit_length() + 8 - (v.bit_length() % 8))) + v
            h = hex(v)[2:]
            return "0x" + (h if len(h) % 2 == 0 else "0" + h)
        if isinstance(v, str):
            if v.startswith("0x") or v.startswith("0X"):
                return "0x" + v[2:]
            # assume raw hex without prefix
            return "0x" + v
    except Exception:
        pass
    return None


def _now_ts() -> float:
    return float(time.time())


# --------------------------------------------------------------------------------------
# Local Event Bus
# --------------------------------------------------------------------------------------


class Subscription:
    """Opaque handle returned to subscribers to allow unsubscription."""

    __slots__ = ("_bus", "_topic", "_cb", "_id")

    def __init__(self, bus: "LocalEventBus", topic: str, cb: Subscriber):
        self._bus = bus
        self._topic = topic
        self._cb = cb
        self._id = uuid.uuid4().hex

    def unsubscribe(self) -> None:
        self._bus._unsubscribe(self._topic, self._cb)


class LocalEventBus:
    """
    A simple, synchronous, thread-safe pub-sub bus.

    - Per-topic subscription lists
    - Best-effort delivery: subscriber exceptions are caught and logged
    - Returns the number of subscribers invoked on publish
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._subs: Dict[str, List[Subscriber]] = {}

    # -- subscription ----------------------------------------------------------

    def subscribe(self, topic: str, callback: Subscriber) -> Subscription:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            self._subs.setdefault(topic, []).append(callback)
        return Subscription(self, topic, callback)

    def _unsubscribe(self, topic: str, callback: Subscriber) -> None:
        with self._lock:
            lst = self._subs.get(topic)
            if not lst:
                return
            try:
                lst.remove(callback)
            except ValueError:
                pass
            if not lst:
                self._subs.pop(topic, None)

    def subscribers(self, topic: str) -> int:
        with self._lock:
            return len(self._subs.get(topic, []))

    # -- publishing ------------------------------------------------------------

    def publish(self, topic: str, payload: JSONDict) -> int:
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        with self._lock:
            subs = list(self._subs.get(topic, []))
        delivered = 0
        for cb in subs:
            try:
                cb(topic, payload)
                delivered += 1
            except Exception as e:
                logger.warning(
                    "subscriber error on topic=%s: %s", topic, e, exc_info=True
                )
        return delivered


# A default, process-local bus that components can share.
default_bus = LocalEventBus()


# --------------------------------------------------------------------------------------
# High-level Notifier helpers for mempool events
# --------------------------------------------------------------------------------------

PENDING_TX = "pendingTx"
DROPPED_TX = "droppedTx"
REPLACED_TX = "replacedTx"


def notify_pending_tx(
    bus: LocalEventBus,
    tx_hash: Any,
    *,
    sender: Any = None,
    nonce: Optional[int] = None,
    effective_fee: Optional[int] = None,
    size: Optional[int] = None,
    meta: Optional[JSONDict] = None,
) -> int:
    payload: JSONDict = {
        "hash": _to_hex(tx_hash),
        "ts": _now_ts(),
    }
    if sender is not None:
        payload["sender"] = _to_hex(sender) or sender
    if nonce is not None:
        payload["nonce"] = int(nonce)
    if effective_fee is not None:
        payload["effectiveFee"] = int(effective_fee)
    if size is not None:
        payload["size"] = int(size)
    if meta:
        payload["meta"] = dict(meta)
    return bus.publish(PENDING_TX, payload)


def notify_dropped_tx(
    bus: LocalEventBus, tx_hash: Any, *, reason: str, meta: Optional[JSONDict] = None
) -> int:
    payload: JSONDict = {
        "hash": _to_hex(tx_hash),
        "reason": str(reason),
        "ts": _now_ts(),
    }
    if meta:
        payload["meta"] = dict(meta)
    return bus.publish(DROPPED_TX, payload)


def notify_replaced_tx(
    bus: LocalEventBus,
    old_hash: Any,
    new_hash: Any,
    *,
    reason: str = "rbf",
    meta: Optional[JSONDict] = None,
) -> int:
    payload: JSONDict = {
        "old": _to_hex(old_hash),
        "new": _to_hex(new_hash),
        "reason": str(reason),
        "ts": _now_ts(),
    }
    if meta:
        payload["meta"] = dict(meta)
    return bus.publish(REPLACED_TX, payload)


# --------------------------------------------------------------------------------------
# WebSocket Bridge
# --------------------------------------------------------------------------------------


class WSBridge:
    """
    Bridges LocalEventBus -> WebSocket hub.

    The hub is expected to expose one of:
        - hub.broadcast(topic: str, payload: dict) -> None
        - hub.publish(topic: str, payload: dict) -> None
        - hub.send(topic: str, payload: dict) -> None

    The bridge subscribes to PENDING_TX, DROPPED_TX, REPLACED_TX and forwards
    events to the hub. Duplicate payloads (by tx hash) can be suppressed for
    a short TTL to avoid spamming reconnecting clients.
    """

    def __init__(
        self, sender: Callable[[str, JSONDict], None], *, dedupe_ttl_sec: float = 2.0
    ):
        self._sender = sender
        self._subs: List[Subscription] = []
        self._dedupe_ttl = max(0.0, float(dedupe_ttl_sec))
        self._last_sent: Dict[Tuple[str, Optional[str]], float] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_hub(cls, hub: Any, **kw: Any) -> "WSBridge":
        send = None
        for name in ("broadcast", "publish", "send"):
            fn = getattr(hub, name, None)
            if callable(fn):
                send = fn
                break
        if send is None:
            raise TypeError(
                "hub must provide .broadcast/.publish/.send(topic, payload)"
            )
        return cls(lambda t, p: send(t, p), **kw)

    # -- lifecycle -------------------------------------------------------------

    def attach(self, bus: LocalEventBus) -> None:
        """Subscribe to all mempool topics on the given bus."""
        if self._subs:
            return  # already attached
        self._subs.append(bus.subscribe(PENDING_TX, self._forward))
        self._subs.append(bus.subscribe(DROPPED_TX, self._forward))
        self._subs.append(bus.subscribe(REPLACED_TX, self._forward))
        logger.info(
            "WSBridge attached to bus; subscribers now: %s/%s/%s",
            bus.subscribers(PENDING_TX),
            bus.subscribers(DROPPED_TX),
            bus.subscribers(REPLACED_TX),
        )

    def detach(self) -> None:
        """Unsubscribe from the bus."""
        for s in self._subs:
            try:
                s.unsubscribe()
            except Exception:
                pass
        self._subs.clear()

    # -- forwarding ------------------------------------------------------------

    def _forward(self, topic: str, payload: JSONDict) -> None:
        key_hash: Optional[str] = None
        if topic == PENDING_TX or topic == DROPPED_TX:
            key_hash = payload.get("hash")
        elif topic == REPLACED_TX:
            key_hash = payload.get("new") or payload.get("old")

        if self._dedupe_ttl > 0.0 and key_hash:
            now = _now_ts()
            k = (topic, key_hash)
            with self._lock:
                last = self._last_sent.get(k, 0.0)
                if now - last < self._dedupe_ttl:
                    return
                self._last_sent[k] = now

        try:
            self._sender(topic, payload)
        except Exception as e:
            logger.warning("WSBridge send error topic=%s: %s", topic, e, exc_info=True)


__all__ = [
    "LocalEventBus",
    "Subscription",
    "default_bus",
    "PENDING_TX",
    "DROPPED_TX",
    "REPLACED_TX",
    "notify_pending_tx",
    "notify_dropped_tx",
    "notify_replaced_tx",
    "WSBridge",
]
