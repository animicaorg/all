"""
ena.chain_registry — ENA On-Chain Model Version Registry
=========================================================

Provides helpers for managing ENA model versions anchored to the DA layer.

Every ~10,000 blocks, a new ENA checkpoint is published to the DA layer
and the model version registry is updated. This module provides:

- ENAChainModelRegistry: manages versioned model entries
- DA integration helpers for anchoring model checkpoints
- Version validation and policy checks
- Periodic anchoring hooks (for automation)

Design:
- Model versions are globally identified by a canonical version string
  (e.g., "ena-v0.9.0-h10000").
- Each version has a DA commitment (content-addressed checkpoint manifest).
- The active model version is stored in chain state and updated via governance
  or operator actions.
- The checkpoint cadence (10,000 blocks) is defined in ena.checkpoint and
  referenced here as a single source of truth.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("ena.chain_registry")

# Re-export checkpoint interval as canonical constant.
try:
    from ena.checkpoint import CHECKPOINT_INTERVAL_BLOCKS  # type: ignore
except ImportError:
    CHECKPOINT_INTERVAL_BLOCKS = 10_000


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ChainModelEntry:
    """
    A single ENA model version entry in the on-chain registry.

    Immutable once registered; governance can only update 'status'.
    """

    version: str              # e.g., "ena-v0.9.0-h10000"
    da_ptr: str               # DA commitment hash for checkpoint manifest
    activation_height: int    # Block height at which this version was activated
    status: str = "active"    # active | deprecated | experimental | local_only
    metadata_hash: str = ""   # Optional DA pointer to tokenizer/architecture manifest


@dataclass
class RegistrySnapshot:
    """Snapshot of the full model registry at a given height."""

    height: int
    active_version: str
    entries: List[ChainModelEntry]


# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------


class ENAChainModelRegistry:
    """
    On-chain ENA model version registry.

    Wraps the execution state ENA model functions and provides higher-level
    operations like bulk listing, status transitions, and DA anchoring.
    """

    def __init__(self, state: Any) -> None:
        """
        Args:
            state: Chain state object supporting get(key) / put(key, value).
        """
        self._state = state
        try:
            from execution.state import ena_state as _ena_state  # type: ignore
            self._ena = _ena_state
        except ImportError:
            self._ena = None

    def _require_ena(self):
        if self._ena is None:
            raise RuntimeError("execution.state.ena_state not available")

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def register_version(
        self,
        version: str,
        da_ptr: str,
        activation_height: int,
        status: str = "active",
        metadata_hash: str = "",
        set_active: bool = False,
    ) -> ChainModelEntry:
        """
        Register a new model version in the on-chain registry.

        Args:
            version: Canonical version string (e.g., "ena-v0.9.0-h10000").
            da_ptr: DA commitment hash for the checkpoint manifest.
            activation_height: Block height of activation.
            status: "active", "deprecated", "experimental", or "local_only".
            metadata_hash: Optional DA pointer to model metadata.
            set_active: If True, also set this version as the chain active model.

        Returns:
            ChainModelEntry for the registered version.
        """
        self._require_ena()
        _validate_version_string(version)
        _validate_status(status)

        self._ena.register_model_version(
            state=self._state,
            version=version,
            da_ptr=da_ptr,
            activation_height=activation_height,
            status=status,
            metadata_hash=metadata_hash,
        )

        if set_active and status == "active":
            self._ena.set_active_model(self._state, version)
            log.info("ENA active model set to %s", version)

        log.info("Registered ENA model version %s (height=%d, status=%s, da_ptr=%s)",
                 version, activation_height, status, da_ptr or "(none)")

        return ChainModelEntry(
            version=version,
            da_ptr=da_ptr,
            activation_height=activation_height,
            status=status,
            metadata_hash=metadata_hash,
        )

    def get_active_version(self) -> str:
        """Return the currently active model version string, or ""."""
        self._require_ena()
        return self._ena.get_active_model(self._state)

    def get_version(self, version: str) -> Optional[ChainModelEntry]:
        """Get a specific model version entry."""
        self._require_ena()
        mv = self._ena.get_model_version(self._state, version)
        if mv is None:
            return None
        return ChainModelEntry(
            version=mv.version,
            da_ptr=mv.da_ptr,
            activation_height=mv.activation_height,
            status=mv.status,
            metadata_hash=mv.metadata_hash,
        )

    def deprecate_version(self, version: str) -> None:
        """Mark a version as deprecated (governance action)."""
        self._require_ena()
        mv = self._ena.get_model_version(self._state, version)
        if mv is None:
            raise ValueError(f"Model version not found: {version!r}")
        self._ena.register_model_version(
            state=self._state,
            version=version,
            da_ptr=mv.da_ptr,
            activation_height=mv.activation_height,
            status="deprecated",
            metadata_hash=mv.metadata_hash,
        )
        log.info("ENA model version deprecated: %s", version)

    def is_version_allowed(self, version: str) -> bool:
        """Check if a model version is allowed for on-chain requests."""
        self._require_ena()
        return self._ena.is_model_allowed(self._state, version)

    # ------------------------------------------------------------------
    # Periodic anchoring hook
    # ------------------------------------------------------------------

    def should_anchor_checkpoint(self, height: int) -> bool:
        """
        Returns True if a checkpoint should be anchored at this block height.

        The anchoring cadence is CHECKPOINT_INTERVAL_BLOCKS (default: 10,000).
        """
        if height <= 0:
            return False
        return height % CHECKPOINT_INTERVAL_BLOCKS == 0

    def process_checkpoint_anchor(
        self,
        height: int,
        block_hash: str,
        da_ptr: str,
        chain_id: int,
        metadata_hash: str = "",
        status: str = "active",
    ) -> Optional[ChainModelEntry]:
        """
        Process a checkpoint anchor at the given height.

        If this height is a checkpoint height (height % 10000 == 0), registers
        a new model version and optionally sets it as active.

        This method is called by the block processor when a new block is finalised.
        It is deterministic: same inputs → same version string and same state changes.

        Args:
            height: Current block height.
            block_hash: Hash of the current block (for version string).
            da_ptr: DA commitment for the checkpoint manifest.
            chain_id: Chain ID.
            metadata_hash: Optional DA pointer to model metadata.
            status: Version status ("active", "experimental", etc.).

        Returns:
            ChainModelEntry if a new version was registered, else None.
        """
        if not self.should_anchor_checkpoint(height):
            return None

        # Compute deterministic version string
        try:
            from ena.checkpoint import compute_checkpoint_version  # type: ignore
            version = compute_checkpoint_version(height)
        except ImportError:
            version = f"ena-v0.9.0-h{height}"

        set_active = status == "active"

        entry = self.register_version(
            version=version,
            da_ptr=da_ptr,
            activation_height=height,
            status=status,
            metadata_hash=metadata_hash,
            set_active=set_active,
        )
        log.info(
            "ENA checkpoint anchored at height=%d: version=%s, da_ptr=%s",
            height, version, da_ptr,
        )
        return entry


# ---------------------------------------------------------------------------
# Standalone helpers (no state required)
# ---------------------------------------------------------------------------


def _validate_version_string(version: str) -> None:
    """Validate ENA version string format."""
    if not version:
        raise ValueError("version must not be empty")
    if not version.startswith("ena-v"):
        raise ValueError(f"version must start with 'ena-v': {version!r}")


def _validate_status(status: str) -> None:
    """Validate ENA model status value."""
    valid = {"active", "deprecated", "experimental", "local_only"}
    if status not in valid:
        raise ValueError(f"Invalid status {status!r}. Must be one of: {sorted(valid)}")


def compute_version_for_height(height: int, major: int = 0, minor: int = 9, patch: int = 0) -> str:
    """
    Compute the canonical ENA version string for a given block height.

    This is the single source of truth for version naming — do not duplicate
    this logic elsewhere.

    Args:
        height: Block height (should be a checkpoint height).
        major, minor, patch: Semantic version components.

    Returns:
        Version string, e.g., "ena-v0.9.0-h10000".
    """
    return f"ena-v{major}.{minor}.{patch}-h{height}"


def parse_version_height(version: str) -> Optional[int]:
    """
    Extract the block height from an ENA version string.

    Returns None if the version string doesn't match the expected format.
    """
    if not version or not version.startswith("ena-v"):
        return None
    try:
        # Format: "ena-v{major}.{minor}.{patch}-h{height}"
        h_part = version.rsplit("-h", 1)
        if len(h_part) != 2:
            return None
        return int(h_part[1])
    except (ValueError, IndexError):
        return None


def is_checkpoint_height(height: int) -> bool:
    """Return True if height is a checkpoint anchor height."""
    if height <= 0:
        return False
    return height % CHECKPOINT_INTERVAL_BLOCKS == 0


__all__ = [
    "ChainModelEntry",
    "RegistrySnapshot",
    "ENAChainModelRegistry",
    "CHECKPOINT_INTERVAL_BLOCKS",
    "compute_version_for_height",
    "parse_version_height",
    "is_checkpoint_height",
]
