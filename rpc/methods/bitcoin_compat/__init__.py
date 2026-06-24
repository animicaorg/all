"""Animica Bitcoin-Core-Compatible RPC Mode.

A compatibility layer that exposes Bitcoin Core 30.x JSON-RPC method names and
JSON shapes over Animica's native chain — NOT a claim that Animica behaves
identically to Bitcoin. Hashes, txids, addresses, mempool policy, fees, wallet
model and mining (PoIES useful-work) are Animica's; only the RPC surface mimics
Bitcoin so existing explorers, wallets, exchanges, bots, indexers and pool
dashboards can "point Bitcoin RPC tooling at a new endpoint".

Importing this package registers every handler (each module uses @method, which
mirrors into the live JSON-RPC dispatcher). Methods answer at both POST / (the
Bitcoin convention) and POST /rpc.

Tiers (see the module docstrings):
  - chain_methods   : Tier 1 read-only core (highest viability)
  - wallet_methods  : Tier 2 wallet adapter (account-model, degraded where noted)
  - mining_methods  : Tier 3 mining (PoIES adapter)
  - control_methods : stop, help
"""

from __future__ import annotations

from . import chain_methods  # noqa: F401
from . import control_methods  # noqa: F401
from . import mining_methods  # noqa: F401
from . import wallet_methods  # noqa: F401

__all__ = ["chain_methods", "wallet_methods", "mining_methods", "control_methods"]
