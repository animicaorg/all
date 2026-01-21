"""
Block announce handler for Phase 6: Block Gossip/Propagation.

This handler manages block announcement broadcasting and reception:
- Broadcasts HEAD_STATUS announcements when new blocks are accepted
- Handles incoming HEAD_STATUS announcements from peers
- Updates peer tips and triggers sync when behind
- Integrates with existing TipManager and sync mechanisms
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

try:
    from p2p.wire.message_ids import MsgID
    from p2p.wire import encoding as wire_codec
    from p2p.wire import messages as wire_msg
except Exception:  # pragma: no cover
    MsgID = wire_codec = wire_msg = None  # type: ignore

log = logging.getLogger(__name__)


@dataclass
class BlockAnnounceHandler:
    """
    Handler for block announcements (Phase 6).
    
    Responsibilities:
    - Subscribe to new head events and broadcast HEAD_STATUS
    - Handle incoming HEAD_STATUS from peers
    - Update peer tips via TipManager
    - Trigger sync when peer tips indicate we're behind
    
    Integrates with:
    - EventBus for NewHeadEvent subscriptions
    - TipManager for peer tip tracking
    - Gossip engine for HEAD_STATUS broadcasting
    - Router for direct HEAD_STATUS messages
    """
    
    cfg: Any  # P2PConfig
    codec: Any  # wire codec for encode/decode
    deps: Any  # NodeDeps (provides head_reader, block_io, etc.)
    gossip: Any  # GossipEngine
    tip_manager: Any  # TipManager
    registry: Any  # PeerRegistry
    events: Any  # EventBus
    
    # Internal state
    _metrics: dict = field(default_factory=dict, init=False)
    _subscribed: bool = field(default=False, init=False)
    _broadcast_task: Optional[asyncio.Task] = field(default=None, init=False)
    _last_broadcast_height: int = field(default=0, init=False)
    
    def __post_init__(self) -> None:
        """Initialize metrics."""
        self._metrics = {
            "announcements_sent": 0,
            "announcements_received": 0,
            "peer_tips_updated": 0,
            "sync_triggered": 0,
        }
        log.info("BlockAnnounceHandler initialized")
    
    def msg_ids(self) -> Iterable[int]:
        """Message IDs this handler accepts (wire-level)."""
        if MsgID is None:
            return []
        return [MsgID.HEAD_STATUS]
    
    async def handle(self, conn: "ConnLike", frame: "Frame") -> None:
        """
        Handle incoming HEAD_STATUS message from a peer.
        
        Args:
            conn: Connection wrapper
            frame: Received frame
        """
        if MsgID is None or wire_msg is None:
            log.warning("Wire protocol not available, skipping HEAD_STATUS handling")
            return
        
        if frame.msg_id != MsgID.HEAD_STATUS:
            log.debug(f"Unexpected msg_id={frame.msg_id} in BlockAnnounceHandler")
            return
        
        try:
            # Decode HEAD_STATUS message
            msg = self.codec.decode(frame.payload, wire_msg.HeadStatus)
            
            self._metrics["announcements_received"] += 1
            
            # Find session for this connection by remote address
            session_id = None
            if self.registry:
                for sid, session in self.registry._sessions.items():
                    if session.remote == conn.remote_addr:
                        session_id = sid
                        break
            
            if not session_id:
                # Fallback: use remote_addr as identifier
                session_id = conn.remote_addr
            
            # Update peer tip via TipManager
            if self.tip_manager:
                self.tip_manager.on_tip_received(
                    session_id=session_id,
                    height=msg.head_height,
                    hash_hex=msg.head_hash.hex() if msg.head_hash else None,
                    tip_time=msg.timestamp_ms / 1000.0 if msg.timestamp_ms else None,
                )
                self._metrics["peer_tips_updated"] += 1
            
            # Check if we need to trigger sync
            if self.deps and hasattr(self.deps, "head_reader"):
                try:
                    local_height = await self._get_local_height()
                    
                    # If peer is ahead, consider triggering sync
                    if msg.head_height > local_height + 2:  # Allow 2 block lag tolerance
                        log.info(
                            f"Peer ahead: local={local_height}, peer={msg.head_height}, "
                            f"triggering sync check",
                            extra={
                                "local_height": local_height,
                                "peer_height": msg.head_height,
                                "gap": msg.head_height - local_height,
                            }
                        )
                        # Emit sync trigger event
                        if self.events:
                            await self.events.emit("syncCheck", {
                                "reason": "peer_ahead",
                                "local_height": local_height,
                                "peer_height": msg.head_height,
                            })
                        self._metrics["sync_triggered"] += 1
                
                except Exception as e:
                    log.debug(f"Failed to check if sync needed: {e}")
        
        except Exception as e:
            log.warning(f"Failed to handle HEAD_STATUS from {conn.remote_addr}: {e}", exc_info=True)
    
    async def start(self) -> None:
        """
        Start the handler: subscribe to new head events and begin broadcasting.
        """
        if self._subscribed:
            return
        
        try:
            # Subscribe to new head events
            if self.events:
                # Create subscription for newHead events
                sub = self.events.subscribe("newHead")
                # Start background task to handle new head events
                self._broadcast_task = asyncio.create_task(self._broadcast_loop(sub))
                log.info("Subscribed to newHead events for block announcements")
            
            self._subscribed = True
        
        except Exception as e:
            log.error(f"Failed to start BlockAnnounceHandler: {e}", exc_info=True)
    
    async def stop(self) -> None:
        """Stop the handler and cancel background tasks."""
        self._subscribed = False
        
        if self._broadcast_task and not self._broadcast_task.done():
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None
        
        log.info("BlockAnnounceHandler stopped")
    
    async def _broadcast_loop(self, subscription: Any) -> None:
        """
        Background loop to broadcast HEAD_STATUS when new blocks are accepted.
        
        Args:
            subscription: Event subscription for newHead events
        """
        try:
            async for event in subscription:
                try:
                    # Extract height from event
                    height = event.get("height", 0) if isinstance(event, dict) else 0
                    
                    # Avoid duplicate broadcasts for same height
                    if height > 0 and height != self._last_broadcast_height:
                        await self._broadcast_head_status()
                        self._last_broadcast_height = height
                
                except Exception as e:
                    log.debug(f"Error processing newHead event: {e}")
        
        except asyncio.CancelledError:
            log.debug("Broadcast loop cancelled")
            raise
        except Exception as e:
            log.error(f"Broadcast loop error: {e}", exc_info=True)
    
    async def _broadcast_head_status(self) -> None:
        """
        Broadcast HEAD_STATUS announcement to all connected peers.
        """
        if not self._subscribed:
            return
        
        try:
            # Get local head info
            local_height, local_hash = await self._get_local_head()
            
            if local_height == 0:
                return  # Nothing to announce yet
            
            # Get chain_id
            chain_id = getattr(self.deps, "chain_id", 1337)
            
            # Build HEAD_STATUS message
            import time
            head_status = wire_msg.HeadStatus(
                chain_id=chain_id,
                head_height=local_height,
                head_hash=local_hash,
                timestamp_ms=int(time.time() * 1000),
                network_best_height=None,  # Could be populated from tip_manager
            )
            
            # Encode message
            payload = self.codec.encode(head_status)
            
            # Broadcast to all connected peers
            # Method 1: Via gossip engine (preferred for fanout)
            if self.gossip:
                try:
                    from p2p.gossip import topics as gossip_topics
                    blocks_topic = gossip_topics.blocks(chain_id)
                    await self.gossip.publish(blocks_topic.path, payload)
                except Exception as e:
                    log.debug(f"Failed to publish HEAD_STATUS via gossip: {e}")
            
            # Method 2: Direct send to all peers (via registry)
            if self.registry:
                await self._broadcast_to_peers(MsgID.HEAD_STATUS, payload)
            
            self._metrics["announcements_sent"] += 1
            log.debug(
                f"Broadcast HEAD_STATUS: height={local_height}, hash={local_hash.hex()[:16]}..."
            )
        
        except Exception as e:
            log.warning(f"Failed to broadcast HEAD_STATUS: {e}", exc_info=True)
    
    async def _broadcast_to_peers(self, msg_id: int, payload: bytes) -> None:
        """
        Send a message directly to all connected peers.
        
        Args:
            msg_id: Message ID
            payload: Encoded payload
        """
        if not self.registry:
            return
        
        # Import PeerState
        from p2p.node.peer_registry import PeerState
        
        # Get all connected sessions
        connected_sessions = []
        for session_id, session in self.registry._sessions.items():
            if session.state == PeerState.CONNECTED and session.identity_ok:
                connected_sessions.append((session_id, session))
        
        # Send to each peer
        tasks = []
        for session_id, session in connected_sessions:
            if hasattr(session, "conn") and session.conn:
                try:
                    task = asyncio.create_task(
                        session.conn.send_frame(msg_id, payload)
                    )
                    tasks.append(task)
                except Exception as e:
                    log.debug(f"Failed to send to {session.remote}: {e}")
        
        # Wait for all sends (best-effort)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _get_local_height(self) -> int:
        """Get local chain height."""
        if not self.deps or not hasattr(self.deps, "head_reader"):
            return 0
        
        try:
            head_reader = self.deps.head_reader
            if hasattr(head_reader, "get_canonical_height"):
                return await head_reader.get_canonical_height()
            elif callable(head_reader):
                result = await head_reader()
                if isinstance(result, tuple):
                    return result[0] if len(result) > 0 else 0
                return int(result) if result is not None else 0
        except Exception:
            pass
        
        return 0
    
    async def _get_local_head(self) -> tuple[int, bytes]:
        """Get local chain head (height, hash)."""
        if not self.deps or not hasattr(self.deps, "head_reader"):
            return (0, b"\x00" * 32)
        
        try:
            head_reader = self.deps.head_reader
            if hasattr(head_reader, "get_canonical_head"):
                result = await head_reader.get_canonical_head()
                if isinstance(result, tuple) and len(result) >= 2:
                    return (result[0], result[1])
            elif callable(head_reader):
                result = await head_reader()
                if isinstance(result, tuple) and len(result) >= 2:
                    return (result[0], result[1])
        except Exception:
            pass
        
        return (0, b"\x00" * 32)
    
    def get_metrics(self) -> dict:
        """Return current metrics snapshot."""
        return dict(self._metrics)


__all__ = [
    "BlockAnnounceHandler",
]
