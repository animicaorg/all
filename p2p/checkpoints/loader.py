"""
Checkpoint loader for fetching checkpoints from RPC or file.

Checkpoints are stored in JSON format:
{
  "checkpoints": [
    {"height": 100, "hash": "0x1234..."},
    {"height": 200, "hash": "0x5678..."}
  ],
  "timestamp": 1234567890  // optional, for age checking
}
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .config import CheckpointsConfig


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A single checkpoint entry mapping height to block hash."""
    
    height: int
    hash: str  # hex string, e.g., "0x1234..."
    
    def __post_init__(self):
        """Validate checkpoint data."""
        if self.height < 0:
            raise ValueError(f"Checkpoint height must be non-negative: {self.height}")
        if not isinstance(self.hash, str):
            raise ValueError(f"Checkpoint hash must be a string: {self.hash}")
        # Normalize hash to lowercase with 0x prefix
        normalized = self.hash.lower()
        if not normalized.startswith("0x"):
            normalized = "0x" + normalized
        object.__setattr__(self, "hash", normalized)


class CheckpointLoader:
    """
    Loads checkpoints from RPC or file based on configuration.
    
    Supports:
    - RPC mode: fetches from chain.getCheckpoints or HTTP endpoint
    - File mode: loads from local JSON file
    - Caching to avoid repeated fetches
    """
    
    def __init__(self, config: CheckpointsConfig):
        self.config = config
        self._log = logging.getLogger("animica.p2p.checkpoints.loader")
        self._cached_checkpoints: Optional[List[Checkpoint]] = None
        self._cache_timestamp: Optional[float] = None
    
    async def load_checkpoints(self) -> List[Checkpoint]:
        """
        Load checkpoints based on configuration.
        
        Returns:
            List of Checkpoint objects, empty if disabled or unavailable.
        
        Raises:
            Exception if strict mode is enabled and checkpoints cannot be loaded.
        """
        if not self.config.is_enabled():
            return []
        
        # Check cache validity
        if self._is_cache_valid():
            self._log.debug("Using cached checkpoints")
            return self._cached_checkpoints or []
        
        try:
            if self.config.requires_rpc():
                checkpoints = await self._load_from_rpc()
            elif self.config.requires_file():
                checkpoints = await self._load_from_file()
            else:
                checkpoints = []
            
            # Update cache
            self._cached_checkpoints = checkpoints
            self._cache_timestamp = time.time()
            
            self._log.info(f"Loaded {len(checkpoints)} checkpoints from {self.config.mode}")
            return checkpoints
        
        except Exception as e:
            self._log.error(f"Failed to load checkpoints: {e}")
            if self.config.strict:
                raise
            
            # Non-strict mode: log warning and continue without checkpoints
            self._log.warning(
                f"Checkpoints unavailable in {self.config.mode} mode, "
                "continuing without checkpoints"
            )
            return []
    
    def _is_cache_valid(self) -> bool:
        """Check if cached checkpoints are still valid."""
        if self._cached_checkpoints is None or self._cache_timestamp is None:
            return False
        
        if self.config.max_age_seconds is None:
            # No max age, cache is always valid once populated
            return True
        
        age = time.time() - self._cache_timestamp
        return age < self.config.max_age_seconds
    
    async def _load_from_rpc(self) -> List[Checkpoint]:
        """
        Load checkpoints from RPC endpoint.
        
        Tries two methods:
        1. JSON-RPC method: chain.getCheckpoints
        2. HTTP endpoint: /checkpoints (or /checkpoints.json) under same base URL
        """
        import httpx
        
        # Try JSON-RPC method first
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "chain.getCheckpoints",
                    "params": [],
                }
                response = await client.post(self.config.rpc_url, json=payload)
                data = response.json()
                
                if "result" in data:
                    return self._parse_checkpoints(data["result"])
        
        except Exception as e:
            self._log.debug(f"JSON-RPC checkpoint fetch failed: {e}")
        
        # Fallback to HTTP endpoint
        try:
            # Extract base URL (remove /rpc suffix if present)
            base_url = self.config.rpc_url.rstrip("/")
            if base_url.endswith("/rpc"):
                base_url = base_url[:-4]
            
            checkpoint_urls = [
                f"{base_url}/checkpoints.json",
                f"{base_url}/checkpoints",
            ]
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                for url in checkpoint_urls:
                    try:
                        response = await client.get(url)
                        if response.status_code == 200:
                            data = response.json()
                            return self._parse_checkpoints(data)
                    except Exception:
                        continue
        
        except Exception as e:
            self._log.debug(f"HTTP checkpoint fetch failed: {e}")
        
        raise RuntimeError(f"Could not fetch checkpoints from RPC: {self.config.rpc_url}")
    
    async def _load_from_file(self) -> List[Checkpoint]:
        """Load checkpoints from local JSON file."""
        if not self.config.file_path:
            raise ValueError("ANIMICA_CHECKPOINTS_FILE not configured")
        
        path = Path(self.config.file_path)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint file not found: {path}")
        
        with path.open("r") as f:
            data = json.load(f)
        
        return self._parse_checkpoints(data)
    
    def _parse_checkpoints(self, data: dict | list) -> List[Checkpoint]:
        """
        Parse checkpoint data from JSON.
        
        Supports formats:
        - {"checkpoints": [...], "timestamp": 123}
        - [{"height": 100, "hash": "0x..."}, ...]
        """
        # Handle dict with "checkpoints" key
        if isinstance(data, dict):
            if "checkpoints" in data:
                checkpoint_list = data["checkpoints"]
            else:
                # Assume dict is a single checkpoint or malformed
                raise ValueError("Invalid checkpoint format: expected 'checkpoints' key")
        elif isinstance(data, list):
            checkpoint_list = data
        else:
            raise ValueError(f"Invalid checkpoint data type: {type(data)}")
        
        checkpoints = []
        for item in checkpoint_list:
            if not isinstance(item, dict):
                self._log.warning(f"Skipping invalid checkpoint entry: {item}")
                continue
            
            height = item.get("height")
            hash_val = item.get("hash")
            
            if height is None or hash_val is None:
                self._log.warning(f"Skipping incomplete checkpoint: {item}")
                continue
            
            try:
                checkpoint = Checkpoint(height=int(height), hash=str(hash_val))
                checkpoints.append(checkpoint)
            except Exception as e:
                self._log.warning(f"Skipping invalid checkpoint {item}: {e}")
        
        # Sort by height for consistency
        checkpoints.sort(key=lambda c: c.height)
        
        return checkpoints
