"""
ENA Checkpoint Publishing
=========================

Publishes ENA model checkpoints to the DA layer every 10,000 blocks.

This module implements the checkpoint publishing pipeline:
1. Trigger: Every 10,000 blocks (height % 10_000 == 0)
2. Manifest: Create checkpoint manifest with metadata
3. Publish: Submit manifest to DA layer
4. Verify: Store commitment on-chain for retrieval

Design:
- Deterministic versioning tied to chain height
- Manifest includes training summary, eval scores, provenance
- DA storage provides availability and retrievability
- Version scheme: ena-v<major>.<minor>.<patch>-h<height>
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

log = logging.getLogger("ena.checkpoint")

# Checkpoint cadence: every 10,000 blocks
CHECKPOINT_INTERVAL_BLOCKS = 10_000


@dataclass
class EnaCheckpointManifest:
    """
    ENA checkpoint manifest schema.
    
    This is the canonical structure published to DA at each checkpoint interval.
    All fields are deterministic and reproducible from chain state.
    """
    
    # Version and chain metadata
    version: str  # e.g., "ena-v0.9.0-h10000"
    chain_id: int
    height: int
    block_hash: str
    created_at: int  # Unix timestamp
    
    # Model metadata
    base_model: str  # e.g., "llama-2-7b"
    architecture: str  # e.g., "transformer"
    
    # Training metadata
    training_runs: List[Dict[str, Any]]  # List of training job records
    datasets: List[Dict[str, Any]]  # Dataset references with provenance
    
    # Evaluation results
    evals: List[Dict[str, Any]]  # Eval metrics (perplexity, accuracy, etc.)
    
    # Weights and artifacts
    weights: Dict[str, Any]  # {format, hash, size, shards[]}
    tokenizer: Dict[str, Any]  # Tokenizer config
    config: Dict[str, Any]  # Model config
    
    # Economics
    aicf_budget_summary: Dict[str, Any]  # Credits allocated to this checkpoint
    contributors_summary: List[Dict[str, Any]]  # Top contributors
    
    # Signatures (optional, for governance validation)
    signatures: List[Dict[str, str]]  # [{signer, signature}]


def should_publish_checkpoint(height: int) -> bool:
    """
    Check if checkpoint should be published at this height.
    
    Args:
        height: Block height
        
    Returns:
        True if height is a multiple of CHECKPOINT_INTERVAL_BLOCKS
    """
    if height <= 0:
        return False
    return height % CHECKPOINT_INTERVAL_BLOCKS == 0


def compute_checkpoint_version(height: int, major: int = 0, minor: int = 9, patch: int = 0) -> str:
    """
    Compute deterministic checkpoint version from height.
    
    Version scheme: ena-v<major>.<minor>.<patch>-h<height>
    
    Args:
        height: Block height
        major: Major version (default 0)
        minor: Minor version (default 9)
        patch: Patch version (default 0)
        
    Returns:
        Version string
        
    Examples:
        >>> compute_checkpoint_version(10000)
        'ena-v0.9.0-h10000'
        >>> compute_checkpoint_version(20000, major=1, minor=0)
        'ena-v1.0.0-h20000'
    """
    return f"ena-v{major}.{minor}.{patch}-h{height}"


def create_checkpoint_manifest(
    height: int,
    block_hash: str,
    chain_id: int,
    training_runs: Optional[List[Dict[str, Any]]] = None,
    datasets: Optional[List[Dict[str, Any]]] = None,
    evals: Optional[List[Dict[str, Any]]] = None,
    weights: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> EnaCheckpointManifest:
    """
    Create checkpoint manifest for the given height.
    
    Args:
        height: Block height
        block_hash: Block hash (hex string)
        chain_id: Chain ID
        training_runs: Training job records (optional)
        datasets: Dataset references (optional)
        evals: Eval results (optional)
        weights: Weights metadata (optional)
        **kwargs: Additional manifest fields
        
    Returns:
        EnaCheckpointManifest instance
    """
    version = compute_checkpoint_version(height)
    created_at = int(time.time())
    
    # Default empty lists/dicts if not provided
    training_runs = training_runs or []
    datasets = datasets or []
    evals = evals or []
    weights = weights or {
        "format": "safetensors",
        "hash": "",
        "size": 0,
        "shards": [],
    }
    
    manifest = EnaCheckpointManifest(
        version=version,
        chain_id=chain_id,
        height=height,
        block_hash=block_hash,
        created_at=created_at,
        base_model=kwargs.get("base_model", "ena-base"),
        architecture=kwargs.get("architecture", "transformer"),
        training_runs=training_runs,
        datasets=datasets,
        evals=evals,
        weights=weights,
        tokenizer=kwargs.get("tokenizer", {}),
        config=kwargs.get("config", {}),
        aicf_budget_summary=kwargs.get("aicf_budget_summary", {}),
        contributors_summary=kwargs.get("contributors_summary", []),
        signatures=kwargs.get("signatures", []),
    )
    
    return manifest


def serialize_manifest(manifest: EnaCheckpointManifest) -> bytes:
    """
    Serialize checkpoint manifest to bytes for DA submission.
    
    Args:
        manifest: Checkpoint manifest
        
    Returns:
        JSON bytes (UTF-8 encoded)
    """
    # Convert to dict and serialize as canonical JSON
    manifest_dict = asdict(manifest)
    
    # Sort keys for deterministic serialization
    json_str = json.dumps(manifest_dict, sort_keys=True, indent=2)
    
    return json_str.encode("utf-8")


async def publish_checkpoint_to_da(
    manifest: EnaCheckpointManifest,
    da_client: Any,
    namespace: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """
    Publish checkpoint manifest to DA layer.
    
    Args:
        manifest: Checkpoint manifest
        da_client: DA client instance (from omni_sdk.da.client)
        namespace: DA namespace (default 0)
        
    Returns:
        (commitment, receipt) tuple
        
    Raises:
        Exception: If DA submission fails
    """
    log.info(f"Publishing checkpoint {manifest.version} at height {manifest.height}")
    
    # Serialize manifest
    manifest_bytes = serialize_manifest(manifest)
    
    log.debug(f"Checkpoint manifest size: {len(manifest_bytes)} bytes")
    
    try:
        # Submit to DA layer
        commitment, receipt = da_client.post_blob(
            namespace=namespace,
            data=manifest_bytes,
        )
        
        log.info(
            f"Checkpoint {manifest.version} published to DA: "
            f"commitment={commitment}, size={len(manifest_bytes)}"
        )
        
        return commitment, receipt
        
    except Exception as e:
        log.error(f"Failed to publish checkpoint {manifest.version} to DA: {e}")
        raise


async def retrieve_checkpoint_from_da(
    commitment: str,
    da_client: Any,
) -> EnaCheckpointManifest:
    """
    Retrieve and deserialize checkpoint manifest from DA.
    
    Args:
        commitment: DA commitment hash
        da_client: DA client instance
        
    Returns:
        Deserialized checkpoint manifest
        
    Raises:
        Exception: If retrieval or deserialization fails
    """
    log.info(f"Retrieving checkpoint from DA: commitment={commitment}")
    
    try:
        # Retrieve from DA
        manifest_bytes = da_client.get_blob(commitment)
        
        # Deserialize
        manifest_dict = json.loads(manifest_bytes.decode("utf-8"))
        
        # Reconstruct dataclass
        manifest = EnaCheckpointManifest(**manifest_dict)
        
        log.info(f"Retrieved checkpoint {manifest.version} from DA")
        
        return manifest
        
    except Exception as e:
        log.error(f"Failed to retrieve checkpoint from DA: {e}")
        raise


def verify_checkpoint_manifest(
    manifest: EnaCheckpointManifest,
    expected_height: Optional[int] = None,
    expected_chain_id: Optional[int] = None,
) -> tuple[bool, Optional[str]]:
    """
    Verify checkpoint manifest integrity.
    
    Args:
        manifest: Checkpoint manifest to verify
        expected_height: Expected block height (optional)
        expected_chain_id: Expected chain ID (optional)
        
    Returns:
        (is_valid, error_message) tuple
    """
    # Check height
    if expected_height is not None and manifest.height != expected_height:
        return False, f"Height mismatch: expected {expected_height}, got {manifest.height}"
    
    # Check chain ID
    if expected_chain_id is not None and manifest.chain_id != expected_chain_id:
        return False, f"Chain ID mismatch: expected {expected_chain_id}, got {manifest.chain_id}"
    
    # Check version format
    if not manifest.version.startswith("ena-v"):
        return False, f"Invalid version format: {manifest.version}"
    
    # Check version matches height
    expected_version_suffix = f"-h{manifest.height}"
    if not manifest.version.endswith(expected_version_suffix):
        return False, f"Version/height mismatch: version={manifest.version}, height={manifest.height}"
    
    # All checks passed
    return True, None


__all__ = [
    "CHECKPOINT_INTERVAL_BLOCKS",
    "EnaCheckpointManifest",
    "should_publish_checkpoint",
    "compute_checkpoint_version",
    "create_checkpoint_manifest",
    "serialize_manifest",
    "publish_checkpoint_to_da",
    "retrieve_checkpoint_from_da",
    "verify_checkpoint_manifest",
]
