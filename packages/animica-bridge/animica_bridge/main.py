"""
Animica Bridge Service - Main Entry Point

Connects the compute platform to the Animica blockchain.
"""

import asyncio
import logging
import os
import signal
from typing import Optional

from animica_bridge.chain_monitor import ChainMonitor
from animica_bridge.payment_processor import PaymentProcessor
from animica_bridge.receipt_submitter import ReceiptSubmitter
from animica_bridge.config import settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AnimicaBridge:
    """Main bridge service coordinator"""
    
    def __init__(self):
        self.chain_monitor = ChainMonitor(
            rpc_url=settings.ANIMICA_RPC_URL,
            chain_id=settings.ANIMICA_CHAIN_ID
        )
        self.payment_processor = PaymentProcessor()
        self.receipt_submitter = ReceiptSubmitter(
            rpc_url=settings.ANIMICA_RPC_URL,
            private_key=settings.BRIDGE_PRIVATE_KEY
        )
        self.running = False
        
    async def start(self):
        """Start the bridge service"""
        logger.info("Starting Animica Bridge Service")
        logger.info(f"Connected to: {settings.ANIMICA_RPC_URL}")
        logger.info(f"Chain ID: {settings.ANIMICA_CHAIN_ID}")
        
        self.running = True
        
        # Start all components
        tasks = [
            self.chain_monitor.run(),
            self.payment_processor.run(),
            self.receipt_submitter.run()
        ]
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bridge service cancelled")
        except Exception as e:
            logger.error(f"Bridge service error: {e}", exc_info=True)
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop the bridge service"""
        logger.info("Stopping Animica Bridge Service")
        self.running = False
        
        # Stop all components
        await self.chain_monitor.stop()
        await self.payment_processor.stop()
        await self.receipt_submitter.stop()
        
        logger.info("Bridge service stopped")


async def main():
    """Main entry point"""
    bridge = AnimicaBridge()
    
    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    
    def signal_handler(sig):
        logger.info(f"Received signal {sig}")
        asyncio.create_task(bridge.stop())
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
    
    try:
        await bridge.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    finally:
        await bridge.stop()


if __name__ == "__main__":
    asyncio.run(main())
