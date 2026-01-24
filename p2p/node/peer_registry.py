from __future__ import annotations

import ipaddress
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
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


class PeerState(str, Enum):
    """Canonical peer connection states."""
    DIALING = "DIALING"
    TCP_CONNECTED = "TCP_CONNECTED"
    HANDSHAKING = "HANDSHAKING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"
    DISCONNECTED = "DISCONNECTED"


@dataclass
class PeerSession:
    session_id: str
    remote: str
    direction: str
    connected_at: float = field(default_factory=time.time)
    peer_id: Optional[str] = None
    last_seen: float = field(default_factory=time.time)
    meta: Dict[str, object] = field(default_factory=dict)
    stage: str = "DIALING"
    
    # State machine fields
    state: PeerState = PeerState.DIALING
    state_since: float = field(default_factory=time.time)
    
    # Identity validation fields
    identity_ok: bool = False
    remote_chain_id: Optional[int] = None
    remote_genesis_hash: Optional[str] = None
    
    # Peer tip/capability fields
    tip_height: Optional[int] = None
    tip_hash: Optional[str] = None
    tip_time: Optional[float] = None
    tip_updated_at: Optional[float] = None
    
    # Error tracking fields
    last_error: Optional[str] = None
    last_error_at: Optional[float] = None
    penalty_score: int = 0
    retry_count: int = 0
    next_retry_at: Optional[float] = None

    def snapshot(self) -> Dict[str, object]:
        snap = {
            "remote": self.remote,
            "direction": self.direction,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "peer_id": self.peer_id or "(handshaking)",
            "state": self.state.value,
            "stage": self.stage,
            "state_since": self.state_since,
            "identity_ok": self.identity_ok,
        }
        if self.remote_chain_id is not None:
            snap["remote_chain_id"] = self.remote_chain_id
        if self.remote_genesis_hash is not None:
            snap["remote_genesis_hash"] = self.remote_genesis_hash
        if self.tip_height is not None:
            snap["tip_height"] = self.tip_height
        if self.tip_hash is not None:
            snap["tip_hash"] = self.tip_hash
        if self.tip_time is not None:
            snap["tip_time"] = self.tip_time
        if self.tip_updated_at is not None:
            snap["tip_updated_at"] = self.tip_updated_at
        if self.last_error is not None:
            snap["last_error"] = self.last_error
        if self.last_error_at is not None:
            snap["last_error_at"] = self.last_error_at
        if self.penalty_score > 0:
            snap["penalty_score"] = self.penalty_score
        if self.retry_count > 0:
            snap["retry_count"] = self.retry_count
        if self.next_retry_at is not None:
            snap["next_retry_at"] = self.next_retry_at
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
      - Track handshake timeouts for peers still handshaking.
    """

    def __init__(
        self,
        *,
        max_inbound_per_ip: int = 8,
        handshake_timeout_s: float = 8.0,
        handshake_rate_limit_per_ip: int = 30,
        handshake_rate_limit_per_netgroup: int = 120,
        handshake_rate_window_s: float = 60.0,
        handshake_rate_netgroup_v4_bits: int = 24,
        handshake_rate_netgroup_v6_bits: int = 48,
    ) -> None:
        self._sessions: Dict[str, PeerSession] = {}
        self._sessions_by_peer_key: Dict[tuple[str, str], PeerSession] = {}
        self._max_inbound_per_ip = max(1, int(max_inbound_per_ip))
        self.handshake_timeout_s = max(5.0, float(handshake_timeout_s))
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

    def get_session(self, session_id: str) -> Optional[PeerSession]:
        return self._sessions.get(session_id)

    def sessions(self) -> Dict[str, PeerSession]:
        return dict(self._sessions)

    def transition_state(self, session_id: str, new_state: PeerState) -> None:
        """
        Transition a peer session to a new state.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.state = new_state
        session.state_since = time.time()
        session.last_seen = time.time()

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
        # Transition to HANDSHAKING state when peer_id is identified
        if session.state in (PeerState.DIALING, PeerState.TCP_CONNECTED):
            session.state = PeerState.HANDSHAKING
            session.state_since = time.time()
            session.stage = "HELLO_RECEIVED"

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
    
    def mark_identity_validated(
        self, 
        session_id: str, 
        *, 
        chain_id: int, 
        genesis_hash: str
    ) -> None:
        """
        Mark a peer session as having validated identity (chain_id and genesis_hash match).
        Transitions state to CONNECTED.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.identity_ok = True
        session.remote_chain_id = chain_id
        session.remote_genesis_hash = genesis_hash
        session.state = PeerState.CONNECTED
        session.state_since = time.time()
        session.last_seen = time.time()
        # Also update meta for backward compatibility
        session.meta["identity_ok"] = True

    def mark_tcp_connected(self, session_id: str) -> None:
        """
        Mark a peer session as having completed the TCP connection stage.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.state = PeerState.TCP_CONNECTED
        session.state_since = time.time()
        session.last_seen = time.time()
        session.stage = "TCP_CONNECTED"
    
    def mark_identity_failed(
        self,
        session_id: str,
        *,
        reason: str,
        chain_id: Optional[int] = None,
        genesis_hash: Optional[str] = None
    ) -> None:
        """
        Mark a peer session as having failed identity validation.
        Transitions state to FAILED.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.identity_ok = False
        session.remote_chain_id = chain_id
        session.remote_genesis_hash = genesis_hash
        session.state = PeerState.FAILED
        session.state_since = time.time()
        session.last_error = reason
        session.last_error_at = time.time()
        session.penalty_score += 1

    def mark_disconnected(self, session_id: str, *, reason: Optional[str] = None) -> None:
        """
        Mark a session as disconnected without removing it immediately.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.state = PeerState.DISCONNECTED
        session.state_since = time.time()
        session.last_error = reason
        session.last_error_at = time.time()
    
    def update_peer_tip(
        self,
        session_id: str,
        *,
        height: int,
        hash_hex: Optional[str] = None,
        tip_time: Optional[float] = None
    ) -> None:
        """
        Update the peer's tip (head) information.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.tip_height = height
        session.tip_hash = hash_hex
        session.tip_time = tip_time
        session.tip_updated_at = time.time()
        session.last_seen = time.time()
    
    def mark_error(
        self,
        session_id: str,
        *,
        reason: str,
        penalty: int = 1
    ) -> None:
        """
        Mark an error for a peer session and increase penalty score.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.last_error = reason
        session.last_error_at = time.time()
        session.penalty_score += penalty
        session.retry_count += 1
        # Exponential backoff: 2^retry_count seconds, capped at 300s (5 min)
        backoff = min(2 ** session.retry_count, 300)
        session.next_retry_at = time.time() + backoff

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
        Count active sessions with completed handshakes AND validated identity.
        
        Only counts peers that have:
        1. Completed handshake (peer_id assigned)
        2. Passed identity validation (identity_ok = True)
        3. State is CONNECTED
        
        This ensures "connected" peers reported to users have actually been
        fully verified (chain_id, genesis hash match, etc).
        
        Deduplicates by (peer_id, direction) to match snapshot() behavior.
        """
        # Deduplicate by (peer_id, direction) - same peer can have multiple connections
        seen_keys = set()
        for session in self._sessions.values():
            # Must have peer_id (handshake complete)
            if not session.peer_id:
                continue
            # Must have passed identity validation
            if not session.identity_ok:
                continue
            # Must be in CONNECTED state
            if session.state != PeerState.CONNECTED:
                continue
            if session.stage != "PEER_READY":
                continue
            key = (session.peer_id, session.direction)
            seen_keys.add(key)
        
        return len(seen_keys)
    
    def get_peer_tips(
        self, *, freshness_window_s: float = 600.0
    ) -> tuple[int, int, int]:
        """
        Get peer tip statistics: (total, fresh, stale).
        
        Args:
            freshness_window_s: Tips updated within this window are considered fresh (default 600s = 10 min)
        
        Returns:
            Tuple of (total tips, fresh tips, stale tips)
        """
        now = time.time()
        total = 0
        fresh = 0
        stale = 0
        
        for session in self._sessions.values():
            if not session.identity_ok:
                continue
            if session.state != PeerState.CONNECTED:
                continue
            if session.stage != "PEER_READY":
                continue
            if session.tip_height is None:
                continue
            
            total += 1
            if session.tip_updated_at and (now - session.tip_updated_at) < freshness_window_s:
                fresh += 1
            else:
                stale += 1
        
        return (total, fresh, stale)
    
    def get_best_peer_tip(
        self, *, freshness_window_s: float = 600.0
    ) -> tuple[Optional[int], Optional[str], Optional[str], Optional[float]]:
        """
        Get the best (highest) peer tip from fresh tips only.
        
        Args:
            freshness_window_s: Only consider tips updated within this window
        
        Returns:
            Tuple of (height, hash, peer_id, age_seconds) or (None, None, None, None) if no fresh tips
        """
        now = time.time()
        best_height = None
        best_hash = None
        best_peer = None
        best_age = None
        
        for session in self._sessions.values():
            if not session.identity_ok:
                continue
            if session.state != PeerState.CONNECTED:
                continue
            if session.stage != "PEER_READY":
                continue
            if session.tip_height is None:
                continue
            if not session.tip_updated_at:
                continue
            
            age = now - session.tip_updated_at
            if age >= freshness_window_s:
                continue
            
            if best_height is None or session.tip_height > best_height:
                best_height = session.tip_height
                best_hash = session.tip_hash
                best_peer = session.peer_id or session.remote
                best_age = age
        
        return (best_height, best_hash, best_peer, best_age)

    def total_active_sessions(self, *, include_handshaking: bool = True) -> int:
        """
        Count all active peer sessions, optionally including those still handshaking.
        
        Args:
            include_handshaking: If True (default), counts peers in handshaking state.
                                 If False, only counts peers with completed handshakes.
        
        Returns:
            Total number of active sessions. When include_handshaking=True, this includes
            peers that are connecting but haven't completed identity exchange yet.
            
        Note:
            This method counts raw sessions without deduplication or identity validation.
            Use peer_count() if you need validated, deduplicated peer counts.
        """
        if not include_handshaking:
            # Count only sessions with peer_id assigned (handshake complete)
            return sum(1 for s in self._sessions.values() if s.peer_id)
        
        # Count all active sessions including handshaking
        return len(self._sessions)

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

    def get_connected_peers_for_sync(self) -> List[Dict[str, object]]:
        """
        Get list of connected peers suitable for sync operations.
        Returns peers with identity_ok=True and state=CONNECTED.
        """
        peers = []
        for session in self._sessions.values():
            if not session.identity_ok:
                continue
            if session.state != PeerState.CONNECTED:
                continue
            peers.append({
                "session_id": session.session_id,
                "peer_id": session.peer_id,
                "remote": session.remote,
                "direction": session.direction,
                "tip_height": session.tip_height,
                "tip_hash": session.tip_hash,
                "tip_updated_at": session.tip_updated_at,
                "last_error": session.last_error,
                "penalty_score": session.penalty_score,
            })
        return peers

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


__all__ = ["PeerRegistry", "PeerSession", "PeerState"]
