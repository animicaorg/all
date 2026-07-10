"""Every registered ModelAdapter must accept seed= without TypeError (7.1.1 P2)."""

from animica.ena import providers as P
from animica.ena.models import ModelProviderConfig


def test_seed_kwarg_accepted_by_all_registered_adapters(monkeypatch):
    # This test is about the ADAPTER SIGNATURE: every registered adapter must
    # accept ``seed=`` without raising TypeError. Network/key errors (ProviderError)
    # are acceptable here — they prove the kwarg was accepted and the call reached
    # the backend. We stub the shared HTTP helper so the OpenAI/Ollama-style
    # adapters return cleanly; key-gated mesh adapters (anthropic/chutes) legitimately
    # raise ProviderError without credentials, which is fine.
    from animica.ena.errors import ProviderError

    def fake_post(url, payload, *, headers, timeout):
        if "embeddings" in url:
            return {"data": [{"embedding": [0.0]}]}
        if "/api/chat" in url:
            return {"message": {"content": "ok"}}
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(P, "_http_post", fake_post)
    P.ensure_mesh_registered()  # exercise the full mesh, not just registration order
    for provider in list(P._MODEL_ADAPTERS.keys()):
        cfg = ModelProviderConfig(name=provider, provider=provider, model="m",
                                  base_url="http://x")
        adapter = P.build_model_adapter(cfg)
        try:
            out = adapter.generate("hello", seed=1234, max_tokens=8)
            assert isinstance(out, str)
        except TypeError as exc:  # a rejected seed= kwarg is the only real failure
            raise AssertionError(f"{provider} adapter rejected seed=: {exc}") from exc
        except ProviderError:
            pass  # key/network unavailable in CI — the kwarg was still accepted


def test_deterministic_seed_changes_output_reproducibly():
    cfg = ModelProviderConfig(name="d", provider="deterministic", model="deterministic")
    a = P.build_model_adapter(cfg)
    o0 = a.generate("hello world", seed=None)
    o1 = a.generate("hello world", seed=1)
    o1b = a.generate("hello world", seed=1)
    o2 = a.generate("hello world", seed=2)
    assert o0 != o1 and o1 == o1b and o1 != o2
    assert a.supports_seed is True


def test_openai_and_ollama_forward_seed(monkeypatch):
    seen = {}

    def fake_post(url, payload, *, headers, timeout):
        seen[url] = payload
        if "/api/chat" in url:
            return {"message": {"content": "ok"}}
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(P, "_http_post", fake_post)
    oa = P.build_model_adapter(ModelProviderConfig(name="o", provider="openai_compatible",
                                                   model="gpt", base_url="http://openai.test"))
    oa.generate("hi", seed=77)
    assert any(p.get("seed") == 77 for p in seen.values())

    seen.clear()
    ol = P.build_model_adapter(ModelProviderConfig(name="l", provider="ollama", model="q",
                                                   base_url="http://ollama.test"))
    ol.generate("hi", seed=88)
    assert any(p.get("options", {}).get("seed") == 88 for p in seen.values())
