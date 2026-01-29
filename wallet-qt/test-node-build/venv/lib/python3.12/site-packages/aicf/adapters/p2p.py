from __future__ import annotations

"""
aicf.adapters.p2p
=================

Optional P2P adapter for gossiping provider heartbeats and coarse availability.
This module intentionally avoids binding to any specific P2P stack. Instead,
you can pass a lightweight "bus" object with:

    publish(topic: str, payload: bytes) -> None
    subscribe(topic: str, handler: Callable[[bytes], None]) -> Subscription
    # where Subscription exposes .close() or .unsubscribe()

Wire format
-----------
* JSON bytes (UTF-8) for maximum portability in early devnets.
* Schemas are stable and versioned via topic names.

Topics
------
* TOPIC_HEARTBEAT_V1      : Point-in-time liveness/capacity signal from a provider
* TOPIC_AVAILABILITY_V1   : (Optional) Aggregated view from an observer

Basic use
---------
    bus = MyP2PBus(...)
    p2p = P2PAdapter(bus)
    p2p.start()  # begins listening for heartbeats
    p2p.publish_heartbeat(Heartbeat(provider_id="prov1", height=123, ...))

You may register a callback to learn about remote heartbeats:
    def on_hb(hb: Heartbeat, src: str | None) -> None:
        print("heartbeat", hb.provider_id, hb.height)
    p2p.on_heartbeat(on_hb)

Notes
-----
* This adapter is best-effort and safe to disable; AICF core logic does not
  require P2P heartbeats to function (RPC/registry paths are authoritative).
"""

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ---- Topics (versioned) --------------------------------------------------------

TOPIC_HEARTBEAT_V1 = "aicf/provider/heartbeat/v1"
TOPIC_AVAILABILITY_V1 = "aicf/provider/availability/v1"


# ---- Errors --------------------------------------------------------------------


class P2PError(Exception):
    """Generic P2P adapter error."""


# ---- Messages ------------------------------------------------------------------


@dataclass
class Heartbeat:
    """
    Provider heartbeat message.

    Fields:
      provider_id: canonical provider identifier
      height     : chain height observed by the provider when sending the hb
      timestamp  : unix seconds (int) from provider's clock
      capacity_ai: available AI work units (non-binding hint)
      capacity_qp: available Quantum work units (non-binding hint)
      qos        : recent QoS score in [0, 1], coarse, self-reported
      nonce      : monotonically increasing integer per provider (dedupe aid)
      sig        : optional signature string (opaque to transport)
    """

    provider_id: str
    height: int
    timestamp: int
    capacity_ai: int = 0
    capacity_qp: int = 0
    qos: float = 1.0
    nonce: int = 0
    sig: Optional[str] = None

    def to_bytes(self) -> bytes:
        # Keep wire stable: sort keys; avoid floats with silly precision
        obj = asdict(self)
        # Clamp/normalize
        obj["provider_id"] = str(obj["provider_id"])
        obj["height"] = int(obj["height"])
        obj["timestamp"] = int(obj["timestamp"])
        obj["capacity_ai"] = int(max(0, obj["capacity_ai"]))
        obj["capacity_qp"] = int(max(0, obj["capacity_qp"]))
        q = float(obj["qos"])
        if q < 0.0:
            q = 0.0
        if q > 1.0:
            q = 1.0
        obj["qos"] = round(q, 6)  # deterministic trimming
        obj["nonce"] = int(max(0, obj["nonce"]))
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def from_bytes(data: bytes) -> "Heartbeat":
        try:
            obj = json.loads(data.decode("utf-8"))
        except Exception as e:  # pragma: no cover - defensive
            raise P2PError(f"invalid heartbeat payload: {e}")
        required = ("provider_id", "height", "timestamp")
        for k in required:
            if k not in obj:
                raise P2PError(f"missing field {k} in heartbeat")
        return Heartbeat(
            provider_id=str(obj["provider_id"]),
            height=int(obj["height"]),
            timestamp=int(obj["timestamp"]),
            capacity_ai=int(obj.get("capacity_ai", 0)),
            capacity_qp=int(obj.get("capacity_qp", 0)),
            qos=float(obj.get("qos", 1.0)),
            nonce=int(obj.get("nonce", 0)),
            sig=obj.get("sig"),
        )


@dataclass
class AvailabilityView:
    """
    Aggregated availability snapshot for a provider (optional).
    Produced by observers; may be gossiped for operator visibility.
    """

    provider_id: str
    height: int
    timestamp: int
    alive: bool
    recent_qos: float
    recent_success: float  # success rate in [0,1] over observer's window

    def to_bytes(self) -> bytes:
        obj = asdict(self)
        obj["height"] = int(obj["height"])
        obj["timestamp"] = int(obj["timestamp"])
        obj["alive"] = bool(obj["alive"])
        obj["recent_qos"] = round(float(obj["recent_qos"]), 6)
        obj["recent_success"] = round(float(obj["recent_success"]), 6)
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def from_bytes(data: bytes) -> "AvailabilityView":
        try:
            obj = json.loads(data.decode("utf-8"))
        except Exception as e:  # pragma: no cover
            raise P2PError(f"invalid availability payload: {e}")
        required = (
            "provider_id",
            "height",
            "timestamp",
            "alive",
            "recent_qos",
            "recent_success",
        )
        for k in required:
            if k not in obj:
                raise P2PError(f"missing field {k} in availability")
        return AvailabilityView(
            provider_id=str(obj["provider_id"]),
            height=int(obj["height"]),
            timestamp=int(obj["timestamp"]),
            alive=bool(obj["alive"]),
            recent_qos=float(obj["recent_qos"]),
            recent_success=float(obj["recent_success"]),
        )


