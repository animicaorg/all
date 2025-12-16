"""
Integration module for checkpoint functionality with P2P sync.

Provides helper functions to initialize checkpoints and integrate them
with header sync and fork choice.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .config import CheckpointsConfig, load_checkpoints_config
from .loader import CheckpointLoader
from .verifier import CheckpointVerifier


_log = logging.getLogger("animica.p2p.checkpoints.integration")


async def initialize_checkpoints(
    config: Optional[CheckpointsConfig] = None,
    chain_id: Optional[int] = None,
    include_builtin: bool = True,
) -> Optional[CheckpointVerifier]:
    """
    Initialize checkpoint system based on configuration.
    
    Args:
        config: CheckpointsConfig instance, or None to load from environment.
        chain_id: Optional chain identifier for built-in checkpoints (1=mainnet, etc.)
        include_builtin: If True, include built-in checkpoints for the chain.
    
    Returns:
        CheckpointVerifier instance if checkpoints are enabled, None otherwise.
    """
    if config is None:
        config = load_checkpoints_config()
    
    if not config.is_enabled() and not (include_builtin and chain_id is not None):
        _log.info("Checkpoints disabled (mode=off) and no built-in requested")
        return None
    
    _log.info(f"Initializing checkpoints in {config.mode} mode (chain_id={chain_id})")
    
    try:
        loader = CheckpointLoader(config, chain_id=chain_id)
        checkpoints = await loader.load_checkpoints(include_builtin=include_builtin)
        
        if not checkpoints:
            _log.warning(
                f"No checkpoints loaded in {config.mode} mode "
                f"(strict={config.strict}, chain_id={chain_id})"
            )
            if config.strict:
                raise RuntimeError(
                    f"Strict mode enabled but no checkpoints available "
                    f"(mode={config.mode})"
                )
            return None
        
        verifier = CheckpointVerifier(checkpoints)
        _log.info(
            f"Loaded {len(checkpoints)} checkpoints "
            f"(heights {verifier.get_lowest_checkpoint_height()}-"
            f"{verifier.get_highest_checkpoint_height()})"
        )
        
        return verifier
    
    except Exception as e:
        _log.error(f"Failed to initialize checkpoints: {e}")
        if config.strict:
            raise
        
        _log.warning("Continuing without checkpoints (non-strict mode)")
        return None


def get_checkpoint_config_summary(config: CheckpointsConfig) -> dict[str, Any]:
    """
    Get a summary of checkpoint configuration for logging/debugging.
    
    Args:
        config: CheckpointsConfig instance.
    
    Returns:
        Dict with configuration summary.
    """
    return {
        "mode": config.mode,
        "enabled": config.is_enabled(),
        "rpc_url": config.rpc_url if config.requires_rpc() else None,
        "file_path": config.file_path if config.requires_file() else None,
        "max_age_seconds": config.max_age_seconds,
        "strict": config.strict,
    }


async def verify_chain_checkpoints(
    verifier: Optional[CheckpointVerifier],
    chain_view: Any,
    max_height: Optional[int] = None,
) -> tuple[bool, list[str]]:
    """
    Verify chain against checkpoints if verifier is available.
    
    Args:
        verifier: CheckpointVerifier instance or None.
        chain_view: Chain view providing get_block_hash_at_height.
        max_height: Optional maximum height to verify.
    
    Returns:
        Tuple of (is_valid, errors) where errors is list of error messages.
    """
    if verifier is None:
        return True, []
    
    try:
        return await verifier.verify_chain(chain_view, max_height)
    except Exception as e:
        _log.error(f"Error during checkpoint verification: {e}")
        return False, [f"Verification error: {e}"]
