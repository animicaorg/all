from __future__ import annotations

import os
from pathlib import Path

from core.utils.hash import sha3_256
from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService
from rpc.methods import tx as tx_methods


def test_reconcile_pending_pool_removes_block_txs(tmp_path: Path) -> None:
    os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
    genesis_path = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"
    sync_deps = P2PDeps.open(f"sqlite:///{tmp_path}/pending.db", str(genesis_path))
    service = P2PService(
        listen_addrs=[],
        seeds=[],
        chain_id=sync_deps.chain_id,
        deps=sync_deps,
        peerstore_path=str(tmp_path / "p2p"),
    )
    raw_tx = b"pending_tx_bytes"
    tx_hash = "0x" + sha3_256(raw_tx).hex()

    tx_methods._pending_put(tx_hash, raw_tx)
    assert tx_methods._pending_get(tx_hash) == raw_tx

    removed = service._reconcile_pending_pool({"txs": [raw_tx]})
    assert removed == 1
    assert tx_methods._pending_get(tx_hash) is None
