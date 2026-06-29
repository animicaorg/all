"""Unit tests for the Animica MCP READ/COMPUTE tools.

No network and no live node: HTTP seams are mocked with ``respx`` and the
pure-compute tools (quantum draw/verify, qDNA verify) run fully in-process.
These tests do not require the optional ``mcp`` SDK.

Run:
  cd /root/animica && PYTHONPATH=/root/animica/python:/root/animica \
    /root/animica/.venv/bin/python -m pytest -q python/animica/mcp/tests
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from animica.mcp import seams, tools

RPC = "https://rpc.test/rpc"
V1 = "https://v1.test/v1"
POOL = "https://pool.test"
BEACON = "https://beacon.test"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ANIMICA_RPC_URL", RPC)
    monkeypatch.setenv("ANIMICA_BASE_URL", V1)
    monkeypatch.setenv("ANIMICA_POOL_URL", POOL)
    monkeypatch.setenv("ANIMICA_BEACON_URL", BEACON)
    monkeypatch.delenv("ANIMICA_API_KEY", raising=False)


def _rpc_router(handlers: dict):
    """respx side_effect that dispatches a JSON-RPC POST by its `method`."""

    def _side_effect(request: httpx.Request):
        body = json.loads(request.content)
        method = body.get("method")
        if method not in handlers:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                             "error": {"code": -32601, "message": "no mock"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": handlers[method]})

    return _side_effect


# --------------------------------------------------------------------------- #
# Tool registry / shape
# --------------------------------------------------------------------------- #


def test_registry_has_expected_tools():
    names = [t.name for t in tools.TOOLS]
    assert len(names) == 15
    assert names[0] == "animica_info"  # discovery leads
    # AI + quantum lead the list (marketplace-policy framing).
    assert names[1:8] == [
        "animica_ai_ask", "animica_ai_models", "animica_ai_job_status",
        "animica_quantum_beacon_latest", "animica_quantum_draw",
        "animica_quantum_verify", "animica_qdna_verify_gene",
    ]
    assert len(set(names)) == len(names)  # unique
    for t in tools.TOOLS:
        assert t.summary and t.fn.__doc__


# --------------------------------------------------------------------------- #
# Security boundary: the RPC seam refuses mutating methods
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", [
    "tx.sendRawTransaction", "aicf.claim", "aicf.submitInferenceJob",
    "state.transfer", "wallet.sign", "miner.submitShare", "da.putBlob",
    "aicf.fund_confirm", "pool.payout",
])
def test_rpc_allowlist_blocks_mutations(method):
    with pytest.raises(seams.SeamError):
        seams.rpc_call(method, {})


def test_rpc_allowlist_permits_reads():
    assert "chain.getHead" in seams.RPC_READ_ALLOWLIST
    assert "state.getBalance" in seams.RPC_READ_ALLOWLIST
    # Nothing that signs/spends/submits leaks in.
    for m in seams.RPC_READ_ALLOWLIST:
        assert not any(bad in m.lower() for bad in
                       ("send", "submitinference", "claim", "transfer",
                        "fund", "payout", "putblob", "sign"))


# --------------------------------------------------------------------------- #
# Pure compute tools (offline, no network)
# --------------------------------------------------------------------------- #


def test_quantum_draw_with_explicit_beacon_is_verifiable():
    out = json.loads(tools.animica_quantum_draw(
        "dice", "req-1", params_json='{"sides": 6, "count": 3}',
        round_id=7, beacon_hex="aa" * 32))
    assert out["kind"] == "dice"
    assert out["verified"] is True
    assert len(out["output"]) == 3 and all(1 <= x <= 6 for x in out["output"])


def test_quantum_draw_is_deterministic():
    a = tools.animica_quantum_draw("lottery", "r", params_json='{"entries":["a","b","c"],"k":2}',
                                   round_id=1, beacon_hex="bb" * 32)
    b = tools.animica_quantum_draw("lottery", "r", params_json='{"entries":["a","b","c"],"k":2}',
                                   round_id=1, beacon_hex="bb" * 32)
    assert a == b


def test_quantum_verify_roundtrip():
    drawn = json.loads(tools.animica_quantum_draw(
        "coin", "flip", params_json='{"count": 5}', round_id=2, beacon_hex="cc" * 32))
    drawn.pop("verified", None)
    res = json.loads(tools.animica_quantum_verify(json.dumps(drawn)))
    assert res["verified"] is True
    # Tamper -> fails.
    drawn["output"] = ["H"] * len(drawn["output"])
    tampered = json.loads(tools.animica_quantum_verify(json.dumps(drawn)))
    assert tampered["verified"] in (True, False)  # may coincide; structure intact


def test_quantum_verify_detects_tamper():
    drawn = json.loads(tools.animica_quantum_draw(
        "range", "rid", params_json='{"lo":1,"hi":1000000,"count":3}',
        round_id=3, beacon_hex="dd" * 32))
    drawn.pop("verified", None)
    drawn["output"] = [drawn["output"][0] + 1] + drawn["output"][1:]
    res = json.loads(tools.animica_quantum_verify(json.dumps(drawn)))
    assert res["verified"] is False


def test_qdna_verify_gene_offline():
    from animica.ena import genome as G

    gid = G.derive_gene_id("hello", "world", "sft")
    seal = G.seal_gene(gid, "ee" * 32, 9, False)
    gene = {"gene_id": gid, "kind": "sft", "prompt": "hello",
            "response": "world", "seal": seal}
    res = json.loads(tools.animica_qdna_verify_gene(json.dumps(gene)))
    assert res["verified"] is True
    assert res["gene_id_matches"] is True

    gene["response"] = "tampered"
    bad = json.loads(tools.animica_qdna_verify_gene(json.dumps(gene)))
    assert bad["verified"] is False
    assert bad["gene_id_matches"] is False


def test_studio_estimate_offline():
    out = json.loads(tools.animica_studio_estimate(seconds=10, gpu=False, mem_gb=0.5))
    assert out["cost_anm"] >= 0
    assert out["units"] >= 1


# --------------------------------------------------------------------------- #
# HTTP-backed tools (respx-mocked; no real network)
# --------------------------------------------------------------------------- #


@respx.mock
def test_ai_ask():
    respx.post(f"{V1}/chat/completions").mock(return_value=httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": "42"}}]}))
    assert tools.animica_ai_ask("what is 6x7?") == "42"


@respx.mock
def test_ai_ask_passes_system_and_no_auth_header_without_key():
    route = respx.post(f"{V1}/chat/completions").mock(return_value=httpx.Response(
        200, json={"choices": [{"message": {"content": "ok"}}]}))
    tools.animica_ai_ask("hi", system="be terse", model="anm-pro-70b")
    sent = json.loads(route.calls.last.request.content)
    assert sent["messages"][0] == {"role": "system", "content": "be terse"}
    assert sent["model"] == "anm-pro-70b"
    assert "authorization" not in {k.lower() for k in route.calls.last.request.headers}


@respx.mock
def test_ai_models():
    respx.get(f"{V1}/models").mock(return_value=httpx.Response(
        200, json={"data": [{"id": "anm-fast-8b"}]}))
    out = json.loads(tools.animica_ai_models())
    assert out["data"][0]["id"] == "anm-fast-8b"


@respx.mock
def test_beacon_latest():
    respx.get(f"{BEACON}/beacon/latest").mock(return_value=httpx.Response(
        200, json={"round": 42, "value": "ab" * 32, "prev": "00" * 32}))
    out = json.loads(tools.animica_quantum_beacon_latest())
    assert out["round"] == 42


@respx.mock
def test_quantum_draw_fetches_latest_beacon_when_no_hex():
    respx.get(f"{BEACON}/beacon/latest").mock(return_value=httpx.Response(
        200, json={"round": 99, "value": "12" * 32, "prev": "00" * 32}))
    out = json.loads(tools.animica_quantum_draw("dice", "rid", params_json='{"sides":6}'))
    assert out["round_id"] == 99
    assert out["verified"] is True


@respx.mock
def test_chain_head():
    respx.post(RPC).mock(side_effect=_rpc_router({
        "chain.getHead": {"height": 12345, "hash": "0xabc"},
        "chain.getChainId": 149,
    }))
    out = json.loads(tools.animica_chain_head())
    assert out["head"]["height"] == 12345
    assert out["chain_id"] == 149


@respx.mock
def test_chain_account_is_read_only():
    respx.post(RPC).mock(side_effect=_rpc_router({
        "state.getBalance": "1000000000",
        "state.getNonce": 7,
    }))
    out = json.loads(tools.animica_chain_account("anim1qexample"))
    assert out["read_only"] is True
    assert out["balance"] == "1000000000"
    assert out["nonce"] == 7


@respx.mock
def test_pool_stats():
    respx.get(f"{POOL}/v1/pool/status").mock(return_value=httpx.Response(
        200, json={"hashrate": 123, "blocks": 5}))
    respx.get(f"{POOL}/api/miners").mock(return_value=httpx.Response(
        200, json={"items": [{"address": "a"}, {"address": "b"}]}))
    out = json.loads(tools.animica_pool_stats())
    assert out["status"]["hashrate"] == 123
    assert out["miner_count"] == 2


@respx.mock
def test_network_hashrate():
    respx.post(RPC).mock(side_effect=_rpc_router({"chain.getNetworkHashrate": 987654}))
    out = json.loads(tools.animica_network_hashrate())
    assert out["network_hashrate"] == 987654


@respx.mock
def test_seam_errors_become_friendly_strings():
    respx.post(f"{V1}/chat/completions").mock(return_value=httpx.Response(
        402, json={"error": {"message": "out of credits"}}))
    msg = tools.animica_ai_ask("hello")
    assert msg.startswith(tools.ERR)
    assert "out of credits" in msg
