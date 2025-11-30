from __future__ import annotations

import pytest

from rpc.tests import new_test_client, rpc_call


def test_get_params_returns_chain_config():
    client, cfg, _ = new_test_client()
    res = rpc_call(client, "chain.getParams")
    assert res["jsonrpc"] == "2.0"
    p = res["result"]
    # Basic shape checks (don't couple to every field so genesis edits won't break the test)
    assert isinstance(p, dict)
    assert p.get("chainId") == cfg.chain_id
    assert "name" in p and isinstance(p["name"], str)
    assert "consensus" in p and isinstance(p["consensus"], dict)
    assert "gas" in p and isinstance(p["gas"], dict)
    assert "block" in p and isinstance(p["block"], dict)


def test_get_head_is_genesis_after_boot():
    client, cfg, _ = new_test_client()
    res = rpc_call(client, "chain.getHead")
    head = res["result"]
    assert head["number"] == 0, f"expected genesis at height 0, got {head}"
    assert isinstance(head["hash"], str) and head["hash"].startswith("0x")
    assert head["chainId"] == cfg.chain_id
    # A few required header roots should exist
    for k in ("stateRoot", "txsRoot", "receiptsRoot", "proofsRoot", "daRoot"):
        assert k in head and isinstance(head[k], str) and head[k].startswith("0x")


def test_get_block_by_number_genesis_no_txs():
    client, cfg, _ = new_test_client()
    # includeTx false
    res0 = rpc_call(
        client,
        "chain.getBlockByNumber",
        params={"number": 0, "includeTx": False, "includeReceipts": False},
    )
    blk = res0["result"]
    assert blk["header"]["number"] == 0
    assert blk["header"]["chainId"] == cfg.chain_id
    assert blk.get("transactions") in (
        [],
        None,
    )  # some servers may omit when includeTx=False

    # includeTx true (should be empty list for genesis)
    res1 = rpc_call(
        client,
        "chain.getBlockByNumber",
        params={"number": 0, "includeTx": True, "includeReceipts": True},
    )
    blk1 = res1["result"]
    assert isinstance(blk1.get("transactions", []), list)
    assert len(blk1.get("transactions", [])) == 0
    # Receipts list (if present) should match len(txs)
    if "receipts" in blk1:
        assert isinstance(blk1["receipts"], list)
        assert len(blk1["receipts"]) == 0


def test_get_block_by_hash_roundtrip_and_equality():
    client, _, _ = new_test_client()

    # Fetch by number → get hash
    by_num = rpc_call(
        client,
        "chain.getBlockByNumber",
        params={"number": 0, "includeTx": False, "includeReceipts": False},
    )["result"]
    h = by_num["header"]["hash"] if "hash" in by_num["header"] else None
    if h is None:
        # Some servers place the hash at the top-level in the block view
        h = by_num.get("hash")
    assert isinstance(h, str) and h.startswith("0x")

    # Fetch by hash
    by_hash = rpc_call(
        client,
        "chain.getBlockByHash",
        params={"hash": h, "includeTx": False, "includeReceipts": False},
    )["result"]

    # Sanity: same height and (if exposed) same hash
    assert by_hash["header"]["number"] == by_num["header"]["number"]
    h2 = by_hash["header"].get("hash", by_hash.get("hash"))
    if h2 is not None:
        assert h2 == h


def test_nonexistent_block_returns_null():
    client, _, _ = new_test_client()
    # Very high height shouldn't exist in a fresh temp DB
    res = rpc_call(
        client,
        "chain.getBlockByNumber",
        params={"number": 999999, "includeTx": False, "includeReceipts": False},
    )
    assert res["result"] is None


def test_head_and_block_views_are_consistent():
    """
    Cross-check that chain.getHead().hash matches the block hash via getBlockByNumber.
    """
    client, _, _ = new_test_client()
    head = rpc_call(client, "chain.getHead")["result"]
    h_head = head["hash"]
    height = head["number"]

    blk = rpc_call(
        client,
        "chain.getBlockByNumber",
        params={"number": height, "includeTx": False, "includeReceipts": False},
    )["result"]

    h_block = blk["header"].get("hash", blk.get("hash"))
    assert h_block == h_head


def test_eth_aliases_resolve():
    client, cfg, _ = new_test_client()

    chain_id_res = rpc_call(client, "eth_chainId")
    assert chain_id_res["result"] == cfg.chain_id

    blk_res = rpc_call(
        client,
        "eth_getBlockByNumber",
        params={"number": 0, "includeTx": False, "includeReceipts": False},
    )
    blk = blk_res["result"]
    assert blk["header"]["number"] == 0
