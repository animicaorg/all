from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from typer.testing import CliRunner

from animica.cli import node

runner = CliRunner()


@respx.mock
def test_status_and_head(monkeypatch: Any) -> None:
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    head_route = respx.post(rpc_url).mock(
        side_effect=[
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"height": 42, "hash": "0xabc", "chainId": 10}}),
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"transactions": []}}),
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"syncing": False}}),
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"height": 42, "hash": "0xabc", "chainId": 10}}),
        ]
    )

    status_result = runner.invoke(node.app, ["status"])
    assert status_result.exit_code == 0
    assert "Head height: 42" in status_result.output

    head_result = runner.invoke(node.app, ["head"])
    assert head_result.exit_code == 0
    data = json.loads(head_result.output)
    assert data["hash"] == "0xabc"

    assert head_route.called


@respx.mock
def test_block_and_tx(monkeypatch: Any) -> None:
    rpc_url = "http://localhost:9998/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    block_route = respx.post(rpc_url).mock(
        side_effect=[
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"height": 5, "hash": "0x123"}}),
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"hash": "0xdeadbeef"}}),
        ]
    )

    block_result = runner.invoke(node.app, ["block", "--height", "5"])
    assert block_result.exit_code == 0
    assert "0xdeadbeef" in block_result.output

    tx_route = respx.post(rpc_url).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"hash": "0xbead"}})
    )

    tx_result = runner.invoke(node.app, ["tx", "--hash", "0xbead"])
    assert tx_result.exit_code == 0
    assert "0xbead" in tx_result.output

    assert block_route.called
    assert tx_route.called
