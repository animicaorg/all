from __future__ import annotations

import sys
import types

import pytest

from rpc.methods.net import net_get_bootstrap_seeds


def test_net_get_bootstrap_seeds_includes_live_peers(monkeypatch):
    outbound_addr = "1.2.3.4:30333"
    inbound_addr = "5.6.7.8:30333"

    class DummyService:
        def __init__(self) -> None:
            self.peers = {
                outbound_addr: {
                    "remote": outbound_addr,
                    "direction": "outbound",
                },
                inbound_addr: {
                    "remote": inbound_addr,
                    "direction": "inbound",
                },
            }

    dummy = DummyService()
    monkeypatch.setitem(
        sys.modules,
        "p2p",
        types.SimpleNamespace(get_service=lambda: dummy),
    )

    result = net_get_bootstrap_seeds()

    seeds = result["seeds"]
    assert f"tcp://{outbound_addr}" in seeds
    assert f"tcp://{inbound_addr}" in seeds

    discovered = result.get("discovered") or {}
    assert f"tcp://{outbound_addr}" in discovered.get("outbound", [])
    assert f"tcp://{inbound_addr}" in discovered.get("inbound", [])


def test_net_get_bootstrap_seeds_handles_missing_p2p(monkeypatch):
    # Drop any existing p2p module so the helper falls back gracefully
    monkeypatch.setitem(sys.modules, "p2p", None)

    result = net_get_bootstrap_seeds()

    # Should return a seeds list even when P2P is unavailable
    assert "seeds" in result
    assert isinstance(result["seeds"], list)
