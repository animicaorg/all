"""Mode resolution: simulate / lite / full, with strict failure paths.

This module is the single source of truth for what counts as "real" training
vs. a stub. Every stage script and the inference runtime call into here to
get an :class:`EffectiveBackend` that records:

- requested_mode               what FLAGSHIP_MODE asked for
- effective_mode               what we can actually deliver
- accelerator_requested        cuda | mps | cpu
- accelerator_effective        cuda | mps | cpu
- fallback_reasons             list of reasons we degraded
- available_for_real_inference whether the resulting bundle should be marked
                               real-inference-ready in its manifest

Strict full mode never silently degrades. Any degradation in full mode
raises ``RuntimeError`` with a clear message. lite and simulate modes
degrade gracefully.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Optional


_MODES = ("simulate", "lite", "full")


@dataclass
class EffectiveBackend:
    requested_mode: str
    effective_mode: str
    requested_base_model: str
    loaded_base_model: str
    accelerator_requested: str
    accelerator_effective: str
    precision: str
    fallback_reasons: list[str] = field(default_factory=list)
    available_for_real_inference: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Accelerator detection                                                       #
# --------------------------------------------------------------------------- #

def _has_cuda() -> bool:
    if os.environ.get("FLAGSHIP_FORCE_CPU") == "1":
        return False
    try:
        import torch    # type: ignore
        return bool(torch.cuda.is_available())
    except Exception:    # noqa: BLE001
        pass
    return shutil.which("nvidia-smi") is not None


def _has_mps() -> bool:
    if platform.system() != "Darwin":
        return False
    try:
        import torch    # type: ignore
        return bool(getattr(torch.backends, "mps", None) and
                    torch.backends.mps.is_available())
    except Exception:    # noqa: BLE001
        return False


def pick_accelerator(order: list[str]) -> str:
    for choice in order:
        if choice == "cuda" and _has_cuda():
            return "cuda"
        if choice == "mps" and _has_mps():
            return "mps"
        if choice == "cpu":
            return "cpu"
    return "cpu"


# --------------------------------------------------------------------------- #
# Resolver                                                                    #
# --------------------------------------------------------------------------- #

def resolve_mode(*, requested: str, requested_base_model: str,
                 training_cfg: dict,
                 env: Optional[dict[str, str]] = None
                 ) -> EffectiveBackend:
    env = env if env is not None else dict(os.environ)
    if requested not in _MODES:
        raise RuntimeError(
            f"FLAGSHIP_MODE must be one of {_MODES!r}, got {requested!r}")

    modes_cfg = training_cfg.get("modes") or {}
    fallbacks: list[str] = []
    notes: list[str] = []

    if requested == "simulate":
        return EffectiveBackend(
            requested_mode=requested,
            effective_mode="simulate",
            requested_base_model=requested_base_model,
            loaded_base_model="",
            accelerator_requested="cpu",
            accelerator_effective="cpu",
            precision=modes_cfg.get("precision", {}).get("cpu", "fp32"),
            available_for_real_inference=False,
            notes=["simulate mode produces stub artifacts only"],
        )

    if requested == "lite":
        override = modes_cfg.get("lite", {}).get(
            "override_base_model_id", requested_base_model)
        if override != requested_base_model:
            notes.append(f"lite mode overrides base_model.id -> {override!r}")
        accel_order = modes_cfg.get("full", {}).get(
            "accelerator_order", ["cuda", "mps", "cpu"])
        accel = pick_accelerator(accel_order)
        return EffectiveBackend(
            requested_mode=requested,
            effective_mode="lite",
            requested_base_model=requested_base_model,
            loaded_base_model=override,
            accelerator_requested=accel_order[0],
            accelerator_effective=accel,
            precision=modes_cfg.get("precision", {}).get(accel, "fp32"),
            available_for_real_inference=False,
            notes=notes + ["lite mode is a sanity smoke; not real training"],
        )

    # full mode
    accel_order = modes_cfg.get("full", {}).get(
        "accelerator_order", ["cuda", "mps", "cpu"])
    accel = pick_accelerator(accel_order)
    if accel == "cpu":
        cpu_gate = modes_cfg.get("full", {}).get(
            "cpu_gate_env_var", "FLAGSHIP_ALLOW_CPU_REAL")
        if env.get(cpu_gate) != "1":
            raise RuntimeError(
                f"strict full mode: no accelerator available; set {cpu_gate}=1 "
                f"to allow CPU real training (it will be slow)",
            )
        fallbacks.append(f"no_accelerator;{cpu_gate}_set")
    precision = modes_cfg.get("precision", {}).get(accel, "fp32")
    return EffectiveBackend(
        requested_mode=requested,
        effective_mode="full",
        requested_base_model=requested_base_model,
        loaded_base_model=requested_base_model,
        accelerator_requested=accel_order[0],
        accelerator_effective=accel,
        precision=precision,
        fallback_reasons=fallbacks,
        available_for_real_inference=True,
        notes=notes,
    )
