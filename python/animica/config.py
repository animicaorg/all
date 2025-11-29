"""Shared configuration helpers for Animica tools.

This module centralizes lightweight network profile handling so
user-facing tools can respect the same environment variables
without hard-coding devnet defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_NETWORK = "devnet"
DEFAULT_RPC_URL = "http://127.0.0.1:8545/rpc"


@dataclass(frozen=True)
class NetworkConfig:
    name: str
    rpc_url: str

    @property
    def rpc_host(self) -> str:
        parsed = urlparse(self.rpc_url)
        return parsed.hostname or "127.0.0.1"

    @property
    def rpc_port(self) -> int:
        parsed = urlparse(self.rpc_url)
        return parsed.port or 8545


def load_network_config() -> NetworkConfig:
    name = os.getenv("ANIMICA_NETWORK", DEFAULT_NETWORK)
    rpc_url = os.getenv("ANIMICA_RPC_URL", DEFAULT_RPC_URL)
    return NetworkConfig(name=name, rpc_url=rpc_url)


__all__ = ["NetworkConfig", "load_network_config", "DEFAULT_NETWORK", "DEFAULT_RPC_URL"]
