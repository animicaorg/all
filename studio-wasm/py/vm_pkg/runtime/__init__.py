"""
vm_pkg.runtime — deterministic Python VM runtime (browser subset)

This package exposes the browser-safe runtime pieces used by @animica/studio-wasm:
a tiny interpreter, gas metering, minimal execution context, and stdlib-facing
APIs for storage/events/hashing/randomness and ABI dispatch. It intentionally
omits host-bound integrations (syscalls, OS I/O, networking).

Modules exported here:
- engine       : deterministic interpreter core
- gasmeter     : gas accounting (debit/refund)
- context      : BlockEnv/TxEnv scaffolding suitable for local simulation
- storage_api  : in-worker key/value storage (ephemeral)
- events_api   : deterministic event/log collector
- hash_api     : keccak/sha3 helpers with pure-bytes I/O
- abi          : call dispatch, arg/return (de)serialization
- random_api   : deterministic PRNG seeded from call/tx

Note: These files are auto-synced from the upstream `vm_py/` tree by
`studio-wasm/scripts/sync_vm_py.py` and may have imports rewritten to
`vm_pkg.*`. Do not edit them by hand here.
"""

from __future__ import annotations

# Re-export submodules for ergonomic imports: `from vm_pkg.runtime import engine`
from . import engine as engine
from . import gasmeter as gasmeter
from . import context as context
from . import storage_api as storage_api
from . import events_api as events_api
from . import hash_api as hash_api
from . import abi as abi
from . import random_api as random_api

__all__ = [
    "engine",
    "gasmeter",
    "context",
    "storage_api",
    "events_api",
    "hash_api",
    "abi",
    "random_api",
]
