"""
Animica proofs package.

This package houses consensus-critical verifiers for all proof kinds
(HashShare, AI v1, Quantum v1, Storage v0, VDF), plus schemas, metrics,
nullifier logic, and the registry that dispatches type_id → verifier.

Public surface:
- __version__: semver string for the proofs module
- registry: functions to register/resolve verifiers (type_id ↔ verifier)
"""

from . import \
    registry  # re-export registry module (get_verifier, register_verifier, ...)
from .version import __version__  # re-export module version

__all__ = ["__version__", "registry"]
