from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional


class DeviceKind(str, Enum):
    """Supported mining device backends."""

    cpu = "cpu"
    cuda = "cuda"  # NVIDIA CUDA (optional)
    rocm = "rocm"  # AMD ROCm (optional)
    opencl = "opencl"  # Generic OpenCL (optional)
    metal = "metal"  # Apple Metal (optional)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    v = v.strip().lower()
    return v in ("1", "true", "t", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    if v is None:
        return default
    try:
        return int(v, 10)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    v = _env(name)
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


@dataclass
class MiningConfig:
    """
    Runtime configuration for the built-in miner, Stratum server, and WS getwork.

    Environment variables (all optional, with sane defaults):

      ANIMICA_MINER_DEVICE=cpu|cuda|rocm|opencl|metal
      ANIMICA_MINER_THREADS=int                       (default: os.cpu_count() or 1)
      ANIMICA_MINER_SHARE_TARGET=float                (0<target<1; difficulty ratio vs Θ; default: 1e-6)
      ANIMICA_MINER_RPC_HTTP=http://host:port/rpc     (default: http://127.0.0.1:8545/rpc)
      ANIMICA_MINER_RPC_WS=ws://host:port/ws          (default: ws://127.0.0.1:8545/ws)
      ANIMICA_MINER_CHAIN_ID=int                      (default: 1)

      ANIMICA_MINER_STRATUM_ENABLED=true|false        (default: true)
      ANIMICA_MINER_STRATUM_HOST=0.0.0.0
      ANIMICA_MINER_STRATUM_PORT=22224

      ANIMICA_MINER_WS_GETWORK_ENABLED=true|false     (default: true)
      ANIMICA_MINER_WS_GETWORK_HOST=0.0.0.0
      ANIMICA_MINER_WS_GETWORK_PORT=22225

      ANIMICA_MINER_TEMPLATE_REFRESH_SECS=float       (default: 2.0)
      ANIMICA_MINER_SUBMIT_BATCH_SIZE=int             (default: 128)

      ANIMICA_MINER_ATTACH_AI=true|false              (default: true)
      ANIMICA_MINER_ATTACH_QUANTUM=true|false         (default: true)
      ANIMICA_MINER_ATTACH_STORAGE=true|false         (default: false)
      ANIMICA_MINER_ATTACH_VDF=true|false             (default: false)

      ANIMICA_MINER_METRICS_ENABLED=true|false        (default: true)
      ANIMICA_MINER_METRICS_HOST=127.0.0.1
      ANIMICA_MINER_METRICS_PORT=9106

      ANIMICA_MINER_RNG_SEED=hex|string               (optional; for deterministic tests ONLY)

    Notes
    - share_target is a *micro-target* difficulty ratio for HashShare proofs relative to Θ.
      Smaller values are harder (fewer shares). For devnets, 1e-6 … 1e-4 is typical.
    - AI/Quantum/Storage/VDF toggles control whether the miner attempts to attach those
      useful proofs to candidate blocks using the workers (see mining/*_worker.py).
    """

    # Device / threads
    device: DeviceKind = DeviceKind.cpu
    threads: int = max(os.cpu_count() or 1, 1)

    # HashShare micro-target (difficulty ratio vs Θ). 0<target<1.
    share_target: float = 1e-6

    # Node endpoints & chain id
    rpc_http_url: str = "http://127.0.0.1:8545/rpc"
    rpc_ws_url: str = "ws://127.0.0.1:8545/ws"
    chain_id: int = 1

    # Stratum TCP server
    stratum_enabled: bool = True
    stratum_host: str = "0.0.0.0"
    stratum_port: int = 22224

    # WS getwork server
    ws_getwork_enabled: bool = True
    ws_getwork_host: str = "0.0.0.0"
    ws_getwork_port: int = 22225

    # Template refresh & submit behavior
    template_refresh_secs: float = 2.0
    submit_batch_size: int = 128

    # Useful-work attachment toggles
    attach_ai: bool = True
    attach_quantum: bool = True
    attach_storage: bool = False
    attach_vdf: bool = False

    # Metrics
    metrics_enabled: bool = True
    metrics_host: str = "127.0.0.1"
    metrics_port: int = 9106

    # Deterministic testing seed (NEVER use in production)
    rng_seed: Optional[str] = None

    @classmethod
    def from_env(cls) -> "MiningConfig":
        dev = _env("ANIMICA_MINER_DEVICE", "cpu").lower()
        device = (
            DeviceKind(dev)
            if dev in DeviceKind.__members__.keys()
            or dev in [d.value for d in DeviceKind]
            else DeviceKind.cpu
        )

        cfg = cls(
            device=device,
            threads=max(1, _env_int("ANIMICA_MINER_THREADS", os.cpu_count() or 1)),
            share_target=_env_float("ANIMICA_MINER_SHARE_TARGET", 1e-6),
            rpc_http_url=_env("ANIMICA_MINER_RPC_HTTP", "http://127.0.0.1:8545/rpc"),
            rpc_ws_url=_env("ANIMICA_MINER_RPC_WS", "ws://127.0.0.1:8545/ws"),
            chain_id=_env_int("ANIMICA_MINER_CHAIN_ID", 1),
            stratum_enabled=_env_bool("ANIMICA_MINER_STRATUM_ENABLED", True),
            stratum_host=_env("ANIMICA_MINER_STRATUM_HOST", "0.0.0.0"),
            stratum_port=_env_int("ANIMICA_MINER_STRATUM_PORT", 22224),
            ws_getwork_enabled=_env_bool("ANIMICA_MINER_WS_GETWORK_ENABLED", True),
            ws_getwork_host=_env("ANIMICA_MINER_WS_GETWORK_HOST", "0.0.0.0"),
            ws_getwork_port=_env_int("ANIMICA_MINER_WS_GETWORK_PORT", 22225),
            template_refresh_secs=_env_float(
                "ANIMICA_MINER_TEMPLATE_REFRESH_SECS", 2.0
            ),
            submit_batch_size=_env_int("ANIMICA_MINER_SUBMIT_BATCH_SIZE", 128),
            attach_ai=_env_bool("ANIMICA_MINER_ATTACH_AI", True),
            attach_quantum=_env_bool("ANIMICA_MINER_ATTACH_QUANTUM", True),
            attach_storage=_env_bool("ANIMICA_MINER_ATTACH_STORAGE", False),
            attach_vdf=_env_bool("ANIMICA_MINER_ATTACH_VDF", False),
            metrics_enabled=_env_bool("ANIMICA_MINER_METRICS_ENABLED", True),
            metrics_host=_env("ANIMICA_MINER_METRICS_HOST", "127.0.0.1"),
            metrics_port=_env_int("ANIMICA_MINER_METRICS_PORT", 9106),
            rng_seed=_env("ANIMICA_MINER_RNG_SEED"),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        # Threads
        if self.threads < 1:
            raise ValueError("threads must be >= 1")
        # Share target within (0,1)
        if not (0.0 < self.share_target < 1.0):
            raise ValueError("share_target must be in the open interval (0, 1).")
        # Ports
        for name, port in (
            ("stratum_port", self.stratum_port),
            ("ws_getwork_port", self.ws_getwork_port),
            ("metrics_port", self.metrics_port),
        ):
            if not (0 < port < 65536):
                raise ValueError(f"{name} must be a valid TCP port (1..65535)")
        # Chain id
        if self.chain_id <= 0:
            raise ValueError("chain_id must be a positive integer")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Convenience singleton for simple scripts:
DEFAULT_CONFIG = MiningConfig.from_env()

__all__ = ["MiningConfig", "DeviceKind", "DEFAULT_CONFIG"]
