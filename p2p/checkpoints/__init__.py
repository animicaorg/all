"""
p2p.checkpoints
===============

Optional checkpoint mechanism for P2P-first sync safety rails.

Model 3 (Hybrid):
- Default behavior remains P2P-first for sync/validation/mining.
- No code path requires https://rpc.animica.org/rpc to be reachable.
- Adds optional checkpoint mechanism that can consult a configured URL
  or local file to fetch/check pinned checkpoints (height->hash).
- Used as a safety rail during initial sync or fork-choice, not as a live head oracle.

Configuration:
- ANIMICA_CHECKPOINTS_MODE=off|rpc|file (default: off)
- ANIMICA_CHECKPOINTS_RPC_URL (default: https://rpc.animica.org/rpc)
- ANIMICA_CHECKPOINTS_FILE (path to JSON checkpoints)
- ANIMICA_CHECKPOINTS_MAX_AGE (optional; seconds)
- ANIMICA_CHECKPOINTS_STRICT (default: false)

Integration points:
- Initial sync when DB empty or far behind
- During reorg/fork-choice when adopting a new best chain
"""

from .config import CheckpointsConfig, load_checkpoints_config
from .loader import CheckpointLoader, Checkpoint
from .verifier import CheckpointVerifier

__all__ = [
    "CheckpointsConfig",
    "load_checkpoints_config",
    "CheckpointLoader",
    "Checkpoint",
    "CheckpointVerifier",
]
