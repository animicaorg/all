"""
Consensus-side work module discovery and selection.

This helper centralizes how the node/miner decides *which* “useful” work
backends are available and which discriminator a block should advertise.

Key ideas:
- Detect optional useful-work modules (AI/Quantum/Storage/VDF) by checking
  import availability. The registry mirrors `proofs/registry.py` bindings.
- Always expose a *memory-hard fallback* (HASH_WORK) so the chain keeps
  progressing even when no specialised backend is present.
- Provide a small, documented extension point: new work kinds only need to
  register a module path and ProofType. Validators stay deterministic because
  the selected work type is carried in the header (workType) and enforced per
  block.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Dict, Iterable, Optional, Set

from consensus.types import ProofType

# Mapping of ProofType -> module import path that acts as the verifier backend.
# If the module is importable, we treat the work kind as “available”.
_MODULES: Dict[ProofType, str] = {
    ProofType.AI: "proofs.ai",
    ProofType.QUANTUM: "proofs.quantum",
    ProofType.STORAGE: "proofs.storage",
    ProofType.VDF: "proofs.vdf",
    ProofType.HASH_WORK: "proofs.hash_work",  # memory-hard fallback
}


@dataclass(frozen=True)
class WorkAvailability:
    """Snapshot of which work kinds are usable on this node."""

    available: Set[int]
    preferred: Optional[int]
    detected: Dict[int, str]
    fallback_only: bool

    def has(self, type_id: int) -> bool:
        return int(type_id) in self.available


def discover_available_work_types(
    *, preferred: Optional[int | str] = None, extras: Optional[Iterable[int]] = None
) -> WorkAvailability:
    """
    Probe the runtime for usable work kinds.

    Args:
        preferred: Optional explicit selection (int ProofType or name).
        extras:    Additional type ids to force-enable (used by tests).
    """
    available: Set[int] = set()
    detected: Dict[int, str] = {}

    for pt, mod in _MODULES.items():
        if find_spec(mod) is not None:
            available.add(int(pt))
            detected[int(pt)] = mod

    if extras:
        available.update(int(x) for x in extras)

    # Always keep HASH_WORK as the portable, memory-hard fallback
    available.add(int(ProofType.HASH_WORK))
    detected.setdefault(int(ProofType.HASH_WORK), _MODULES[ProofType.HASH_WORK])

    pref_val = _normalize_preferred(preferred)
    fallback_only = available == {int(ProofType.HASH_WORK)}
    return WorkAvailability(
        available=available,
        preferred=pref_val,
        detected=detected,
        fallback_only=fallback_only,
    )


def select_work_type(
    chain_id: int, height: int, *, preferred: Optional[int | str] = None
) -> int:
    """
    Choose a work type for mining given the current chain/height and hints.

    - Honors an explicit `preferred` request if available.
    - Otherwise, pick the first non-fallback type; if none, use HASH_WORK.
    """
    avail = discover_available_work_types(preferred=preferred)
    if avail.preferred is not None and avail.has(avail.preferred):
        return int(avail.preferred)

    non_fallback = [t for t in avail.available if t != int(ProofType.HASH_WORK)]
    if non_fallback:
        return int(sorted(non_fallback)[0])

    return int(ProofType.HASH_WORK)


def _normalize_preferred(preferred: Optional[int | str]) -> Optional[int]:
    if preferred is None:
        return None
    if isinstance(preferred, int):
        return preferred
    try:
        return int(ProofType[preferred.upper()])
    except Exception:
        return None


__all__ = ["WorkAvailability", "discover_available_work_types", "select_work_type"]
