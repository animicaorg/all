"""Chat-client defaults to streaming pipeline decode.

These tests pin the contract between the chat REPL / DistributedAICFProvider
and the AICF JSON-RPC server: every chat turn submits with
``mode="auto"`` + ``decode_mode="streaming"`` unless the caller (or an
env var) flips it. Old nodes that don't recognise the new fields keep
working because the client only forwards them when non-None.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Mapping

import pytest


# Make the bundled agent_runtime importable without installing the wheel.
sys.path.insert(0, "ai/agent_runtime/src")
from agent_runtime.aicf_client import AICFClient, JobSpec    # noqa: E402


class _FakeRPC:
    """Captures the last JSON-RPC submitInferenceJob payload and returns
    a fixed JobSubmitResult-shaped dict, so we can assert the chat
    client sent exactly what we expected without spinning up a node."""

    def __init__(self, response: Mapping[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._response = dict(response or {
            "job_id": "0xabc",
            "accepted_tier": "standard",
            "provider_id": "",
            "mode": "pipeline",
            "stages": 2,
            "payment_accepted": True,
        })

    def __call__(self, method: str, params: Any) -> Any:
        self.calls.append((method, dict(params or {})))
        return self._response


def _client_with(rpc: _FakeRPC) -> AICFClient:
    client = AICFClient(endpoint="http://nope.example.invalid")
    # Bypass the real network — swap the bound RPC dispatcher.
    client._rpc = rpc    # type: ignore[assignment]
    return client


def test_submit_omits_pipeline_fields_when_none():
    """Default JobSpec (no mode/stages/decode_mode set) does NOT forward
    those keys — old nodes that pre-date the routing keys keep working."""
    rpc = _FakeRPC()
    c = _client_with(rpc)
    c.submit(JobSpec(prompt="hi"), signed_payment={"txn_hex": ""})
    method, params = rpc.calls[0]
    assert method == "aicf.submitInferenceJob"
    spec_sent = params["spec"]
    assert "mode" not in spec_sent
    assert "stages" not in spec_sent
    assert "decode_mode" not in spec_sent


def test_submit_forwards_pipeline_fields_when_set():
    rpc = _FakeRPC()
    c = _client_with(rpc)
    c.submit(
        JobSpec(prompt="hi", mode="auto", stages=4, decode_mode="streaming"),
        signed_payment={"txn_hex": ""},
    )
    _method, params = rpc.calls[0]
    spec_sent = params["spec"]
    assert spec_sent["mode"] == "auto"
    assert spec_sent["stages"] == 4
    assert spec_sent["decode_mode"] == "streaming"


def test_submit_result_surfaces_mode_and_stages():
    rpc = _FakeRPC(response={
        "job_id": "0xdef",
        "accepted_tier": "standard",
        "provider_id": "",
        "mode": "pipeline",
        "stages": 3,
    })
    c = _client_with(rpc)
    result = c.submit(
        JobSpec(prompt="hi", mode="auto", decode_mode="streaming"),
        signed_payment={"txn_hex": ""},
    )
    assert result.mode == "pipeline"
    assert result.stages == 3


def test_distributed_provider_defaults_to_streaming_pipeline():
    """When the chat client doesn't override, the request hits the node
    as ``mode="auto"`` + ``decode_mode="streaming"``. We bypass the
    payment / wallet machinery by reaching directly into the provider's
    ``client`` and observing what's serialised."""
    from agent_runtime.providers import DistributedAICFProvider, TurnRequest
    from agent_runtime.wallet import WalletInfo

    # Build a barely-functional provider — no real cfg, just enough to
    # exercise the spec construction path.
    class _Cfg:
        integration = {"aicf": {
            "job_submit": {
                "timeout_sec": 1.0,
                "poll_interval_ms": 50,
                "max_retries": 0,
                "retry_backoff_ms": [10],
            },
            "treasury_address": "anim1placeholder",
        }}
        model_catalog = {"routing": {"default_tier": "standard"}}
        repo_root = "/tmp"

    rpc = _FakeRPC(response={
        "job_id": "0x111",
        "accepted_tier": "standard",
        "provider_id": "",
        "mode": "pipeline",
        "stages": 2,
        "payment_accepted": True,
    })
    prov = DistributedAICFProvider(
        cfg=_Cfg(), rpc_url="http://nope.example.invalid",
        wallet_path=None, wallet_label=None,
    )
    prov.client._rpc = rpc    # type: ignore[assignment]
    # Bypass wallet / estimate / sign / settle by stubbing each call site.
    prov._wallet = WalletInfo(
        address="anim1xxx", chain_id=1,
        balance_animica=10.0, balance_lookup_ok=True,
        balance_lookup_error="",
    )
    # estimate_cost and settle call _rpc too; the fake returns the same
    # dict regardless of method, which is fine: estimateJobCost reads
    # cost_animica / latency_ms / tier / providers, all default to 0.
    # The chat provider's serve() flow:
    #   1. estimate_cost  → uses rpc fake (returns mostly-zero)
    #   2. preview_payment + sign_payment  → patch to no-op
    #   3. submit  → captured here
    #   4. stream + settle  → also via fake; we just need them to no-op
    # To keep the test focused on the submit payload, swap submit to
    # tap the spec and short-circuit.
    captured: dict = {}

    real_submit = prov.client.submit

    def _spy(spec, *, signed_payment):
        captured["mode"] = spec.mode
        captured["decode_mode"] = spec.decode_mode
        captured["stages"] = spec.stages
        return real_submit(spec, signed_payment=signed_payment)

    prov.client.submit = _spy    # type: ignore[assignment]

    # Stub the wallet payment helpers too — they would otherwise call
    # the real signing infra.
    import agent_runtime.providers as providers_mod
    providers_mod.get_next_nonce = lambda *a, **k: 0
    providers_mod.preview_payment = lambda wi, amount: type(
        "P", (), {"sufficient": True, "reason": None}
    )()

    class _Signed:
        def __init__(self) -> None:
            self.__dict__ = {"txn_hex": ""}
    providers_mod.sign_payment = lambda *a, **k: _Signed()

    # And the stream + settle calls (they read from the same fake rpc).
    prov.client.stream = lambda *a, **k: iter([])    # type: ignore[assignment]

    class _Settle:
        text = "answer"
        actual_cost_animica = 0.0
        provider_id = ""
        latency_ms = 0
    prov.client.settle = lambda *a, **k: _Settle()    # type: ignore[assignment]

    req = TurnRequest(prompt="what is animica")
    result = prov.serve(req)
    assert captured["mode"] == "auto", captured
    assert captured["decode_mode"] == "streaming", captured
    # Per-turn metadata reflects the node's resolved routing.
    assert result.metadata["routing_mode"] == "pipeline"
    assert result.metadata["pipeline_stages"] == 2
    assert result.metadata["decode_mode"] == "streaming"


