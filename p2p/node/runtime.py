from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from .peer_registry import PeerRegistry, PeerSession, PeerState


class ConnectionStage(str, Enum):
    DIALING = "DIALING"
    TCP_CONNECTED = "TCP_CONNECTED"
    HELLO_SENT = "HELLO_SENT"
    HELLO_RECEIVED = "HELLO_RECEIVED"
    SESSION_CREATED = "SESSION_CREATED"
    ROUTES_BOUND = "ROUTES_BOUND"
    TIP_QUERY_SENT = "TIP_QUERY_SENT"
    PEER_READY = "PEER_READY"


_STAGE_ORDER = {
    ConnectionStage.DIALING: 0,
    ConnectionStage.TCP_CONNECTED: 1,
    ConnectionStage.HELLO_SENT: 2,
    ConnectionStage.HELLO_RECEIVED: 3,
    ConnectionStage.SESSION_CREATED: 4,
    ConnectionStage.ROUTES_BOUND: 5,
    ConnectionStage.TIP_QUERY_SENT: 6,
    ConnectionStage.PEER_READY: 7,
}


@dataclass(frozen=True)
class RuntimeCounts:
    total: int
    connected_total: int
    connected_inbound: int
    connected_outbound: int
    handshaking: int


class P2PRuntime:
    """
    Single authoritative runtime view for P2P connections and diagnostics.

    Owns:
      - Connection states (via PeerRegistry sessions)
      - Peerstore reference
      - Event ring buffer (fixed length)
      - Derived counters (no cached counters elsewhere)
    """

    def __init__(
        self,
        registry: PeerRegistry,
        *,
        peerstore: Any = None,
        event_limit: int = 500,
    ) -> None:
        self._registry = registry
        self.peerstore = peerstore
        self._events: deque[dict[str, Any]] = deque(maxlen=max(100, int(event_limit)))

    @property
    def connections(self) -> Dict[str, PeerSession]:
        return self._registry.sessions()

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def record_event(self, event: str, **payload: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {"at": time.time(), "event": event}
        entry.update(payload)
        self._events.append(entry)
        return entry

    def set_stage(self, session_id: str, stage: ConnectionStage) -> None:
        session = self._registry.get_session(session_id)
        if session is None:
            return
        current_value = session.stage or ConnectionStage.DIALING.value
        current_enum = (
            ConnectionStage(current_value)
            if current_value in ConnectionStage._value2member_map_
            else ConnectionStage.DIALING
        )
        current_rank = _STAGE_ORDER.get(current_enum, 0)
        next_rank = _STAGE_ORDER.get(stage, 0)
        if next_rank < current_rank:
            return
        session.stage = stage.value
        session.last_seen = time.time()

    def counts(self) -> RuntimeCounts:
        sessions = list(self._registry.sessions().values())
        total = len(sessions)
        connected_sessions = [
            s
            for s in sessions
            if s.identity_ok
            and s.state == PeerState.CONNECTED
            and s.stage == ConnectionStage.PEER_READY.value
        ]
        connected_total = len(connected_sessions)
        connected_inbound = sum(
            1 for s in connected_sessions if s.direction == "inbound"
        )
        connected_outbound = sum(
            1 for s in connected_sessions if s.direction == "outbound"
        )
        handshaking = sum(
            1
            for s in sessions
            if s.state not in (PeerState.FAILED, PeerState.DISCONNECTED)
            and s.stage != ConnectionStage.PEER_READY.value
        )
        return RuntimeCounts(
            total=total,
            connected_total=connected_total,
            connected_inbound=connected_inbound,
            connected_outbound=connected_outbound,
            handshaking=handshaking,
        )


__all__ = ["ConnectionStage", "P2PRuntime", "RuntimeCounts"]
