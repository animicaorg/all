from __future__ import annotations

import ipaddress
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _extract_ip(remote: str) -> str:
    """
    Best-effort parser to extract an IP/host portion from a remote address string.

    Accepts tcp://host:port, host:port, or bare host strings. Falls back to the
    full remote string if parsing fails so limits still apply deterministically.
    """
    try:
        if "://" in remote:
            remote = remote.split("://", 1)[1]
        if remote.startswith("[") and "]" in remote:
            host = remote.split("]", 1)[0].lstrip("[")
            return str(ipaddress.ip_address(host))
        if ":" in remote:
            host = remote.rsplit(":", 1)[0]
        else:
            host = remote
        return str(ipaddress.ip_address(host))
    except Exception:
        # Unknown/invalid host - use the raw string so limits still function.
        return remote


@dataclass
class PeerSession:
    session_id: str
    remote: str
    direction: str
    connected_at: float = field(default_factory=time.time)
    peer_id: Optional[str] = None
    last_seen: float = field(default_factory=time.time)
    meta: Dict[str, object] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, object]:
        snap = {
            "remote": self.remote,
            "direction": self.direction,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "peer_id": self.peer_id or "unknown",
        }
        if self.meta:
            snap.update(self.meta)
        return snap


class PeerRegistry:
    """
    Authoritative registry of active peer sessions.

    Responsibilities:
      - Enforce per-IP inbound limits.
      - Rate-limit inbound handshakes per IP and netgroup.
      - Deduplicate by peer_id (keep the newest connection).
      - Track handshake timeouts for unknown peer_ids.
    """

    def __init__(
        self,
        *,
        max_inbound_per_ip: int = 8,
        handshake_timeout_s: float = 3.0,
        handshake_rate_limit_per_ip: int = 30,
        handshake_rate_limit_per_netgroup: int = 120,
        handshake_rate_window_s: float = 60.0,
        handshake_rate_netgroup_v4_bits: int = 24,
        handshake_rate_netgroup_v6_bits: int = 48,
    ) -> None:
        self._sessions: Dict[str, PeerSession] = {}
        self._sessions_by_peer_key: Dict[tuple[str, str], PeerSession] = {}
        self._max_inbound_per_ip = max(1, int(max_inbound_per_ip))
        self.handshake_timeout_s = max(0.01, float(handshake_timeout_s))
        self._handshake_rate_limit_per_ip = max(0, int(handshake_rate_limit_per_ip))
        self._handshake_rate_limit_per_netgroup = max(
            0, int(handshake_rate_limit_per_netgroup)
        )
        self._handshake_rate_window_s = max(0.1, float(handshake_rate_window_s))
        self._handshake_rate_netgroup_v4_bits = max(
            1, min(32, int(handshake_rate_netgroup_v4_bits))
        )
        self._handshake_rate_netgroup_v6_bits = max(
            1, min(128, int(handshake_rate_netgroup_v6_bits))
        )
        self._handshake_rate_ip: Dict[str, List[float]] = {}
        self._handshake_rate_netgroup: Dict[str, List[float]] = {}

    # --------------------------- registration --------------------------- #

    def register(self, remote: str, direction: str) -> PeerSession:
        """
        Register a pending connection. Raises ValueError when inbound limits are exceeded.
        """
        if direction == "inbound":
            ip = _extract_ip(remote)
            self._enforce_handshake_rate(ip)
            if self._inbound_count(ip) >= self._max_inbound_per_ip:
                raise ValueError(f"inbound limit reached for {ip}")

        session = PeerSession(
            session_id=str(uuid.uuid4()),
            remote=remote,
            direction=direction,
        )
        self._sessions[session.session_id] = session
        return session

    def mark_identified(self, session_id: str, peer_id: str) -> List[str]:
        """
        Attach a peer_id to a session. Returns a list of session_ids that should be dropped
        because a newer connection replaced them. Tracks one inbound and one outbound
        session per peer_id.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return []

        session.peer_id = peer_id
        session.last_seen = time.time()

        replaced: List[str] = []
        peer_key = (peer_id, session.direction)
        existing = self._sessions_by_peer_key.get(peer_key)
        if existing and existing.session_id != session_id:
            # Keep the newest connection per direction.
            if existing.connected_at <= session.connected_at:
                replaced.append(existing.session_id)
                self._sessions_by_peer_key[peer_key] = session
            else:
                # Older connection is newer than this one; drop the current session instead.
                replaced.append(session.session_id)
        else:
            self._sessions_by_peer_key[peer_key] = session

        for rid in replaced:
            if rid != session.session_id:
                self.remove(rid)

        return list(dict.fromkeys(replaced))

    def update_meta(self, session_id: str, **meta: object) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.meta.update(meta)
        session.last_seen = time.time()

    def mark_seen(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.last_seen = time.time()

    # --------------------------- removal --------------------------- #

    def remove(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session and session.peer_id:
            peer_key = (session.peer_id, session.direction)
            mapped = self._sessions_by_peer_key.get(peer_key)
            if mapped and mapped.session_id == session_id:
                self._sessions_by_peer_key.pop(peer_key, None)

    def purge_stale(self, now: Optional[float] = None) -> List[str]:
        """
        Remove sessions that never completed a handshake within the timeout window.
        Returns the list of session_ids that were purged.
        """
        now = now or time.time()
        expired: List[str] = []
        for session_id, session in list(self._sessions.items()):
            if session.peer_id:
                continue
            if now - session.connected_at >= self.handshake_timeout_s:
                expired.append(session_id)
                self.remove(session_id)
        return expired

    # --------------------------- queries --------------------------- #

    def peer_count(self) -> int:
        """
        Count active sessions with completed handshakes.
        """
        identified = set(self._sessions_by_peer_key)
        return len(identified)

    def snapshot(self) -> List[Dict[str, object]]:
        """
        Return a deduplicated list of peer snapshots for RPC/CLI consumption.

        Keeps at most one inbound and one outbound session per peer_id while
        retaining anonymous sessions for in-progress handshakes.
        """
        snapshots: Dict[str, PeerSession] = {}
        for session in self._sessions.values():
            if session.peer_id:
                key = f"{session.peer_id}:{session.direction}"
            else:
                key = session.session_id
            snapshots[key] = session
        return [s.snapshot() for s in snapshots.values()]

    # --------------------------- helpers --------------------------- #

    def _inbound_count(self, ip: str) -> int:
        count = 0
        for session in self._sessions.values():
            if session.direction != "inbound":
                continue
            if _extract_ip(session.remote) == ip:
                count += 1
        return count

    def _enforce_handshake_rate(self, ip: str) -> None:
        now = time.time()
        if self._handshake_rate_limit_per_ip > 0:
            ip_events = self._handshake_rate_ip.setdefault(ip, [])
            self._prune_rate_window(ip_events, now)
            if len(ip_events) >= self._handshake_rate_limit_per_ip:
                raise ValueError(f"handshake rate limit reached for {ip}")
            ip_events.append(now)

        if self._handshake_rate_limit_per_netgroup > 0:
            netgroup = self._netgroup_key(ip)
            ng_events = self._handshake_rate_netgroup.setdefault(netgroup, [])
            self._prune_rate_window(ng_events, now)
            if len(ng_events) >= self._handshake_rate_limit_per_netgroup:
                raise ValueError(f"handshake netgroup rate limit reached for {netgroup}")
            ng_events.append(now)

    def _prune_rate_window(self, events: List[float], now: float) -> None:
        cutoff = now - self._handshake_rate_window_s
        while events and events[0] < cutoff:
            events.pop(0)

    def _netgroup_key(self, ip: str) -> str:
        try:
            addr = ipaddress.ip_address(ip)
        except Exception:
            return ip
        if addr.version == 4:
            bits = self._handshake_rate_netgroup_v4_bits
        else:
            bits = self._handshake_rate_netgroup_v6_bits
        net = ipaddress.ip_network(f"{addr}/{bits}", strict=False)
        return str(net)


__all__ = ["PeerRegistry", "PeerSession"]
