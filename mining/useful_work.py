"""
Adaptive useful-work selection for the miner.

This bridges miner-side decisions to the consensus `work_registry`, handling:
- Module discovery (AI/Quantum/Storage/VDF/hash_work).
- Hardware hints (CPU/GPU) for ops/telemetry.
- A single entrypoint to pick the `workType` discriminator that gets stamped
  into block headers after the mainnet height-100_000 fork.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Dict, Optional, Sequence

from consensus import work_registry
from consensus.types import ProofType


@dataclass(frozen=True)
class BackendSnapshot:
    work_types: work_registry.WorkAvailability
    hardware: Sequence[str]


def detect_hardware_backends() -> Sequence[str]:
    """
    Lightweight hardware probe (no heavy imports):
      - "gpu" if OpenCL/CUDA backends are importable
      - Always includes "cpu"
    """
    hw = ["cpu"]
    for mod in ("mining.gpu_opencl", "mining.gpu_metal", "mining.gpu_cuda"):
        if find_spec(mod):
            hw.append("gpu")
            break
    return hw


def snapshot() -> BackendSnapshot:
    """Return a consolidated view of work types and hardware backends."""
    return BackendSnapshot(
        work_types=work_registry.discover_available_work_types(
            preferred=os.getenv("ANIMICA_MINER_WORK_TYPE")
        ),
        hardware=detect_hardware_backends(),
    )


def auto_select_work_type(
    *, chain_id: int, height: int, preferred: Optional[int | str] = None
) -> Optional[int]:
    """
    Pick a work type for the current block.

    Selection order:
      1) Explicit argument `preferred`
      2) Env override ANIMICA_MINER_WORK_TYPE (int or name)
      3) First detected non-fallback type
      4) HASH_WORK fallback
    """
    pref = preferred or os.getenv("ANIMICA_MINER_WORK_TYPE")
    return int(work_registry.select_work_type(chain_id, height, preferred=pref))


def describe_backends() -> Dict[str, object]:
    """Human-friendly description used in logs/CLI."""
    snap = snapshot()
    return {
        "work_types": sorted(snap.work_types.available),
        "fallback_only": snap.work_types.fallback_only,
        "hardware": list(snap.hardware),
    }


__all__ = ["BackendSnapshot", "auto_select_work_type", "detect_hardware_backends", "describe_backends", "snapshot"]