# ---- Adapter -------------------------------------------------------------------


class P2PAdapter:
    """
    Thin wrapper over an injected bus for provider heartbeat gossip.

    Dedupe & basic validation:
      * Rejects non-monotonic nonce per provider (unless equal & older timestamp).
      * Rejects heartbeats older than `max_skew_sec` behind local time.
      * Enforces minimal interval per provider (`min_interval_sec`) to cut spam.

    Callbacks:
      - on_heartbeat_cb(hb: Heartbeat, src_peer: Optional[str]) -> None
      - on_availability_cb(av: AvailabilityView, src_peer: Optional[str]) -> None
    """

    def __init__(
        self,
        bus: Any,
        *,
        min_interval_sec: float = 5.0,
        max_skew_sec: float = 300.0,
    ) -> None:
        self.bus = bus
        self.min_interval = float(min_interval_sec)
        self.max_skew = float(max_skew_sec)
        self._subs: list[Any] = []
        self._lock = threading.RLock()
        # provider_id -> (last_nonce, last_ts, last_recv_wall)
        self._last: Dict[str, Tuple[int, int, float]] = {}
        self._on_hb: Optional[Callable[[Heartbeat, Optional[str]], None]] = None
        self._on_av: Optional[Callable[[AvailabilityView, Optional[str]], None]] = None

    # -- Lifecycle --------------------------------------------------------------

    def start(self) -> None:
        """Subscribe to topics on the bus."""
        try:
            self._subs.append(
                self.bus.subscribe(TOPIC_HEARTBEAT_V1, self._handle_heartbeat)
            )
        except Exception as e:  # pragma: no cover - bus-dependent
            logger.warning("failed to subscribe heartbeat topic: %s", e)
        try:
            self._subs.append(
                self.bus.subscribe(TOPIC_AVAILABILITY_V1, self._handle_availability)
            )
        except Exception as e:  # pragma: no cover
            logger.debug("availability subscription skipped: %s", e)

    def stop(self) -> None:
        """Unsubscribe from topics."""
        with self._lock:
            subs = list(self._subs)
            self._subs.clear()
        for s in subs:
            try:
                # support either .close() or .unsubscribe()
                if hasattr(s, "close"):
                    s.close()
                elif hasattr(s, "unsubscribe"):
                    s.unsubscribe()
            except Exception:  # pragma: no cover
                pass

    # -- Callbacks --------------------------------------------------------------

    def on_heartbeat(self, cb: Callable[[Heartbeat, Optional[str]], None]) -> None:
        self._on_hb = cb

    def on_availability(
        self, cb: Callable[[AvailabilityView, Optional[str]], None]
    ) -> None:
        self._on_av = cb

    # -- Publish ----------------------------------------------------------------

    def publish_heartbeat(self, hb: Heartbeat) -> None:
        """Publish a heartbeat to the network."""
        payload = hb.to_bytes()
        self.bus.publish(TOPIC_HEARTBEAT_V1, payload)

    def publish_availability(self, av: AvailabilityView) -> None:
        """Publish an availability snapshot to the network."""
        payload = av.to_bytes()
        self.bus.publish(TOPIC_AVAILABILITY_V1, payload)

    # -- Handlers ---------------------------------------------------------------

    def _handle_heartbeat(self, payload: bytes, peer_id: Optional[str] = None) -> None:
        try:
            hb = Heartbeat.from_bytes(payload)
        except Exception as e:
            logger.debug("drop invalid heartbeat: %s", e)
            return

        now = time.time()
        if hb.timestamp < int(now - self.max_skew):
            logger.debug(
                "stale heartbeat from %s dropped: ts=%s now=%s",
                hb.provider_id,
                hb.timestamp,
                int(now),
            )
            return

        with self._lock:
            last = self._last.get(hb.provider_id)
            if last is not None:
                last_nonce, last_ts, last_recv = last
                # Spam control: minimal wall interval per provider
                if (now - last_recv) < self.min_interval:
                    logger.debug("rate-limited heartbeat from %s", hb.provider_id)
                    return
                # Dedupe/ordering: strictly increasing nonce OR (same nonce & newer ts)
                if hb.nonce < last_nonce or (
                    hb.nonce == last_nonce and hb.timestamp <= last_ts
                ):
                    logger.debug(
                        "non-monotonic heartbeat from %s dropped (nonce %s<=%s, ts %s<=%s)",
                        hb.provider_id,
                        hb.nonce,
                        last_nonce,
                        hb.timestamp,
                        last_ts,
                    )
                    return
            # Accept and record
            self._last[hb.provider_id] = (hb.nonce, hb.timestamp, now)

        if self._on_hb:
            try:
                self._on_hb(hb, peer_id)
            except Exception as e:  # pragma: no cover
                logger.warning("heartbeat callback error: %s", e)

    def _handle_availability(
        self, payload: bytes, peer_id: Optional[str] = None
    ) -> None:
        try:
            av = AvailabilityView.from_bytes(payload)
        except Exception as e:
            logger.debug("drop invalid availability: %s", e)
            return
        if self._on_av:
            try:
                self._on_av(av, peer_id)
            except Exception as e:  # pragma: no cover
                logger.warning("availability callback error: %s", e)

    # -- Local inspection -------------------------------------------------------

    def last_seen(self, provider_id: str) -> Optional[Tuple[int, int, float]]:
        """Return (last_nonce, last_timestamp, last_recv_wall) for a provider, or None."""
        with self._lock:
            return self._last.get(provider_id)


__all__ = [
    "P2PError",
    "TOPIC_HEARTBEAT_V1",
    "TOPIC_AVAILABILITY_V1",
    "Heartbeat",
    "AvailabilityView",
    "P2PAdapter",
]
