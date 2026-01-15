"""
Headers sync for P2P2.

Implements headers-first sync using block locators.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Tuple

from ..protocol import Message, MsgType, create_getheaders

logger = logging.getLogger(__name__)


class ChainStore(Protocol):
    """Interface to chain storage."""
    
    async def get_head_height(self) -> int:
        """Get current head height."""
        ...
    
    async def get_head_hash(self) -> str:
        """Get current head hash."""
        ...
    
    async def get_block_hash(self, height: int) -> Optional[str]:
        """Get block hash at height."""
        ...
    
    async def has_header(self, block_hash: str) -> bool:
        """Check if we have this header."""
        ...
    
    async def store_headers(self, headers: List[Dict]) -> bool:
        """Store headers. Returns True if successful."""
        ...
    
    async def get_header(self, block_hash: str) -> Optional[Dict]:
        """Get header by hash."""
        ...


@dataclass
class HeadersSyncConfig:
    """Configuration for headers sync."""
    batch_size: int = 2000
    request_timeout: float = 10.0
    max_concurrent_requests: int = 3


@dataclass
class HeadersSyncStats:
    """Statistics for headers sync."""
    started_at: float = field(default_factory=time.time)
    headers_received: int = 0
    headers_stored: int = 0
    requests_sent: int = 0
    timeouts: int = 0
    errors: int = 0


class HeadersSync:
    """
    Headers-first synchronization.
    
    Fetches headers from peers until we reach their advertised tip.
    """
    
    def __init__(
        self,
        config: HeadersSyncConfig,
        chain_store: ChainStore,
        peer_manager,
    ):
        self.config = config
        self.chain_store = chain_store
        self.peer_manager = peer_manager
        
        self.stats = HeadersSyncStats()
        
        # Request tracking
        self.pending_requests: Dict[str, asyncio.Future] = {}  # request_id -> Future[headers]
        
        # Target tracking
        self.target_height: Optional[int] = None
        self.target_hash: Optional[str] = None
        
        logger.info("Headers sync initialized")
    
    async def build_locator(self, from_height: int) -> List[str]:
        """
        Build block locator for getheaders.
        
        Uses exponential backoff: recent blocks, then sparse.
        """
        locator = []
        step = 1
        height = from_height
        
        while height > 0 and len(locator) < 10:
            block_hash = await self.chain_store.get_block_hash(height)
            if block_hash:
                locator.append(block_hash)
            
            height -= step
            if len(locator) > 10:
                step *= 2
        
        # Always include genesis
        genesis_hash = await self.chain_store.get_block_hash(0)
        if genesis_hash and genesis_hash not in locator:
            locator.append(genesis_hash)
        
        return locator
    
    async def fetch_headers(
        self,
        peer_id: str,
        target_height: Optional[int] = None,
    ) -> bool:
        """
        Fetch headers from a peer.
        
        Returns True if made progress, False if stalled/error.
        """
        try:
            # Get current head
            head_height = await self.chain_store.get_head_height()
            
            # Check if we're done
            if target_height and head_height >= target_height:
                logger.info(f"Headers sync complete: {head_height}")
                return True
            
            # Build locator
            locator = await self.build_locator(head_height)
            
            # Create request
            request_id = f"headers-{peer_id}-{time.time()}"
            getheaders = create_getheaders(
                locator=locator,
                stop=None,
                limit=self.config.batch_size,
            )
            getheaders.id = request_id
            
            # Create future for response
            future: asyncio.Future[List[Dict]] = asyncio.Future()
            self.pending_requests[request_id] = future
            
            # Send request
            if peer_id not in self.peer_manager.connections:
                logger.warning(f"Peer {peer_id} not connected")
                return False
            
            conn = self.peer_manager.connections[peer_id]
            await conn.send(getheaders)
            
            self.stats.requests_sent += 1
            logger.debug(f"Sent GETHEADERS to {peer_id} from height {head_height}")
            
            # Wait for response
            try:
                headers = await asyncio.wait_for(
                    future,
                    timeout=self.config.request_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(f"GETHEADERS timeout from {peer_id}")
                self.stats.timeouts += 1
                return False
            finally:
                if request_id in self.pending_requests:
                    del self.pending_requests[request_id]
            
            if not headers:
                logger.debug(f"No headers received from {peer_id}")
                return False
            
            self.stats.headers_received += len(headers)
            
            # Store headers
            success = await self.chain_store.store_headers(headers)
            if success:
                self.stats.headers_stored += len(headers)
                logger.info(f"Stored {len(headers)} headers (now at {head_height + len(headers)})")
                
                # Update peer score
                if peer_id in self.peer_manager.peers:
                    peer = self.peer_manager.peers[peer_id]
                    peer.score.headers_delivered += len(headers)
                    peer.score.add_good_behavior(0.5)
                
                return True
            else:
                logger.error(f"Failed to store headers from {peer_id}")
                self.stats.errors += 1
                
                # Penalize peer
                if peer_id in self.peer_manager.peers:
                    peer = self.peer_manager.peers[peer_id]
                    peer.score.add_bad_behavior(2.0)
                
                return False
        
        except Exception as e:
            logger.error(f"Headers fetch error from {peer_id}: {e}")
            self.stats.errors += 1
            return False
    
    async def handle_headers(self, peer_id: str, msg: Message):
        """Handle HEADERS response."""
        request_id = msg.id
        if not request_id or request_id not in self.pending_requests:
            logger.debug(f"Unexpected HEADERS from {peer_id}")
            return
        
        # Extract headers
        headers = msg.payload.get("headers", [])
        
        # Resolve future
        future = self.pending_requests[request_id]
        if not future.done():
            future.set_result(headers)
    
    async def sync_to_peer(self, peer_id: str, target_height: int) -> bool:
        """
        Sync headers to a specific peer's advertised height.
        
        Returns True if fully synced.
        """
        logger.info(f"Starting headers sync to height {target_height} with {peer_id}")
        
        self.target_height = target_height
        
        while True:
            head_height = await self.chain_store.get_head_height()
            
            if head_height >= target_height:
                logger.info(f"Headers sync complete at height {head_height}")
                return True
            
            # Fetch batch
            success = await self.fetch_headers(peer_id, target_height)
            if not success:
                logger.warning(f"Headers sync stalled at height {head_height}")
                return False
            
            # Small delay to avoid overwhelming
            await asyncio.sleep(0.01)
