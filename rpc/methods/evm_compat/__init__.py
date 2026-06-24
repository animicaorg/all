"""Animica Ethereum/EVM-Compatible RPC Mode.

An Ethereum JSON-RPC **compatibility facade** — it exposes eth_*/net_*/web3_*
method names and the Ethereum wire format (hex QUANTITY/DATA, 20-byte 0x
addresses, Ethereum block/tx/receipt shapes) over Animica's native chain, so
ethers.js, web3.js, MetaMask, explorers, bots and indexers can connect to
Animica through familiar methods.

This is an RPC facade, NOT EVM execution. Animica stays its own post-quantum
PoIES useful-work L1: it does not run Solidity bytecode. Honest claim:
"Ethereum RPC-compatible facade, with a path toward full EVM execution."

Address bridge: Animica uses post-quantum anim1... accounts; this facade gives
each a deterministic 20-byte 0x alias (keccak256-derived) + a binding registry so
0x lookups resolve back (see bridge.py / animica_evmBind). An EVM alias is a
display/lookup handle, not key-compatibility — sending from an EVM wallet needs
an explicit binding + relayer (Phase 2).

Importing this package registers every handler; methods answer at POST / and /rpc.
"""

from __future__ import annotations

from . import eth_methods  # noqa: F401
from . import eth_tx  # noqa: F401
from . import net_web3  # noqa: F401

__all__ = ["eth_methods", "eth_tx", "net_web3"]
