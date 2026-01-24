"""
Deterministic handshake manager for P2P connections.

This module extracts handshake orchestration from the monolithic p2p_service_legacy.py
and provides a clean state-machine based approach using PeerRegistry.

Handshake Flow:
    1. register(remote, direction) → session_id, state=DIALING
    2. mark_identified(session_id, peer_id) → state=HANDSHAKING
    3. on_hello_received() → validates protocol
    4. on_identity_received() → validates chain_id/genesis → state=CONNECTED or FAILED
    5. check_timeouts() → fails stuck handshakes

State Transitions:
    DIALING → HANDSHAKING (on peer_id received)
    HANDSHAKING → CONNECTED (on identity validation success)
    HANDSHAKING → FAILED (on identity validation failure)
    Any → FAILED (on timeout)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .peer_registry import PeerRegistry, PeerState

log = logging.getLogger(__name__)


@dataclass
class HandshakeSession:
    """Tracks handshake-specific state for a peer session."""
    session_id: str
    remote: str
    direction: str
    started_at: float = field(default_factory=time.time)
    
    # Handshake progress
    hello_sent_at: Optional[float] = None
    hello_received_at: Optional[float] = None
    
    # Identity validation fields
    peer_id: Optional[str] = None
    version: Optional[str] = None
    agent: Optional[str] = None
    chain_id: Optional[int] = None
    genesis_hash: Optional[str] = None
    
    # Metadata
    last_error: Optional[str] = None


class HandshakeManager:
    """
    Orchestrates deterministic handshake flow with timeouts and state transitions.
    
    Uses PeerRegistry for authoritative state tracking while managing handshake-specific
    logic (protocol validation, identity checks, timeouts).
    
    Args:
        registry: PeerRegistry instance for state management
        dial_timeout_s: Timeout for dial phase (default 8.0s)
        handshake_timeout_s: Timeout for complete handshake (default 15.0s)
        chain_id: Local chain ID for validation
        genesis_hash: Local genesis hash (hex) for validation
    """
    
    def __init__(
        self,
        registry: PeerRegistry,
        *,
        dial_timeout_s: float = 8.0,
        handshake_timeout_s: float = 15.0,
        chain_id: int,
        genesis_hash: str,
    ):
        self._registry = registry
        self._dial_timeout_s = dial_timeout_s
        self._handshake_timeout_s = handshake_timeout_s
        self._chain_id = chain_id
        self._genesis_hash = genesis_hash.lower() if genesis_hash else ""
        
        # Handshake-specific tracking (session_id -> HandshakeSession)
        self._sessions: Dict[str, HandshakeSession] = {}
    
    def start_handshake(self, remote: str, direction: str) -> str:
        """
        Register a new peer connection and start handshake tracking.
        
        Args:
            remote: Remote peer address (e.g., "tcp://host:port")
            direction: Connection direction ("inbound" or "outbound")
        
        Returns:
            session_id: Unique session identifier
        
        Raises:
            ValueError: If inbound limits are exceeded
        """
        # Register with PeerRegistry (enforces limits, creates session)
        session = self._registry.register(remote, direction)
        session_id = session.session_id
        
        # Create handshake tracking
        hs = HandshakeSession(
            session_id=session_id,
            remote=remote,
            direction=direction,
        )
        self._sessions[session_id] = hs
        
        # State is DIALING by default from registry
        log.info(
            "Handshake started",
            extra={
                "session_id": session_id,
                "remote": remote,
                "direction": direction,
                "state": PeerState.DIALING.value,
            }
        )
        
        return session_id

    def track_session(self, session_id: str, remote: str, direction: str) -> None:
        """
        Register an existing PeerRegistry session for handshake tracking.

        This is used when the caller creates registry sessions directly and still
        wants HandshakeManager to manage identity validation and timeouts.

        Args:
            session_id: Existing PeerRegistry session ID
            remote: Remote peer address (e.g., "tcp://host:port")
            direction: Connection direction ("inbound" or "outbound")
        """
        if session_id in self._sessions:
            return

        self._sessions[session_id] = HandshakeSession(
            session_id=session_id,
            remote=remote,
            direction=direction,
        )
        log.debug(
            "Handshake tracking registered for existing session",
            extra={
                "session_id": session_id,
                "remote": remote,
                "direction": direction,
            },
        )
    
    def on_hello_sent(self, session_id: str) -> None:
        """Mark that Hello message was sent to peer."""
        hs = self._sessions.get(session_id)
        if hs:
            hs.hello_sent_at = time.time()
    
    def on_hello_received(
        self,
        session_id: str,
        peer_id: str,
        version: str,
        agent: str,
    ) -> None:
        """
        Process received Hello message and transition to HANDSHAKING state.
        
        Args:
            session_id: Session identifier
            peer_id: Remote peer ID (hex)
            version: Protocol version string
            agent: Peer agent string
        """
        hs = self._sessions.get(session_id)
        if not hs:
            log.warning(
                "Hello received for unknown session",
                extra={"session_id": session_id}
            )
            return
        
        hs.hello_received_at = time.time()
        hs.peer_id = peer_id
        hs.version = version
        hs.agent = agent
        
        # Mark peer_id in registry (transitions DIALING → HANDSHAKING)
        replaced = self._registry.mark_identified(session_id, peer_id)
        if replaced:
            # Clean up replaced sessions from handshake tracking
            for replaced_id in replaced:
                if replaced_id in self._sessions:
                    del self._sessions[replaced_id]
        
        duration = hs.hello_received_at - hs.started_at
        log.info(
            "Hello received, peer identified",
            extra={
                "session_id": session_id,
                "remote": hs.remote,
                "peer_id": peer_id,
                "version": version,
                "agent": agent,
                "state": PeerState.HANDSHAKING.value,
                "duration_s": f"{duration:.3f}",
            }
        )
    
    def on_identity_received(
        self,
        session_id: str,
        chain_id: int,
        genesis_hash: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate peer identity (chain_id, genesis_hash) and transition to CONNECTED or FAILED.
        
        Args:
            session_id: Session identifier
            chain_id: Remote chain ID
            genesis_hash: Remote genesis hash (hex)
        
        Returns:
            Tuple of (success: bool, error_reason: Optional[str])
            - (True, None) on success
            - (False, reason) on validation failure
        """
        hs = self._sessions.get(session_id)
        if not hs:
            return (False, "session_not_found")
        
        hs.chain_id = chain_id
        hs.genesis_hash = genesis_hash.lower() if genesis_hash else ""
        
        # Validate chain_id
        if chain_id != self._chain_id:
            reason = "chain_id_mismatch"
            hs.last_error = reason
            
            self._registry.mark_identity_failed(
                session_id,
                reason=reason,
                chain_id=chain_id,
                genesis_hash=genesis_hash,
            )
            
            duration = time.time() - hs.started_at
            log.warning(
                "Identity validation failed: chain_id mismatch",
                extra={
                    "session_id": session_id,
                    "remote": hs.remote,
                    "peer_id": hs.peer_id,
                    "local_chain_id": self._chain_id,
                    "peer_chain_id": chain_id,
                    "state": PeerState.FAILED.value,
                    "duration_s": f"{duration:.3f}",
                }
            )
            return (False, reason)
        
        # Validate genesis_hash
        if self._genesis_hash and hs.genesis_hash != self._genesis_hash:
            reason = "genesis_hash_mismatch"
            hs.last_error = reason
            
            self._registry.mark_identity_failed(
                session_id,
                reason=reason,
                chain_id=chain_id,
                genesis_hash=genesis_hash,
            )
            
            duration = time.time() - hs.started_at
            log.warning(
                "Identity validation failed: genesis_hash mismatch",
                extra={
                    "session_id": session_id,
                    "remote": hs.remote,
                    "peer_id": hs.peer_id,
                    "local_genesis_hash": self._genesis_hash[:16] + "...",
                    "peer_genesis_hash": (hs.genesis_hash[:16] + "...") if hs.genesis_hash else "none",
                    "state": PeerState.FAILED.value,
                    "duration_s": f"{duration:.3f}",
                }
            )
            return (False, reason)
        
        # Identity validation successful
        self._registry.mark_identity_validated(
            session_id,
            chain_id=chain_id,
            genesis_hash=genesis_hash,
        )
        
        duration = time.time() - hs.started_at
        log.info(
            "Identity validated, handshake complete",
            extra={
                "session_id": session_id,
                "remote": hs.remote,
                "peer_id": hs.peer_id,
                "chain_id": chain_id,
                "state": PeerState.CONNECTED.value,
                "duration_s": f"{duration:.3f}",
            }
        )
        
        return (True, None)
    
    def check_timeouts(self, now: Optional[float] = None) -> List[str]:
        """
        Check for stuck handshakes and fail them with timeout errors.
        
        Args:
            now: Current timestamp (defaults to time.time())
        
        Returns:
            List of session_ids that timed out
        """
        now = now or time.time()
        timed_out: List[str] = []
        
        for session_id, hs in list(self._sessions.items()):
            # Skip already connected/failed sessions
            session = self._registry._sessions.get(session_id)
            if not session:
                # Session was removed, clean up
                del self._sessions[session_id]
                continue
            
            if session.state in (PeerState.CONNECTED, PeerState.FAILED):
                # Clean up completed handshakes
                del self._sessions[session_id]
                continue
            
            elapsed = now - hs.started_at
            
            # Check dial timeout (no Hello received yet)
            if session.state == PeerState.DIALING and elapsed >= self._dial_timeout_s:
                reason = "dial_timeout"
                hs.last_error = reason
                
                self._registry.mark_error(
                    session_id,
                    reason=reason,
                    penalty=1,
                )
                self._registry.transition_state(session_id, PeerState.FAILED)
                
                log.warning(
                    "Handshake failed: dial timeout",
                    extra={
                        "session_id": session_id,
                        "remote": hs.remote,
                        "direction": hs.direction,
                        "elapsed_s": f"{elapsed:.3f}",
                        "timeout_s": self._dial_timeout_s,
                        "state": PeerState.FAILED.value,
                    }
                )
                
                timed_out.append(session_id)
                continue
            
            # Check handshake timeout (Hello received but identity not validated)
            if session.state == PeerState.HANDSHAKING and elapsed >= self._handshake_timeout_s:
                reason = "handshake_timeout"
                hs.last_error = reason
                
                self._registry.mark_error(
                    session_id,
                    reason=reason,
                    penalty=1,
                )
                self._registry.transition_state(session_id, PeerState.FAILED)
                
                log.warning(
                    "Handshake failed: handshake timeout",
                    extra={
                        "session_id": session_id,
                        "remote": hs.remote,
                        "peer_id": hs.peer_id,
                        "direction": hs.direction,
                        "elapsed_s": f"{elapsed:.3f}",
                        "timeout_s": self._handshake_timeout_s,
                        "state": PeerState.FAILED.value,
                    }
                )
                
                timed_out.append(session_id)
                continue
        
        return timed_out
    
    def get_session(self, session_id: str) -> Optional[HandshakeSession]:
        """Get handshake session by ID."""
        return self._sessions.get(session_id)
    
    def remove_session(self, session_id: str) -> None:
        """Remove handshake tracking for a session."""
        self._sessions.pop(session_id, None)
    
    def active_handshakes(self) -> int:
        """Get count of active handshakes (not yet CONNECTED or FAILED)."""
        return len(self._sessions)


__all__ = ["HandshakeManager", "HandshakeSession"]
