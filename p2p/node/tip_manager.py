"""
Peer tip exchange and tracking manager.

This module manages peer tip (head status) information:
- Periodic polling of peer tips to keep them fresh
- Storing received tip updates in PeerRegistry
- Computing best tip across the network
- Tip freshness tracking

Integrates with handshake flow: after identity validation, immediately requests
peer's head status.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

from .peer_registry import PeerRegistry, PeerState

log = logging.getLogger(__name__)


class TipManager:
    """
    Manages peer tip exchange and freshness tracking.
    
    Responsibilities:
    - Poll peers periodically for head status updates
    - Store received tips in PeerRegistry
    - Compute network best tip from fresh peer data
    - Track tip staleness and initiate refreshes
    
    Args:
        registry: PeerRegistry instance for peer state access
        poll_interval_s: Interval between tip polls (default 30s)
        freshness_window_s: Window for considering tips fresh (default 600s)
    """
    
    def __init__(
        self,
        registry: PeerRegistry,
        *,
        poll_interval_s: float = 30.0,
        freshness_window_s: float = 600.0,
    ):
        self._registry = registry
        self._poll_interval_s = poll_interval_s
        self._freshness_window_s = freshness_window_s
        
        # Track last poll attempt per session (session_id -> timestamp)
        self._last_poll_at: Dict[str, float] = {}
    
    def on_handshake_complete(self, session_id: str) -> bool:
        """
        Called after identity validation succeeds. Marks that initial tip request is needed.
        
        Args:
            session_id: Session identifier
        
        Returns:
            True if tip request should be sent, False otherwise
        """
        # Don't record a poll time yet - caller will send the request
        # This allows poll_peer_tips() to distinguish initial vs. refresh polls
        return True
    
    def on_tip_received(
        self,
        session_id: str,
        height: int,
        hash_hex: Optional[str] = None,
        tip_time: Optional[float] = None,
    ) -> None:
        """
        Process received tip (HeadStatus) from peer.
        
        Args:
            session_id: Session identifier
            height: Peer's head height
            hash_hex: Peer's head hash (hex string, optional)
            tip_time: Peer's local timestamp for the tip (optional)
        """
        # Update tip in registry
        self._registry.update_peer_tip(
            session_id,
            height=height,
            hash_hex=hash_hex,
            tip_time=tip_time,
        )
        
        # Record successful poll
        self._last_poll_at[session_id] = time.time()
        
        # Get session info for logging
        session = self._registry._sessions.get(session_id)
        if not session:
            return
        
        # Calculate age of tip
        now = time.time()
        age = None
        if session.tip_updated_at:
            age = now - session.tip_updated_at
        
        log.info(
            "Peer tip updated",
            extra={
                "session_id": session_id,
                "remote": session.remote,
                "peer_id": session.peer_id or "(unknown)",
                "height": height,
                "hash": (hash_hex[:16] + "...") if hash_hex else "none",
                "age_s": f"{age:.1f}" if age is not None else "new",
            }
        )
    
    def poll_peer_tips(self, now: Optional[float] = None) -> List[str]:
        """
        Identify peers that need tip refresh and return their session_ids.
        
        Returns session_ids for CONNECTED peers whose tips are stale (older than poll_interval_s).
        
        Args:
            now: Current timestamp (defaults to time.time())
        
        Returns:
            List of session_ids that need tip polling
        """
        now = now or time.time()
        to_poll: List[str] = []
        
        for session_id, session in self._registry._sessions.items():
            # Only poll CONNECTED peers with validated identity
            if not session.identity_ok:
                continue
            if session.state != PeerState.CONNECTED:
                continue
            
            # Check if we've polled recently
            last_poll = self._last_poll_at.get(session_id)
            if last_poll is not None:
                elapsed = now - last_poll
                if elapsed < self._poll_interval_s:
                    continue
            
            # Check tip freshness
            if session.tip_updated_at:
                tip_age = now - session.tip_updated_at
                if tip_age < self._poll_interval_s:
                    # Tip is still fresh, no need to poll
                    continue
            
            to_poll.append(session_id)
        
        if to_poll:
            log.debug(
                "Polling peer tips",
                extra={
                    "count": len(to_poll),
                    "session_ids": to_poll[:5],  # Log first 5
                }
            )
        
        return to_poll
    
    def mark_poll_attempted(self, session_id: str, now: Optional[float] = None) -> None:
        """
        Mark that a tip poll was attempted for a peer.
        
        Args:
            session_id: Session identifier
            now: Timestamp of poll attempt (defaults to time.time())
        """
        now = now or time.time()
        self._last_poll_at[session_id] = now
    
    def mark_poll_failed(
        self,
        session_id: str,
        reason: str,
        now: Optional[float] = None
    ) -> None:
        """
        Mark that a tip poll failed for a peer.
        
        Args:
            session_id: Session identifier
            reason: Failure reason
            now: Timestamp (defaults to time.time())
        """
        now = now or time.time()
        self._last_poll_at[session_id] = now
        
        session = self._registry._sessions.get(session_id)
        if session:
            log.debug(
                "Tip poll failed",
                extra={
                    "session_id": session_id,
                    "remote": session.remote,
                    "peer_id": session.peer_id or "(unknown)",
                    "reason": reason,
                }
            )
    
    def get_best_tip(self) -> Tuple[Optional[int], Optional[str], Optional[str], Optional[float]]:
        """
        Get the best (highest) peer tip from fresh tips.
        
        Delegates to PeerRegistry.get_best_peer_tip() with configured freshness window.
        
        Returns:
            Tuple of (height, hash_hex, peer_id, age_seconds) or (None, None, None, None)
        """
        return self._registry.get_best_peer_tip(freshness_window_s=self._freshness_window_s)
    
    def get_tip_stats(self) -> Tuple[int, int, int]:
        """
        Get peer tip statistics: (total, fresh, stale).
        
        Delegates to PeerRegistry.get_peer_tips() with configured freshness window.
        
        Returns:
            Tuple of (total_tips, fresh_tips, stale_tips)
        """
        return self._registry.get_peer_tips(freshness_window_s=self._freshness_window_s)
    
    def cleanup_session(self, session_id: str) -> None:
        """
        Remove tip tracking state for a disconnected session.
        
        Args:
            session_id: Session identifier
        """
        self._last_poll_at.pop(session_id, None)


__all__ = ["TipManager"]