def test_env_overrides_decode_mode_default(monkeypatch):
    """ANIMICA_AICF_CHAT_DECODE_MODE flips the session default without
    requiring a code change — useful when migrating between node
    versions during a rollout."""
    from agent_runtime.providers import DistributedAICFProvider, TurnRequest
    from agent_runtime.wallet import WalletInfo

    class _Cfg:
        integration = {"aicf": {
            "job_submit": {
                "timeout_sec": 1.0,
                "poll_interval_ms": 50,
                "max_retries": 0,
                "retry_backoff_ms": [10],
            },
            "treasury_address": "anim1placeholder",
        }}
        model_catalog = {"routing": {"default_tier": "standard"}}
        repo_root = "/tmp"

    monkeypatch.setenv("ANIMICA_AICF_CHAT_DECODE_MODE", "prefill_only")
    monkeypatch.setenv("ANIMICA_AICF_CHAT_MODE", "auto")

    rpc = _FakeRPC()
    prov = DistributedAICFProvider(
        cfg=_Cfg(), rpc_url="http://nope.example.invalid",
        wallet_path=None, wallet_label=None,
    )
    prov.client._rpc = rpc    # type: ignore[assignment]
    prov._wallet = WalletInfo(
        address="anim1xxx", chain_id=1,
        balance_animica=10.0, balance_lookup_ok=True,
        balance_lookup_error="",
    )

    captured: dict = {}
    real_submit = prov.client.submit

    def _spy(spec, *, signed_payment):
        captured["decode_mode"] = spec.decode_mode
        return real_submit(spec, signed_payment=signed_payment)

    prov.client.submit = _spy    # type: ignore[assignment]
    import agent_runtime.providers as providers_mod
    providers_mod.get_next_nonce = lambda *a, **k: 0
    providers_mod.preview_payment = lambda wi, amount: type(
        "P", (), {"sufficient": True, "reason": None}
    )()

    class _Signed:
        def __init__(self) -> None:
            self.__dict__ = {"txn_hex": ""}
    providers_mod.sign_payment = lambda *a, **k: _Signed()
    prov.client.stream = lambda *a, **k: iter([])    # type: ignore[assignment]

    class _Settle:
        text = "x"; actual_cost_animica = 0.0; provider_id = ""; latency_ms = 0
    prov.client.settle = lambda *a, **k: _Settle()    # type: ignore[assignment]

    prov.serve(TurnRequest(prompt="hi"))
    assert captured["decode_mode"] == "prefill_only"


