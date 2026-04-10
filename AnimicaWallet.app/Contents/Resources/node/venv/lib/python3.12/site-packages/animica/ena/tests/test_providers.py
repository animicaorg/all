from __future__ import annotations

import json
from pathlib import Path

import httpx

from animica.ena.config import load_ena_config
from animica.ena.models import EmbeddingProviderConfig, ModelProviderConfig
from animica.ena.providers import create_embedding_provider, create_model_provider
from animica.ena.retrieval import IndexManager
from animica.ena.store import EnaStore


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://mock.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("mock failure", request=request, response=response)

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def request(self, method: str, url: str, json=None):
        if url.endswith("/models"):
            return _FakeResponse({"data": [{"id": "mock-chat-model"}]})
        if url.endswith("/chat/completions"):
            labels = []
            if isinstance(json, dict):
                try:
                    payload = json["messages"][-1]["content"]
                    decoded = payload if isinstance(payload, dict) else __import__("json").loads(payload)
                    labels = list(decoded.get("labels", []))
                except Exception:
                    labels = []
            label = "sync" if "sync" in labels else (labels[0] if labels else "general")
            return _FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"label":"%s","reason":"mock classification"}' % label,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"total_tokens": 8},
                }
            )
        if url.endswith("/embeddings"):
            items = []
            for index, text in enumerate(json["input"]):
                if "chain" in text or "stable" in text or "finality" in text:
                    embedding = [1.0, 0.0]
                else:
                    embedding = [0.0, 1.0]
                items.append({"index": index, "embedding": embedding})
            return _FakeResponse({"data": items})
        raise AssertionError(f"unexpected URL {url}")


def test_model_provider_selection_and_structured_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    monkeypatch.setenv("TEST_OPENAI_KEY", "secret")
    monkeypatch.setattr("animica.ena.providers.httpx.Client", _FakeClient)

    config = load_ena_config()
    config.model_providers["mock_remote"] = ModelProviderConfig(
        provider="openai_compatible",
        transport="remote_api",
        model="mock-chat-model",
        base_url="https://mock.test",
        api_key_env_vars=["TEST_OPENAI_KEY"],
    )
    config.default_model_provider = "mock_remote"

    remote = create_model_provider(config)
    deterministic = create_model_provider(config, provider_name="deterministic")

    assert remote.list_models()[0]["id"] == "mock-chat-model"
    assert deterministic.config.provider == "deterministic"

    parsed = remote.classify("Sync downloads headers first.", ["sync", "finality"])
    assert parsed["label"] == "sync"
    assert "classification" in parsed["reason"]


def test_embedding_provider_and_semantic_ranking(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    monkeypatch.setenv("TEST_EMBED_KEY", "secret")
    monkeypatch.setattr("animica.ena.providers.httpx.Client", _FakeClient)

    config = load_ena_config()
    config.embedding_providers["mock_embed"] = EmbeddingProviderConfig(
        provider="openai_compatible",
        transport="remote_api",
        model="mock-embed-model",
        base_url="https://mock.test",
        api_key_env_vars=["TEST_EMBED_KEY"],
    )
    config.default_embedding_provider = "mock_embed"

    store = EnaStore(config)
    indexer = IndexManager(store, config)

    docs = tmp_path / "docs.jsonl"
    rows = [
        {"title": "Cats", "content_text": "Cats are mammals and purr."},
        {"title": "Finality", "content_text": "Consensus finality confirms a stable chain head."},
    ]
    docs.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = indexer.index_jsonl_records(docs, index_name="docs", reset=True, embedding_provider_name="mock_embed")
    assert result["embedding_provider"] == "mock_embed"

    semantic_hits = indexer.search("stable chain head", index_name="docs", strategy="semantic", embedding_provider_name="mock_embed")
    assert semantic_hits
    assert semantic_hits[0].title == "Finality"
    assert semantic_hits[0].semantic_score > 0

    hybrid_hits = indexer.search("stable chain head", index_name="docs", strategy="hybrid", embedding_provider_name="mock_embed")
    assert hybrid_hits[0].title == "Finality"
