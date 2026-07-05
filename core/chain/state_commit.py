"""ANM-C03: canonical, deterministic block-commitment roots.

The header carries stateRoot / txsRoot / receiptsRoot, but historically the
importer never compared them to the block's actual transactions or post-execution
state (txsRoot/stateRoot equality checks were absent on the import path). A peer
could therefore serve a PoW-valid header with a *different* transaction set
(txsRoot unchecked) or apply an invalid state transition (stateRoot unchecked)
and honest nodes would silently diverge.

These helpers recompute the roots deterministically so block import can verify the
header's commitments. Verification is wired forward-only and height-gated in
core.chain.block_import (see core.network_params.FORK_ROOT_COMMITMENT); this module
is pure computation with no policy.
"""
from __future__ import annotations

from typing import Any

from core.utils.hash import ZERO32
from core.utils.merkle import compute_txs_root_from_txs, kv_merkle_root


def compute_state_root(state_db: Any) -> bytes:
    """Deterministic root over all accounts: kv_merkle_root(addr -> Account.to_cbor()).

    kv_merkle_root sorts by key (account address), so the result is independent of
    iteration order. Accounts are the whole of consensus state on a contract-free
    chain (the VM is fail-closed, ANM-C05/C06); if contract storage is later
    enabled it must be folded into each account leaf before that activation.
    Returns ZERO32 for empty state.
    """
    items: list[tuple[bytes, bytes]] = []
    for addr, acc in state_db.iter_accounts():
        items.append((bytes(addr), acc.to_cbor()))
    if not items:
        return ZERO32
    return kv_merkle_root(items)


def compute_block_txs_root(block: Any) -> bytes:
    """Canonical txsRoot over the block's transactions (matches the miner/header
    packer and core.types.block.Block.txs_root: merkle over sorted tx.hash())."""
    txs = list(getattr(block, "txs", ()) or ())
    if not txs:
        return ZERO32
    return compute_txs_root_from_txs(txs)
