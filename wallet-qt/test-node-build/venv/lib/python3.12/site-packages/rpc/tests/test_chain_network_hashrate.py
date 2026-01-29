from __future__ import annotations

import math

import pytest

from core.types.block import Block
from core.utils.hash import ZERO32
from rpc import deps
from rpc.hashrate import difficulty_to_work, work_to_hashshare_rate
from rpc.tests import new_test_client, rpc_call


def _append_block(block_db, parent_header, *, timestamp: int, theta_micro: int):
    header = parent_header.build_child(
        timestamp=int(timestamp),
        state_root=ZERO32,
        txs_root=ZERO32,
        receipts_root=ZERO32,
        proofs_root=ZERO32,
        da_root=ZERO32,
        theta_micro=int(theta_micro),
    )
    block = Block.from_components(header=header, txs=(), proofs=(), receipts=None)
    block_db.append_canonical_block(header.height, block)
    return header


def test_difficulty_to_work_conversion():
    theta_micro = 1_000_000
    expected = math.e
    assert difficulty_to_work(theta_micro) == pytest.approx(expected, rel=1e-6)


def test_chain_get_network_hashrate_computes_from_headers():
    client, _, _ = new_test_client()
    ctx = deps.get_ctx()
    block_db = ctx.block_db
    genesis = block_db.get_header_by_height(0)
    assert genesis is not None

    theta_micro = 1_000_000
    base_ts = int(getattr(genesis, "timestamp", 0) or 0)
    header1 = _append_block(
        block_db, genesis, timestamp=base_ts + 10, theta_micro=theta_micro
    )
    header2 = _append_block(
        block_db, header1, timestamp=base_ts + 20, theta_micro=theta_micro
    )

    res = rpc_call(client, "chain.getNetworkHashrate", params={"window_blocks": 3})
    result = res["result"]
    assert result["window_blocks"] == 3
    assert result["height_start"] == 0
    assert result["height_end"] == 2
    assert result["hashrate_hsps"] is not None

    expected_work = 3 * difficulty_to_work(theta_micro)
    dt = header2.timestamp - genesis.timestamp
    assert result["hashrate_hsps"] == pytest.approx(
        work_to_hashshare_rate(expected_work / dt), rel=1e-6
    )


def test_chain_get_network_hashrate_insufficient_blocks():
    client, _, _ = new_test_client()
    res = rpc_call(client, "chain.getNetworkHashrate", params={"window_blocks": 10})
    result = res["result"]
    assert result["hashrate_hsps"] is None
    assert result["unknown_reason"] == "insufficient_blocks"
