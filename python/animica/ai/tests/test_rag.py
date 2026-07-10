"""Unit tests for animica.ai.rag — chunking, cosine, and the on-disk store."""

from __future__ import annotations

from animica.ai.rag import RagStore, chunk_text, cosine


def test_chunk_text_empty_and_small():
    assert chunk_text("") == []
    assert chunk_text("short") == ["short"]


def test_chunk_text_splits_paragraphs():
    text = "\n\n".join([f"paragraph number {i} " * 20 for i in range(6)])
    chunks = chunk_text(text, size=300)
    assert len(chunks) > 1
    assert all(len(c) <= 600 for c in chunks)  # bounded (hard-wrap fallback)


def test_chunk_text_hardwraps_oversized():
    big = "x" * 2000
    chunks = chunk_text(big, size=500, overlap=50)
    assert len(chunks) >= 4
    assert all(len(c) <= 500 for c in chunks)


def test_cosine():
    assert cosine([1, 0, 0], [1, 0, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([1, 2, 3], []) == 0.0  # length mismatch -> 0
    assert cosine([0, 0], [0, 0]) == 0.0  # zero vector -> 0


def test_store_roundtrip_and_search(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_HOME", str(tmp_path))
    s = RagStore("unit")
    s.model = "legacy-hash"
    s.add("alpha doc", "a.txt", [1.0, 0.0, 0.0])
    s.add("beta doc", "b.txt", [0.0, 1.0, 0.0])
    s.save()

    loaded = RagStore("unit").load()
    assert loaded.model == "legacy-hash"
    assert len(loaded.items) == 2
    hits = loaded.search([0.9, 0.1, 0.0], k=1)
    assert hits[0]["text"] == "alpha doc"
    assert hits[0]["score"] > 0.9
    assert "unit" in RagStore.list_stores()
