"""
ENA Checkpoint Integration Hook
===============================

Integration point for triggering ENA checkpoint publishing from block import.

This module is called by the chain block import logic when a block at a checkpoint
height is finalized. It orchestrates the checkpoint creation and DA publishing.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

log = logging.getLogger("ena.checkpoint.hook")


def on_block_finalized(
    height: int,
    block_hash: str,
    chain_id: int,
    state: Any,
    da_client: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """
    Hook called when a block is finalized.
    
    Checks if checkpoint should be published and triggers the pipeline if needed.
    
    Args:
        height: Block height
        block_hash: Block hash (hex string)
        chain_id: Chain ID
        state: Chain state (for querying training runs, evals, etc.)
        da_client: DA client instance (optional, for actual publishing)
        
    Returns:
        Checkpoint metadata dict if published, None otherwise
    """
    from ena.checkpoint import should_publish_checkpoint, create_checkpoint_manifest, publish_checkpoint_to_da
    
    # Check if we should publish at this height
    if not should_publish_checkpoint(height):
        return None
    
    log.info(f"Checkpoint trigger activated at height {height}")
    
    try:
        # Query training runs from state (stub for now)
        training_runs = _get_training_runs_since_last_checkpoint(state, height)
        
        # Query datasets used
        datasets = _get_datasets_used(state, height)
        
        # Query eval results
        evals = _get_eval_results(state, height)
        
        # Query weights metadata (if available)
        weights = _get_weights_metadata(state, height)
        
        # Query AICF budget summary
        aicf_budget = _get_aicf_budget_summary(state, height)
        
        # Query top contributors
        contributors = _get_top_contributors(state, height)
        
        # Create checkpoint manifest
        manifest = create_checkpoint_manifest(
            height=height,
            block_hash=block_hash,
            chain_id=chain_id,
            training_runs=training_runs,
            datasets=datasets,
            evals=evals,
            weights=weights,
            aicf_budget_summary=aicf_budget,
            contributors_summary=contributors,
        )
        
        log.info(f"Created checkpoint manifest: {manifest.version}")
        
        # Publish to DA if client is available
        if da_client:
            import asyncio
            commitment, receipt = asyncio.run(publish_checkpoint_to_da(manifest, da_client))
            
            log.info(f"Published checkpoint {manifest.version} to DA: {commitment}")
            
            # Store commitment on-chain (TODO: implement state storage)
            _store_checkpoint_commitment(state, height, commitment, receipt)
            
            return {
                "version": manifest.version,
                "height": height,
                "commitment": commitment,
                "receipt": receipt,
            }
        else:
            log.warning(f"DA client not available, checkpoint manifest created but not published")
            return {
                "version": manifest.version,
                "height": height,
                "published": False,
            }
            
    except Exception as e:
        log.error(f"Failed to publish checkpoint at height {height}: {e}", exc_info=True)
        return None


def _get_training_runs_since_last_checkpoint(state: Any, current_height: int) -> list[Dict[str, Any]]:
    """
    Query training runs completed since last checkpoint.
    
    Args:
        state: Chain state
        current_height: Current block height
        
    Returns:
        List of training run records
    """
    # TODO: Implement actual query against AICF job state
    # For now, return stub data
    
    from ena.checkpoint import CHECKPOINT_INTERVAL_BLOCKS
    
    last_checkpoint_height = current_height - CHECKPOINT_INTERVAL_BLOCKS
    
    log.debug(f"Querying training runs from height {last_checkpoint_height} to {current_height}")
    
    # Stub: return empty list for now
    # In production, this would query AICF job database for completed training jobs
    return []


def _get_datasets_used(state: Any, current_height: int) -> list[Dict[str, Any]]:
    """
    Query datasets used for training.
    
    Args:
        state: Chain state
        current_height: Current block height
        
    Returns:
        List of dataset records with provenance
    """
    # TODO: Implement actual query
    # In production, this would track:
    # - Dataset source URLs
    # - License information
    # - Hash/commitment for reproducibility
    # - Curation metadata
    
    return []


def _get_eval_results(state: Any, current_height: int) -> list[Dict[str, Any]]:
    """
    Query evaluation results for current model.
    
    Args:
        state: Chain state
        current_height: Current block height
        
    Returns:
        List of eval records with metrics
    """
    # TODO: Implement actual query
    # In production, this would include:
    # - Perplexity scores
    # - Accuracy metrics
    # - Factuality eval results
    # - Hallucination detection scores
    
    return []


def _get_weights_metadata(state: Any, current_height: int) -> Dict[str, Any]:
    """
    Get model weights metadata.
    
    Args:
        state: Chain state
        current_height: Current block height
        
    Returns:
        Weights metadata dict
    """
    # TODO: Implement actual metadata extraction
    # In production, this would include:
    # - Hash of weight file
    # - Size in bytes
    # - Shard manifests (for large models)
    # - Format (safetensors, pytorch, etc.)
    
    return {
        "format": "safetensors",
        "hash": "",
        "size": 0,
        "shards": [],
    }


def _get_aicf_budget_summary(state: Any, current_height: int) -> Dict[str, Any]:
    """
    Get AICF budget summary for this checkpoint period.
    
    Args:
        state: Chain state
        current_height: Current block height
        
    Returns:
        Budget summary dict
    """
    # TODO: Implement actual AICF budget query
    # In production, this would query:
    # - Total credits allocated to ENA training
    # - Credits spent on jobs
    # - Credits remaining
    # - Block reward contributions
    # - Fee contributions
    
    return {
        "total_credits_allocated": 0,
        "credits_spent": 0,
        "credits_remaining": 0,
        "source_breakdown": {
            "block_rewards": 0,
            "transaction_fees": 0,
            "ena_call_fees": 0,
        },
    }


def _get_top_contributors(state: Any, current_height: int) -> list[Dict[str, Any]]:
    """
    Get top contributors (miners, GPU providers, etc.) for this period.
    
    Args:
        state: Chain state
        current_height: Current block height
        
    Returns:
        List of contributor records
    """
    # TODO: Implement actual contributor tracking
    # In production, this would query:
    # - Top miners by credits earned
    # - Top GPU providers by compute contributed
    # - Top storage providers by bytes stored
    
    return []


def _store_checkpoint_commitment(
    state: Any,
    height: int,
    commitment: str,
    receipt: Dict[str, Any],
) -> None:
    """
    Store checkpoint commitment in chain state for future retrieval.
    
    Args:
        state: Chain state
        height: Block height
        commitment: DA commitment hash
        receipt: DA receipt
    """
    # TODO: Implement actual state storage
    # In production, this would:
    # - Store commitment in a chain state DB
    # - Index by height for fast lookup
    # - Allow RPC queries like checkpoint.getByHeight(height)
    
    log.info(f"Stored checkpoint commitment for height {height}: {commitment}")


__all__ = [
    "on_block_finalized",
]
