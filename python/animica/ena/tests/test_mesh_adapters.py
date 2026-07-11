"""Mesh backends: Anthropic/Claude, Chutes, ena_served (7.1.1 P3)."""

from animica.ena import providers as P
from animica.ena.models import ModelProviderConfig


def _cfg(provider, model="m", **kw):
    return ModelProviderConfig(name=provider, provider=provider, model=model, **kw)


def test_mesh_registered():
    P.ensure_mesh_registered()
    for k in ("anthropic", "claude", "chutes", "bittensor", "ena_served"):
        assert k in P._MODEL_ADAPTERS


def test_anthropic_payload_is_400_safe(monkeypatch):
    from animica.ena import adapters_anthropic as AA
    seen = {}

    def fake_post(url, payload, *, headers, timeout):
        seen.update(url=url, payload=payload, headers=headers)
        return {"content": [{"type": "text", "text": "hi from claude"}]}

    monkeypatch.setattr(AA, "_http_post", fake_post)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    adapter = P.build_model_adapter(_cfg("anthropic", "claude-opus-4-8"))
    out = adapter.generate("hello", system="be brief", temperature=0.9, max_tokens=100)
    assert out == "hi from claude"
    assert seen["url"].endswith("/v1/messages")
    assert seen["headers"]["anthropic-version"] == AA.ANTHROPIC_VERSION
    assert seen["headers"]["x-api-key"] == "sk-test"
    # opus-4-8 rejects sampling params → they must be omitted
    assert "temperature" not in seen["payload"] and "top_p" not in seen["payload"]
    assert seen["payload"]["max_tokens"] == 100 and seen["payload"]["system"] == "be brief"
    assert adapter.supports_seed is False


def test_anthropic_haiku_allows_temperature(monkeypatch):
    from animica.ena import adapters_anthropic as AA
    seen = {}
    monkeypatch.setattr(AA, "_http_post",
                        lambda url, payload, **k: seen.update(payload=payload) or {"content": [{"type": "text", "text": "ok"}]})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    P.build_model_adapter(_cfg("anthropic", "claude-haiku-4-5-20251001")).generate("x", temperature=0.5)
    assert seen["payload"].get("temperature") == 0.5


def test_anthropic_needs_key(monkeypatch):
    from animica.ena.errors import ProviderError
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import pytest
    with pytest.raises(ProviderError):
        P.build_model_adapter(_cfg("anthropic", "claude-opus-4-8")).generate("x")


def test_chutes_defaults(monkeypatch):
    from animica.ena import providers as PP
    seen = {}
    monkeypatch.setattr(PP, "_http_post",
                        lambda url, payload, **k: seen.update(url=url) or {"choices": [{"message": {"content": "c"}}]})
    monkeypatch.setenv("CHUTES_API_TOKEN", "ct")
    out = P.build_model_adapter(_cfg("chutes", "some-model")).generate("hi")
    assert out == "c" and "chutes" in seen["url"]


def test_ena_served_targets_serve_url(monkeypatch):
    from animica.ena import providers as PP
    seen = {}
    monkeypatch.setattr(PP, "_http_post",
                        lambda url, payload, **k: seen.update(url=url, payload=payload) or {"choices": [{"message": {"content": "served"}}]})
    monkeypatch.setenv("ANIMICA_ENA_SERVE_URL", "http://ena.test/v1")
    adapter = P.build_model_adapter(_cfg("ena_served", "ena://pool7"))
    out = adapter.generate("hi")
    assert out == "served" and seen["url"].startswith("http://ena.test/v1")
    assert seen["payload"]["model"] == "pool7"  # ena://pool7 → pool7
    assert adapter.supports_seed is True
