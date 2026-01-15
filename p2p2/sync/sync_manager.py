"""
Sync manager for P2P2.

Coordinates headers-first sync followed by blocks sync.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from .headers import HeadersSync, HeadersSyncConfig
from .blocks import BlocksSync, BlocksSyncConfig

logger = logging.getLogger(__name__)


@dataclass
class SyncManagerConfig:
    """Configuration for sync manager."""
    headers_config: HeadersSyncConfig
    blocks_config: BlocksSyncConfig
    sync_interval: float = 5.0  # Seconds between sync attempts
    stall_timeout: float = 60.0  # Seconds before declaring stall


class SyncManager:
    """
    Overall sync coordinator.
    
    Implements 2-phase sync:
    Phase A: Headers sync to peer's tip
    Phase B: Blocks sync in height order
    """
    
    def __init__(
        self,
        config: SyncManagerConfig,
        chain_store,
        peer_manager,
    ):
        self.config = config
        self.chain_store = chain_store
        self.peer_manager = peer_manager
        
        # Sub-components
        self.headers_sync = HeadersSync(
            config=config.headers_config,
            chain_store=chain_store,
            peer_manager=peer_manager,
        )
        
        self.blocks_sync = BlocksSync(
            config=config.blocks_config,
            chain_store=chain_store,
            peer_manager=peer_manager,
        )
        
        # State
        self.is_syncing = False
        self.target_height: Optional[int] = None
        self.last_progress_height = 0
        self.last_progress_time = time.time()
        
        # Background task
        self._sync_task: Optional[asyncio.Task] = None
        
        logger.info("Sync manager initialized")
    
    async def start(self):
        """Start sync manager."""
        await self.blocks_sync.start()
        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info("Sync manager started")
    
    async def stop(self):
        """Stop sync manager."""
        await self.blocks_sync.stop()
        if self._sync_task:
            self._sync_task.cancel()
        logger.info("Sync manager stopped")
    
    async def _sync_loop(self):
        """Main sync loop."""
        while True:
            try:
                await asyncio.sleep(self.config.sync_interval)
                
                if self.is_syncing:
                    continue
                
                # Check if we need to sync
                await self._check_and_sync()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sync loop error: {e}")
                await asyncio.sleep(5)
    
    async def _check_and_sync(self):
        """Check if we're behind and trigger sync."""
        # Get our height
        our_height = await self.chain_store.get_head_height()
        
        # Find best peer
        best_peer = self._select_sync_peer()
        if not best_peer:
            return
        
        # Check peer's advertised height
        peer_height = best_peer.info.best_height if best_peer.info else 0
        
        if peer_height > our_height + 5:
            logger.info(f"Behind peer {best_peer.peer_id} (us={our_height}, peer={peer_height})")
            await self.sync_to_peer(best_peer.peer_id, peer_height)
    
    def _select_sync_peer(self):
        """Select best peer for syncing."""
        connected = self.peer_manager.get_connected_peers()
        if not connected:
            return None
        
        # Filter peers with valid info
        candidates = [p for p in connected if p.info and p.info.best_height > 0]
        if not candidates:
            return None
        
        # Sort by: score (desc), height (desc), rtt (asc)
        candidates.sort(
            key=lambda p: (
                -p.score.score,
                -p.info.best_height,
                p.rtt_ms if p.rtt_ms else 999999,
            )
        )
        
        return candidates[0]
    
    async def sync_to_peer(self, peer_id: str, target_height: int) -> bool:
        """
        Sync to a peer's advertised height.
        
        Phase A: Sync headers
        Phase B: Sync blocks
        """
        if self.is_syncing:
            logger.warning("Already syncing")
            return False
        
        self.is_syncing = True
        self.target_height = target_height
        self.last_progress_height = await self.chain_store.get_head_height()
        self.last_progress_time = time.time()
        
        try:
            logger.info(f"=== Starting sync to height {target_height} with {peer_id} ===")
            
            # Phase A: Headers
            logger.info("Phase A: Headers sync")
            headers_success = await self.headers_sync.sync_to_peer(peer_id, target_height)
            
            if not headers_success:
                logger.warning("Headers sync failed")
                return False
            
            logger.info("Phase A complete")
            
            # Phase B: Blocks
            logger.info("Phase B: Blocks sync")
            blocks_success = await self._sync_blocks(peer_id, target_height)
            
            if not blocks_success:
                logger.warning("Blocks sync failed")
                return False
            
            logger.info("Phase B complete")
            logger.info(f"=== Sync complete to height {target_height} ===")
            return True
            
        finally:
            self.is_syncing = False
    
    async def _sync_blocks(self, peer_id: str, target_height: int) -> bool:
        """Sync blocks in windows."""
        start_time = time.time()
        
        while True:
            # Check timeout
            if time.time() - start_time > 3600:  # 1 hour max
                logger.error("Blocks sync timeout (1 hour)")
                return False
            
            # Get current height
            current_height = await self.chain_store.get_head_height()
            
            # Check if done
            if current_height >= target_height:
                logger.info(f"Blocks sync complete at height {current_height}")
                return True
            
            # Check stall
            if current_height > self.last_progress_height:
                self.last_progress_height = current_height
                self.last_progress_time = time.time()
            elif time.time() - self.last_progress_time > self.config.stall_timeout:
                logger.warning(f"Blocks sync stalled at height {current_height}")
                
                # Try to recover from orphans
                orphan_count = self.blocks_sync.orphan_pool.size()
                if orphan_count > 0:
                    logger.info(f"Attempting orphan recovery ({orphan_count} orphans)")
                    await self._request_missing_parents(peer_id)
                    await asyncio.sleep(10)
                    continue
                
                return False
            
            # Sync next window
            window_size = min(
                self.config.blocks_config.window_size,
                target_height - current_height,
            )
            
            window_end = current_height + window_size
            
            logger.info(f"Syncing window {current_height + 1} to {window_end} ({window_size} blocks)")
            
            success = await self.blocks_sync.sync_window(
                start_height=current_height + 1,
                end_height=window_end,
                peer_id=peer_id,
            )
            
            if not success:
                logger.warning(f"Window sync failed, rotating peer")
                
                # Try different peer
                new_peer = self._select_sync_peer()
                if new_peer and new_peer.peer_id != peer_id:
                    peer_id = new_peer.peer_id
                    logger.info(f"Switched to peer {peer_id}")
                
                await asyncio.sleep(5)
            
            # Small delay
            await asyncio.sleep(0.1)
    
    async def _request_missing_parents(self, peer_id: str):
        """Request all missing parents from orphan pool."""
        missing = self.blocks_sync.orphan_pool.get_missing_parents()
        if missing:
            logger.info(f"Requesting {len(missing)} missing parents")
            await self.blocks_sync.request_blocks(list(missing), peer_id)
    
    def get_status(self) -> dict:
        """Get sync status for introspection."""
        return {
            "is_syncing": self.is_syncing,
            "target_height": self.target_height,
            "headers_stats": {
                "headers_received": self.headers_sync.stats.headers_received,
                "headers_stored": self.headers_sync.stats.headers_stored,
                "requests_sent": self.headers_sync.stats.requests_sent,
                "timeouts": self.headers_sync.stats.timeouts,
            },
            "blocks_stats": {
                "blocks_received": self.blocks_sync.stats.blocks_received,
                "blocks_stored": self.blocks_sync.stats.blocks_stored,
                "blocks_requested": self.blocks_sync.stats.blocks_requested,
                "orphans_received": self.blocks_sync.stats.orphans_received,
                "orphans_resolved": self.blocks_sync.stats.orphans_resolved,
                "parent_backfills": self.blocks_sync.stats.parent_backfills,
            },
            "orphan_pool_size": self.blocks_sync.orphan_pool.size(),
            "inflight_blocks": len(self.blocks_sync.inflight),
        }
