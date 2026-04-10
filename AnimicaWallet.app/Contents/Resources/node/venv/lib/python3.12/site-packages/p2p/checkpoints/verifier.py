"""
Checkpoint verifier for validating chain against checkpoints.

Provides logic to verify that the local canonical chain matches
checkpoints at specified heights. Used during:
- Initial sync when DB is empty or far behind
- Fork choice / reorg when adopting a new best chain
"""

from __future__ import annotations

import logging
from typing import List, Optional, Protocol, runtime_checkable

from .loader import Checkpoint


@runtime_checkable
class ChainView(Protocol):
    """
    Minimal interface for chain queries needed by checkpoint verification.
    
    Implementations should provide access to block hashes at specific heights
    on the canonical chain.
    """
    
    async def get_block_hash_at_height(self, height: int) -> Optional[str]:
        """
        Get block hash at specified height on canonical chain.
        
        Returns:
            Block hash as hex string (with 0x prefix), or None if not found.
        """
        ...


class CheckpointVerifier:
    """
    Verifies that the local chain matches configured checkpoints.
    
    Used as a safety rail during sync and fork choice to prevent
    adopting a chain that diverges from known-good checkpoints.
    """
    
    def __init__(self, checkpoints: List[Checkpoint]):
        """
        Initialize verifier with checkpoints.
        
        Args:
            checkpoints: List of Checkpoint objects to verify against.
        """
        self.checkpoints = checkpoints
        self._log = logging.getLogger("animica.p2p.checkpoints.verifier")
        
        # Build height->hash lookup for quick access
        self._checkpoint_map = {cp.height: cp.hash for cp in checkpoints}
    
    def has_checkpoints(self) -> bool:
        """Return True if any checkpoints are configured."""
        return len(self.checkpoints) > 0
    
    def get_checkpoint_at_height(self, height: int) -> Optional[Checkpoint]:
        """Get checkpoint at specified height, if one exists."""
        hash_val = self._checkpoint_map.get(height)
        if hash_val is None:
            return None
        return Checkpoint(height=height, hash=hash_val)
    
    async def verify_chain(
        self, chain: ChainView, max_height: Optional[int] = None
    ) -> tuple[bool, List[str]]:
        """
        Verify that the local chain matches checkpoints.
        
        Args:
            chain: ChainView implementation providing block hash queries.
            max_height: Optional maximum height to check (for partial verification).
        
        Returns:
            Tuple of (is_valid, errors) where errors is a list of mismatch descriptions.
        """
        if not self.has_checkpoints():
            return True, []
        
        errors = []
        
        for checkpoint in self.checkpoints:
            # Skip checkpoints beyond max_height if specified
            if max_height is not None and checkpoint.height > max_height:
                continue
            
            try:
                local_hash = await chain.get_block_hash_at_height(checkpoint.height)
                
                if local_hash is None:
                    # Block not yet synced, skip this checkpoint
                    self._log.debug(
                        f"Checkpoint at height {checkpoint.height} not yet synced, skipping"
                    )
                    continue
                
                # Normalize hashes for comparison (lowercase, with 0x prefix)
                local_hash_normalized = local_hash.lower()
                if not local_hash_normalized.startswith("0x"):
                    local_hash_normalized = "0x" + local_hash_normalized
                
                checkpoint_hash_normalized = checkpoint.hash.lower()
                if not checkpoint_hash_normalized.startswith("0x"):
                    checkpoint_hash_normalized = "0x" + checkpoint_hash_normalized
                
                if local_hash_normalized != checkpoint_hash_normalized:
                    error = (
                        f"Checkpoint mismatch at height {checkpoint.height}: "
                        f"expected {checkpoint.hash}, got {local_hash}"
                    )
                    self._log.error(error)
                    errors.append(error)
                else:
                    self._log.debug(f"Checkpoint verified at height {checkpoint.height}")
            
            except Exception as e:
                error = f"Error verifying checkpoint at height {checkpoint.height}: {e}"
                self._log.error(error)
                errors.append(error)
        
        is_valid = len(errors) == 0
        return is_valid, errors
    
    async def verify_block(
        self, chain: ChainView, height: int, block_hash: str
    ) -> tuple[bool, Optional[str]]:
        """
        Verify a single block against checkpoint if one exists at that height.
        
        Args:
            chain: ChainView implementation (not used, but kept for consistency).
            height: Block height to verify.
            block_hash: Block hash to verify.
        
        Returns:
            Tuple of (is_valid, error_message).
            is_valid is True if no checkpoint exists or hash matches.
            error_message is None if valid, otherwise contains description.
        """
        checkpoint = self.get_checkpoint_at_height(height)
        if checkpoint is None:
            # No checkpoint at this height, accept
            return True, None
        
        # Normalize hashes for comparison
        block_hash_normalized = block_hash.lower()
        if not block_hash_normalized.startswith("0x"):
            block_hash_normalized = "0x" + block_hash_normalized
        
        checkpoint_hash_normalized = checkpoint.hash.lower()
        if not checkpoint_hash_normalized.startswith("0x"):
            checkpoint_hash_normalized = "0x" + checkpoint_hash_normalized
        
        if block_hash_normalized != checkpoint_hash_normalized:
            error = (
                f"Checkpoint mismatch at height {height}: "
                f"expected {checkpoint.hash}, got {block_hash}"
            )
            self._log.error(error)
            return False, error
        
        self._log.debug(f"Block verified against checkpoint at height {height}")
        return True, None
    
    def get_highest_checkpoint_height(self) -> Optional[int]:
        """Get the height of the highest configured checkpoint."""
        if not self.has_checkpoints():
            return None
        return max(cp.height for cp in self.checkpoints)
    
    def get_lowest_checkpoint_height(self) -> Optional[int]:
        """Get the height of the lowest configured checkpoint."""
        if not self.has_checkpoints():
            return None
        return min(cp.height for cp in self.checkpoints)
