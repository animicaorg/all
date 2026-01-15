"""
Storage interfaces for P2P2.

Adapters to connect P2P2 to existing chain storage.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ChainStoreAdapter:
    """
    Adapter to connect P2P2 sync to existing chain storage.
    
    This bridges to the existing core/db/block_db.py and related storage.
    """
    
    def __init__(self, block_db, state_db):
        """
        Initialize adapter.
        
        Args:
            block_db: Existing block database instance
            state_db: Existing state database instance
        """
        self.block_db = block_db
        self.state_db = state_db
    
    async def get_head_height(self) -> int:
        """Get current head height."""
        try:
            # Assuming block_db has a get_head_height() method
            if hasattr(self.block_db, 'get_head_height'):
                return await self.block_db.get_head_height()
            elif hasattr(self.block_db, 'get_latest_height'):
                return await self.block_db.get_latest_height()
            else:
                # Fallback: get head and extract height
                head = await self.block_db.get_head()
                return head.get('height', 0) if head else 0
        except Exception as e:
            logger.error(f"Failed to get head height: {e}")
            return 0
    
    async def get_head_hash(self) -> str:
        """Get current head hash."""
        try:
            head = await self.block_db.get_head()
            return head.get('hash', '') if head else ''
        except Exception as e:
            logger.error(f"Failed to get head hash: {e}")
            return ''
    
    async def get_block_hash(self, height: int) -> Optional[str]:
        """Get block hash at height."""
        try:
            block = await self.block_db.get_block_by_height(height)
            return block.get('hash') if block else None
        except Exception as e:
            logger.debug(f"Failed to get block hash at height {height}: {e}")
            return None
    
    async def has_header(self, block_hash: str) -> bool:
        """Check if we have this header."""
        try:
            if hasattr(self.block_db, 'has_header'):
                return await self.block_db.has_header(block_hash)
            else:
                # Fallback: try to get header
                header = await self.get_header(block_hash)
                return header is not None
        except Exception:
            return False
    
    async def has_block(self, block_hash: str) -> bool:
        """Check if we have this block."""
        try:
            if hasattr(self.block_db, 'has_block'):
                return await self.block_db.has_block(block_hash)
            else:
                # Fallback: try to get block
                block = await self.get_block(block_hash)
                return block is not None
        except Exception:
            return False
    
    async def get_header(self, block_hash: str) -> Optional[Dict]:
        """Get header by hash."""
        try:
            if hasattr(self.block_db, 'get_header'):
                return await self.block_db.get_header(block_hash)
            else:
                # Fallback: get full block and extract header
                block = await self.get_block(block_hash)
                if block:
                    # Extract header fields
                    return {
                        'hash': block.get('hash'),
                        'parent_hash': block.get('parent_hash'),
                        'height': block.get('height'),
                        'timestamp': block.get('timestamp'),
                    }
                return None
        except Exception as e:
            logger.debug(f"Failed to get header {block_hash[:8]}: {e}")
            return None
    
    async def get_block(self, block_hash: str) -> Optional[Dict]:
        """Get block by hash."""
        try:
            if hasattr(self.block_db, 'get_block_by_hash'):
                return await self.block_db.get_block_by_hash(block_hash)
            elif hasattr(self.block_db, 'get_block'):
                return await self.block_db.get_block(block_hash)
            else:
                logger.warning("No method to get block by hash")
                return None
        except Exception as e:
            logger.debug(f"Failed to get block {block_hash[:8]}: {e}")
            return None
    
    async def get_block_at_height(self, height: int) -> Optional[Dict]:
        """Get block at specific height."""
        try:
            if hasattr(self.block_db, 'get_block_by_height'):
                return await self.block_db.get_block_by_height(height)
            elif hasattr(self.block_db, 'get_block_at_height'):
                return await self.block_db.get_block_at_height(height)
            else:
                logger.warning("No method to get block by height")
                return None
        except Exception as e:
            logger.debug(f"Failed to get block at height {height}: {e}")
            return None
    
    async def store_headers(self, headers: List[Dict]) -> bool:
        """Store headers."""
        try:
            if hasattr(self.block_db, 'store_headers'):
                await self.block_db.store_headers(headers)
                return True
            else:
                # Fallback: store individually
                for header in headers:
                    if hasattr(self.block_db, 'store_header'):
                        await self.block_db.store_header(header)
                return True
        except Exception as e:
            logger.error(f"Failed to store {len(headers)} headers: {e}")
            return False
    
    async def store_block(self, block: Dict) -> bool:
        """Store a block."""
        try:
            if hasattr(self.block_db, 'store_block'):
                await self.block_db.store_block(block)
                return True
            elif hasattr(self.block_db, 'insert_block'):
                await self.block_db.insert_block(block)
                return True
            else:
                logger.warning("No method to store block")
                return False
        except Exception as e:
            logger.error(f"Failed to store block {block.get('hash', 'unknown')[:8]}: {e}")
            return False
