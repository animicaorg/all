"""L2 node configuration — env-driven, following the repo's ANIMICA_* convention.

See ``docs/l2/RUNNING.md``. Every knob has a safe default so ``ANIMICA_L2_ENABLE=1``
alone brings up a working all-in-one dev node.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from .batch import ClosurePolicy
from .constants import (
    BRIDGE_ADDRESS_ENV,
    DEFAULT_BATCH_MAX_BYTES,
    DEFAULT_BATCH_MAX_MS,
    DEFAULT_BATCH_MAX_TXS,
    L2_CHAIN_ID_DEVNET,
    L2_CHAIN_ID_MAINNET,
    SettlementMode,
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return int(v, 0)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class L2Config:
    enabled: bool = False
    mode: str = "all"  # sequencer | node | prover | all
    l2_chain_id: int = L2_CHAIN_ID_DEVNET
    settlement_mode: SettlementMode = SettlementMode.VALIDITY
    data_dir: str = "/data/l2"
    rpc_port: int = 8551
    p2p_port: int = 8552
    settlement_enabled: bool = False
    exec_workers: int = 0  # 0 = auto
    sig_workers: int = 0
    proof_workers: int = 1
    batch_max_txs: int = DEFAULT_BATCH_MAX_TXS
    batch_max_ms: int = DEFAULT_BATCH_MAX_MS
    batch_max_bytes: int = DEFAULT_BATCH_MAX_BYTES
    tick_interval_ms: int = 25
    max_pending: int = 500_000
    bridge_address: str = ""
    l1_rpc_url: str = "http://127.0.0.1:8545"

    def closure(self) -> ClosurePolicy:
        return ClosurePolicy(
            max_txs=self.batch_max_txs,
            max_bytes=self.batch_max_bytes,
            max_age_ms=self.batch_max_ms,
        )

    @staticmethod
    def from_env() -> "L2Config":
        network = _env("ANIMICA_NETWORK", "devnet").lower()
        default_chain = L2_CHAIN_ID_MAINNET if network == "mainnet" else L2_CHAIN_ID_DEVNET
        mode_str = _env("ANIMICA_L2_SETTLEMENT_MODE", "VALIDITY").upper()
        try:
            settlement = SettlementMode(mode_str)
        except ValueError:
            settlement = SettlementMode.VALIDITY
        data_dir = _env("ANIMICA_L2_DATA_DIR") or os.path.join(
            _env("ANIMICA_DATA_DIR", "/data"), "l2"
        )
        return L2Config(
            enabled=_env_bool("ANIMICA_L2_ENABLE", False),
            mode=_env("ANIMICA_L2_MODE", "all").lower(),
            l2_chain_id=_env_int("ANIMICA_L2_CHAIN_ID", default_chain),
            settlement_mode=settlement,
            data_dir=data_dir,
            rpc_port=_env_int("ANIMICA_L2_RPC_PORT", 8551),
            p2p_port=_env_int("ANIMICA_L2_P2P_PORT", 8552),
            settlement_enabled=_env_bool("ANIMICA_L2_SETTLEMENT_ENABLED", False),
            exec_workers=_env_int("ANIMICA_L2_EXEC_WORKERS", 0),
            sig_workers=_env_int("ANIMICA_L2_SIG_WORKERS", 0),
            proof_workers=_env_int("ANIMICA_L2_PROOF_WORKERS", 1),
            batch_max_txs=_env_int("ANIMICA_L2_BATCH_MAX_TXS", DEFAULT_BATCH_MAX_TXS),
            batch_max_ms=_env_int("ANIMICA_L2_BATCH_MAX_MS", DEFAULT_BATCH_MAX_MS),
            batch_max_bytes=_env_int("ANIMICA_L2_BATCH_MAX_BYTES", DEFAULT_BATCH_MAX_BYTES),
            tick_interval_ms=_env_int("ANIMICA_L2_TICK_MS", 25),
            max_pending=_env_int("ANIMICA_L2_MAX_PENDING", 500_000),
            bridge_address=_env(BRIDGE_ADDRESS_ENV, ""),
            l1_rpc_url=_env("ANIMICA_L2_L1_RPC_URL", "http://127.0.0.1:8545"),
        )
