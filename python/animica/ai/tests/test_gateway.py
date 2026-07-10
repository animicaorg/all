"""Tests for the OpenAI-compatible AI gateway (5.2.0).

Runs fully offline by forcing the `deterministic` chat provider and `hashing`
embedding provider — both are network-free, so these assert the HTTP contract
(OpenAI shapes, streaming, auth, error envelope) without any real model/GPU.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from animica.ai.gateway import build_app  # noqa: E402


def _client(**kw):
    # deterministic provider => offline, stable output.
    kw.setdefault("provider", "deterministic")
    return TestClient(build_app(**kw))


def test_health():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_models_list_shape():
    r = _client().get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert isinstance(body["data"], list)
    for m in body["data"]:
        assert {"id", "object", "owned_by"} <= set(m)


def test_chat_completion_non_streaming():
    r = _client().post("/v1/chat/completions", json={
        "model": "deterministic",
        "messages": [{"role": "system", "content": "be terse"},
                     {"role": "user", "content": "hello world"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"]  # deterministic echoes content
    assert body["choices"][0]["finish_reason"] == "stop"
    assert set(body["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}


def test_chat_completion_streaming_sse():
    with _client() as c:
        with c.stream("POST", "/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "stream please"}],
            "stream": True,
        }) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            lines = [ln for ln in r.iter_lines() if ln]
    # SSE frames are `data: {...}` and a terminal `data: [DONE]`.
    assert any("[DONE]" in ln for ln in lines)
    chunks = [json.loads(ln[6:]) for ln in lines if ln.startswith("data: ") and "[DONE]" not in ln]
    assert chunks and all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_completions_legacy():
    r = _client().post("/v1/completions", json={"prompt": "complete this"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "text_completion"
    assert body["choices"][0]["text"]


def test_embeddings_offline_hashing():
    # hashing embedding provider is offline and deterministic.
    r = _client(provider=None).post("/v1/embeddings",
                                    json={"model": "legacy-hash", "input": ["a", "b"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 2
    assert isinstance(body["data"][0]["embedding"], list) and body["data"][0]["embedding"]


def test_error_envelope_on_bad_request():
    r = _client().post("/v1/chat/completions", json={"messages": []})
    assert r.status_code == 400
    assert "error" in r.json()
    assert set(r.json()["error"]) >= {"message", "type", "code"}


def test_auth_required_when_key_set():
    c = _client(api_key="secret")
    # no header -> 401 OpenAI shape
    r = c.get("/v1/models")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"
    # wrong key -> 401
    assert c.get("/v1/models", headers={"authorization": "Bearer nope"}).status_code == 401
    # correct key -> 200
    assert c.get("/v1/models", headers={"authorization": "Bearer secret"}).status_code == 200


def test_json_mode_injects_instruction():
    # JSON mode must not error and must still return a completion.
    r = _client().post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "give me data"}],
        "response_format": {"type": "json_object"},
    })
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Native tool calling (§17). The upstream HTTP is mocked — no ollama/network.
# --------------------------------------------------------------------------- #
def test_native_tools_ollama_mapping(monkeypatch):
    from animica.ai import gateway
    from animica.ena.models import ModelProviderConfig

    # Fake an ollama /api/chat response carrying an object-args tool_call.
    monkeypatch.setattr(gateway, "_http_post", lambda *a, **k: {
        "message": {"content": "", "tool_calls": [
            {"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}]}})
    cfg = ModelProviderConfig(name="ollama", provider="ollama", model="qwen2.5:7b",
                              base_url="http://x")
    choices = gateway.native_tools_choices(cfg, [{"role": "user", "content": "weather?"}],
                                           tools=[{"type": "function"}], tool_choice=None,
                                           max_tokens=None, temperature=None)
    tc = choices[0]["message"]["tool_calls"][0]
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_weather"
    # OpenAI requires arguments as a JSON *string*.
    assert tc["function"]["arguments"] == '{"city": "Paris"}'
    assert choices[0]["finish_reason"] == "tool_calls"


def test_rate_limit_returns_429():
    # 2 requests/min: 3rd from the same client must 429 with the OpenAI shape.
    c = _client(rate_limit_per_min=2)
    assert c.get("/v1/models").status_code == 200
    assert c.get("/v1/models").status_code == 200
    r = c.get("/v1/models")
    assert r.status_code == 429
    assert r.json()["error"]["type"] == "rate_limit_error"


def test_native_tools_returns_none_for_deterministic():
    from animica.ai import gateway
    from animica.ena.models import ModelProviderConfig

    cfg = ModelProviderConfig(name="d", provider="deterministic", model="deterministic")
    assert gateway.native_tools_choices(cfg, [], [{"type": "function"}], None, None, None) is None


def test_gateway_returns_tool_calls(monkeypatch):
    from animica.ai import gateway

    # Force the native path to yield a tool call regardless of provider.
    monkeypatch.setattr(gateway, "native_tools_choices", lambda *a, **k: [
        {"index": 0, "finish_reason": "tool_calls",
         "message": {"role": "assistant", "content": None,
                     "tool_calls": [{"id": "call_0", "type": "function",
                                     "function": {"name": "f", "arguments": "{}"}}]}}])
    r = _client().post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "use a tool"}],
        "tools": [{"type": "function", "function": {"name": "f"}}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    assert body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "f"


def test_gateway_tools_fallback_when_no_native(monkeypatch):
    from animica.ai import gateway

    # Native unsupported (None) → falls back to a normal content completion.
    monkeypatch.setattr(gateway, "native_tools_choices", lambda *a, **k: None)
    r = _client().post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "f"}}],
    })
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"]  # deterministic content
