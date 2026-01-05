"""
Chain Monitor - Watches Animica blockchain for events
"""

import asyncio
import logging
from typing import Optional
import sys
import os

# Add repo root to path for imports
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from animica_bridge.config import settings

logger = logging.getLogger(__name__)


class ChainMonitor:
    """Monitors Animica blockchain for relevant events"""
    
    def __init__(self, rpc_url: str, chain_id: int):
        self.rpc_url = rpc_url
        self.chain_id = chain_id
        self.running = False
        self.last_block = 0
        
    async def run(self):
        """Main monitoring loop"""
        self.running = True
        logger.info("Chain monitor started")
        
        while self.running:
            try:
                await self._poll_events()
                await asyncio.sleep(settings.POLLING_INTERVAL)
            except Exception as e:
                logger.error(f"Chain monitoring error: {e}", exc_info=True)
                await asyncio.sleep(settings.POLLING_INTERVAL)
    
    async def _poll_events(self):
        """Poll for new events"""
        try:
            # TODO: Integrate with Animica RPC
            # from omni_sdk import AnimicaClient
            # client = AnimicaClient(self.rpc_url)
            # current_block = await client.get_block_number()
            
            # For now, use placeholder
            current_block = self.last_block + 1
            
            if current_block > self.last_block:
                logger.debug(f"Scanning blocks {self.last_block} to {current_block}")
                
                # Check for payment transactions
                await self._check_payment_events(self.last_block, current_block)
                
                # Check for job submissions
                await self._check_job_events(self.last_block, current_block)
                
                # Check for contributor registrations
                await self._check_contributor_events(self.last_block, current_block)
                
                self.last_block = current_block
        
        except Exception as e:
            logger.error(f"Event polling error: {e}")
    
    async def _check_payment_events(self, from_block: int, to_block: int):
        """Check for payment events"""
        # TODO: Query blockchain for payment transactions
        # Look for transactions to compute platform address
        # Emit events for payment processor
        pass
    
    async def _check_job_events(self, from_block: int, to_block: int):
        """Check for compute job events"""
        # TODO: Query for job submission events from smart contract
        pass
    
    async def _check_contributor_events(self, from_block: int, to_block: int):
        """Check for contributor registration events"""
        # TODO: Query for contributor registration events
        pass
    
    async def stop(self):
        """Stop monitoring"""
        logger.info("Stopping chain monitor")
        self.running = False
