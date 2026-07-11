"""Tests for animica.ai.nodekey — dedicated inference signing key."""

from pathlib import Path


def test_create_reuse_persist_no_secret_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_AI_HOME", str(tmp_path))
    monkeypatch.delenv("ANIMICA_PQ_MODE", raising=False)
    from animica.ai import nodekey
    nodekey.reset_cache()

    k1 = nodekey.load_or_create_key()
    assert k1 is not None
    assert len(k1.public_key) == 1952 and len(k1.secret_key) == 4032
    assert k1.alg_name == "ml_dsa_65"
    assert Path(nodekey.key_path()).exists()

    # cached + stable across reloads (fresh cache re-reads the same file)
    nodekey.reset_cache()
    k2 = nodekey.load_or_create_key()
    assert k2.public_key == k1.public_key and k2.secret_key == k1.secret_key

    ident = nodekey.public_identity()
    assert ident["public_key_hex"] == k1.public_key.hex()
    assert "secret" not in str(ident).lower()
    assert k1.secret_key.hex() not in str(ident)
    nodekey.reset_cache()


def test_pq_disabled_fails_open(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ANIMICA_PQ_MODE", "disabled")
    from animica.ai import nodekey
    nodekey.reset_cache()
    assert nodekey.load_or_create_key() is None
    assert nodekey.public_identity() is None
    nodekey.reset_cache()