def test_turn_request_decode_mode_takes_priority_over_env(monkeypatch):
    """Per-turn override > env var > built-in 'streaming' default."""
    from agent_runtime.providers import DistributedAICFProvider, TurnRequest
    from agent_runtime.wallet import WalletInfo

    class _Cfg:
        integration = {"aicf": {
            "job_submit": {
                "timeout_sec": 1.0,
                "poll_interval_ms": 50,
                "max_retries": 0,
                "retry_backoff_ms": [10],
            },
            "treasury_address": "anim1placeholder",
        }}
        model_catalog = {"routing": {"default_tier": "standard"}}
        repo_root = "/tmp"

    monkeypatch.setenv("ANIMICA_AICF_CHAT_DECODE_MODE", "prefill_only")

    rpc = _FakeRPC()
    prov = DistributedAICFProvider(
        cfg=_Cfg(), rpc_url="http://nope.example.invalid",
        wallet_path=None, wallet_label=None,
    )
    prov.client._rpc = rpc    # type: ignore[assignment]
    prov._wallet = WalletInfo(
        address="anim1xxx", chain_id=1,
        balance_animica=10.0, balance_lookup_ok=True,
        balance_lookup_error="",
    )
    captured: dict = {}
    real_submit = prov.client.submit

    def _spy(spec, *, signed_payment):
        captured["decode_mode"] = spec.decode_mode
        return real_submit(spec, signed_payment=signed_payment)
    prov.client.submit = _spy    # type: ignore[assignment]
    import agent_runtime.providers as providers_mod
    providers_mod.get_next_nonce = lambda *a, **k: 0
    providers_mod.preview_payment = lambda wi, amount: type(
        "P", (), {"sufficient": True, "reason": None}
    )()

    class _Signed:
        def __init__(self) -> None:
            self.__dict__ = {"txn_hex": ""}
    providers_mod.sign_payment = lambda *a, **k: _Signed()
    prov.client.stream = lambda *a, **k: iter([])    # type: ignore[assignment]

    class _Settle:
        text = "x"; actual_cost_animica = 0.0; provider_id = ""; latency_ms = 0
    prov.client.settle = lambda *a, **k: _Settle()    # type: ignore[assignment]

    # Per-turn override wins over env.
    prov.serve(TurnRequest(prompt="hi", decode_mode="streaming"))
    assert captured["decode_mode"] == "streaming"


if __name__ == "__main__":     # pragma: no cover — manual smoke
    pytest.main([__file__, "-v"])
