#!/usr/bin/env python3
"""
Simple P2P2 node runner for testing.

Usage:
    python run_p2p2_node.py --listen-port 9333 --data-dir /tmp/p2p2-node1
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent))

from p2p2.service import P2PService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


# Minimal mock storage for testing
class MockBlockDB:
    """Mock block database."""
    
    def __init__(self):
        self.blocks = {"genesis": {"hash": "genesis", "parent_hash": "0x0", "height": 0}}
        self.head_height = 0
        self.head_hash = "genesis"
    
    async def get_head_height(self):
        return self.head_height
    
    async def get_head(self):
        return {"hash": self.head_hash, "height": self.head_height}
    
    async def get_block_by_height(self, height):
        # Simplified - just return genesis for now
        if height == 0:
            return self.blocks["genesis"]
        return None
    
    async def get_block_by_hash(self, block_hash):
        return self.blocks.get(block_hash)
    
    async def has_block(self, block_hash):
        return block_hash in self.blocks
    
    async def store_block(self, block):
        block_hash = block["hash"]
        self.blocks[block_hash] = block
        
        # Update head if next height
        height = block.get("height", 0)
        if height == self.head_height + 1:
            self.head_height = height
            self.head_hash = block_hash
        
        logger.info(f"Stored block {block_hash[:8]} at height {height}")


class MockStateDB:
    """Mock state database."""
    pass


async def main():
    parser = argparse.ArgumentParser(description="Run P2P2 test node")
    parser.add_argument("--listen-host", default="0.0.0.0", help="Listen host")
    parser.add_argument("--listen-port", type=int, default=9333, help="Listen port")
    parser.add_argument("--node-id", default="test-node-1", help="Node ID")
    parser.add_argument("--network-id", default="testnet", help="Network ID")
    parser.add_argument("--chain-id", type=int, default=1337, help="Chain ID")
    parser.add_argument("--genesis-hash", default="0x0", help="Genesis hash")
    parser.add_argument("--data-dir", default="/tmp/p2p2-test", help="Data directory")
    parser.add_argument("--seeds", nargs="+", help="Seed nodes (host:port)")
    
    args = parser.parse_args()
    
    # Create data directory
    Path(args.data_dir).mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Starting P2P2 test node")
    logger.info(f"  Node ID: {args.node_id}")
    logger.info(f"  Network: {args.network_id} (chain_id={args.chain_id})")
    logger.info(f"  Listen: {args.listen_host}:{args.listen_port}")
    logger.info(f"  Data dir: {args.data_dir}")
    
    # Create mock storage
    block_db = MockBlockDB()
    state_db = MockStateDB()
    
    # Create P2P service
    service = P2PService(
        node_id=args.node_id,
        network_id=args.network_id,
        chain_id=args.chain_id,
        genesis_hash=args.genesis_hash,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        data_dir=args.data_dir,
        block_db=block_db,
        state_db=state_db,
    )
    
    # Start service
    await service.start()
    
    # Connect to seeds if provided
    if args.seeds:
        logger.info(f"Connecting to {len(args.seeds)} seed nodes")
        for seed in args.seeds:
            await service.connect_to_seed(seed)
    
    logger.info("P2P2 service started successfully")
    logger.info("Press Ctrl+C to stop")
    
    # Run until interrupted
    try:
        while True:
            await asyncio.sleep(10)
            
            # Print status
            status = service.api.get_node_status()
            logger.info(f"Status: {status['p2p']['peers']['connected']} peers connected, "
                       f"sync={status['sync']['is_syncing']}, "
                       f"orphans={status['sync']['orphan_pool_size']}")
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await service.stop()
        logger.info("Stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
