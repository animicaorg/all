"""The all-in-one L2 node: constructs and holds the sequencer + store, recovers
canonical state on start, and exposes a process-wide singleton the RPC/CLI/SDK
share. Production deployments run the same object with only a subset of duties
enabled (``mode``), but the object graph is identical so nothing is rewritten to
decentralize later.
"""

from __future__ import annotations

import threading
from typing import Optional

from .config import L2Config
from .crypto import get_verifier
from .sequencer import Sequencer, SequencerConfig
from .store import L2Store


class L2Node:
    def __init__(self, config: Optional[L2Config] = None) -> None:
        self.config = config or L2Config.from_env()
        self.store = L2Store(self.config.data_dir)
        # Recover canonical state (empty at genesis).
        tree, escrows, bridge, head = self.store.recover(self.config.l2_chain_id)
        # Worker defaults are 1 deliberately. Benchmarking showed CPython's GIL
        # serializes both the pure-Python executor and the liboqs verify binding
        # (which does not release the GIL), so adding threads *reduces*
        # throughput here. The deterministic parallel executor is proven correct
        # and stays available (ANIMICA_L2_EXEC_WORKERS>1) for a future process-
        # pool / free-threaded / native backend that can actually use cores.
        # See docs/l2/PERFORMANCE.md.
        seq_cfg = SequencerConfig(
            l2_chain_id=self.config.l2_chain_id,
            settlement_mode=self.config.settlement_mode,
            exec_workers=self.config.exec_workers or 1,
            max_pending=self.config.max_pending,
            closure=self.config.closure(),
        )
        verifier = get_verifier(workers=self.config.sig_workers or 1)
        self.sequencer = Sequencer(
            seq_cfg,
            self.store,
            tree=tree,
            escrows=escrows,
            bridge=bridge,
            head_batch=head,
            verifier=verifier,
        )
        self.started = False

    # ── lifecycle ──
    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    # ── status ──
    def status(self) -> dict:
        s = self.sequencer
        return {
            "enabled": self.config.enabled,
            "mode": self.config.mode,
            "l2ChainId": self.config.l2_chain_id,
            "settlementMode": self.config.settlement_mode.value,
            "headBatch": s.batch_number,
            "stateRoot": "0x" + s.state_root().hex(),
            "pending": len(s._pending),  # noqa: SLF001 (status view)
            "sigBackend": s.verifier.backend_name,
            # L1 account users send ANM to in order to deposit into L2. Empty
            # until an operator configures ANIMICA_L2_BRIDGE_ADDRESS — deposits
            # are impossible without it, so wallets/explorer surface this.
            "bridgeAddress": self.config.bridge_address or None,
            "depositsEnabled": bool(self.config.bridge_address),
            "bridge": s.bridge.summary(),
        }


_lock = threading.Lock()
_node: Optional[L2Node] = None


def get_l2_node(config: Optional[L2Config] = None) -> L2Node:
    global _node
    with _lock:
        if _node is None:
            _node = L2Node(config)
        return _node


def set_l2_node(node: Optional[L2Node]) -> None:
    global _node
    with _lock:
        _node = node


def reset_l2_node_for_tests() -> None:
    set_l2_node(None)
