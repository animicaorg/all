"""
p2p.discovery
=============

Discovery mechanisms for Animica's P2P layer:

- seeds      : DNS/HTTP/bootstrap seed loaders.
- mdns       : Optional LAN discovery via mDNS (developer convenience).
- kademlia   : Lightweight DHT for peer lookup (nodeId = peer_id).
- nat        : UPnP/NAT-PMP helpers for hole punching and port mapping.

This package only provides a lazy import shim so that importing `p2p`
doesn't pull optional dependencies until a specific discovery backend
is actually used. See specs in `p2p/specs/SEEDS.md`.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Dict

__all__ = ["seeds", "mdns", "kademlia", "nat"]

# Lazy module map (attribute → module path)
_modules: Dict[str, str] = {
    "seeds": "p2p.discovery.seeds",
    "mdns": "p2p.discovery.mdns",
    "kademlia": "p2p.discovery.kademlia",
    "nat": "p2p.discovery.nat",
}

def __getattr__(name: str) -> ModuleType:
    """
    Lazily import submodules on first access, keeping import side-effects
    (like optional aio libraries) out of cold paths.
    """
    modpath = _modules.get(name)
    if modpath is None:
        raise AttributeError(f"module 'p2p.discovery' has no attribute {name!r}")
    module = importlib.import_module(modpath)
    globals()[name] = module  # cache for subsequent lookups
    return module
