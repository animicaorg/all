from __future__ import annotations

from rpc.tests import new_test_client, rpc_call


def test_chain_id_and_params_agree_with_config():
    client, cfg, _ = new_test_client()

    chain_id_res = rpc_call(client, "chain.getChainId")
    params_res = rpc_call(client, "chain.getParams")

    assert chain_id_res["result"] == cfg.chain_id
    assert params_res["result"].get("chainId") == cfg.chain_id

