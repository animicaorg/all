from __future__ import annotations

import json
from pathlib import Path

from rpc.tests import fetch_openrpc, new_test_client


def test_openrpc_matches_repo_spec():
    """
    The RPC server should serve the repository OpenRPC document verbatim. This
    keeps method names and schemas stable for SDKs and wallets.
    """

    client, _, _ = new_test_client()
    served = fetch_openrpc(client)

    spec_path = Path("spec/openrpc.json")
    local = json.loads(spec_path.read_text())

    assert served["openrpc"] == local["openrpc"]

    served_methods = {m["name"] for m in served.get("methods", [])}
    local_methods = {m["name"] for m in local.get("methods", [])}

    # Method set must match exactly so downstream codegen stays deterministic
    assert served_methods == local_methods

    # Spot-check a few critical endpoints exist
    for required in (
        "chain_getChainId",
        "chain_getHead",
        "tx_sendRawTransaction",
        "tx_getTransactionByHash",
        "da_getBlob",
    ):
        assert required in served_methods, f"Missing OpenRPC method: {required}"
