"""
Receipt Submitter - Submits compute receipts to blockchain
"""

import asyncio
import logging
from typing import Optional
import hashlib

from animica_bridge.config import settings

logger = logging.getLogger(__name__)


class ReceiptSubmitter:
    """Submits proof-of-execution receipts to blockchain"""
    
    def __init__(self, rpc_url: str, private_key: str):
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.running = False
        self.pending_receipts = []
        
    async def run(self):
        """Main submission loop"""
        self.running = True
        logger.info("Receipt submitter started")
        
        while self.running:
            try:
                await self._submit_pending_receipts()
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"Receipt submission error: {e}", exc_info=True)
                await asyncio.sleep(30)
    
    async def _submit_pending_receipts(self):
        """Submit pending receipts to chain"""
        if not self.pending_receipts:
            return
        
        logger.info(f"Submitting {len(self.pending_receipts)} receipts")
        
        for receipt in self.pending_receipts[:]:
            try:
                await self._submit_receipt(receipt)
                self.pending_receipts.remove(receipt)
            except Exception as e:
                logger.error(f"Failed to submit receipt: {e}")
    
    async def _submit_receipt(self, receipt: dict):
        """Submit a single receipt"""
        # TODO: Create and sign transaction
        # Submit to blockchain
        # Wait for confirmation
        # Store receipt hash
        
        logger.info(f"Submitted receipt for job {receipt.get('job_id')}")
    
    async def create_receipt(self, job_id: str, result: dict, metadata: dict) -> dict:
        """
        Create a receipt for compute job.
        
        Args:
            job_id: Job identifier
            result: Job result data
            metadata: Additional metadata
        
        Returns:
            Receipt object
        """
        # Create receipt hash
        receipt_data = f"{job_id}:{result}:{metadata}".encode()
        receipt_hash = hashlib.sha256(receipt_data).hexdigest()
        
        receipt = {
            "job_id": job_id,
            "receipt_hash": receipt_hash,
            "result_summary": str(result)[:100],
            "metadata": metadata,
            "status": "pending"
        }
        
        # Add to pending queue
        self.pending_receipts.append(receipt)
        
        logger.info(f"Created receipt for job {job_id}")
        return receipt
    
    async def verify_receipt(self, receipt_hash: str) -> bool:
        """
        Verify receipt exists on chain.
        
        Args:
            receipt_hash: Receipt hash
        
        Returns:
            True if receipt is on-chain
        """
        # TODO: Query blockchain for receipt
        return False
    
    async def stop(self):
        """Stop submitter"""
        logger.info("Stopping receipt submitter")
        self.running = False
