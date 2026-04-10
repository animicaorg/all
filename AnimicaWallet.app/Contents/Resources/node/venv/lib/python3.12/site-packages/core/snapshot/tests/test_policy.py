import os

from core.snapshot.policy import SnapshotPolicy


def test_policy_defaults_mainnet(monkeypatch):
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    monkeypatch.delenv("ANIMICA_SNAPSHOT_AUTO", raising=False)
    policy = SnapshotPolicy.from_env(chain_id=1)
    assert policy.auto_enabled is True


def test_policy_defaults_devnet(monkeypatch):
    monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
    monkeypatch.delenv("ANIMICA_SNAPSHOT_AUTO", raising=False)
    policy = SnapshotPolicy.from_env(chain_id=1337)
    assert policy.auto_enabled is False


def test_policy_manifest_urls(monkeypatch):
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    monkeypatch.setenv(
        "ANIMICA_SNAPSHOT_MANIFEST_URLS", "https://example.com/latest.json, https://backup/"
    )
    policy = SnapshotPolicy.from_env(chain_id=1)
    assert policy.manifest_urls == [
        "https://example.com/latest.json",
        "https://backup/",
    ]
