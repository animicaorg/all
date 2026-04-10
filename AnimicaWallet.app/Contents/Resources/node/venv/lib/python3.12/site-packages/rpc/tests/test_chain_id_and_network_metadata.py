from __future__ import annotations

from rpc.tests import new_test_client, rpc_call


def test_chain_id_and_params_agree_with_config():
    client, cfg, _ = new_test_client()

    chain_id_res = rpc_call(client, "chain.getChainId")
    params_res = rpc_call(client, "chain.getParams")

    assert chain_id_res["result"] == cfg.chain_id
    assert params_res["result"].get("chainId") == cfg.chain_id


def test_chain_identity_reports_genesis_fork_and_protocol():
    client, cfg, _ = new_test_client()

    identity_res = rpc_call(client, "chain.getChainIdentity")
    identity = identity_res["result"]

    assert identity["chainId"] == cfg.chain_id
    assert identity["genesisHash"].startswith("0x")
    assert isinstance(identity["forkId"], int)
    assert identity["consensusId"]
    assert identity["protocolVersion"]
