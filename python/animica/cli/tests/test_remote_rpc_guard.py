from __future__ import annotations

import os

import pytest
import typer

from animica.cli.rpc_guard import guard_bootstrap_rpc


def test_guard_blocks_bootstrap_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    with pytest.raises(typer.Exit):
        guard_bootstrap_rpc("http://127.0.0.1:8545/rpc")


def test_guard_allows_with_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    monkeypatch.setenv("ANIMICA_I_UNDERSTAND_REMOTE_RISK", "1")
    guard_bootstrap_rpc("http://127.0.0.1:8545/rpc", allow_remote=True)


def test_guard_skips_bootstrap_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    # Should not raise because method is explicitly bootstrap.*
    guard_bootstrap_rpc(
        "http://127.0.0.1:8545/rpc",
        allow_remote=False,
        allow_bootstrap_methods=True,
        method="bootstrap.getManifest",
    )
