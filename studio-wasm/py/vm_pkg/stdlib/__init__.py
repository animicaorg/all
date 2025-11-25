"""
stdlib — contract-facing convenience surface for the browser simulator.

Contracts are expected to import from this package:

    from stdlib import storage, events, hash, abi, treasury

Modules exposed:
- storage  : simple key/value storage helpers (bytes <-> bytes/int)
- events   : deterministic event emit helpers
- hash     : keccak256 / sha3_256 / sha3_512 wrappers
- abi      : small helpers like `require` and `revert`
- treasury : inert stubs for local simulations (balance/transfer)

This is a thin re-export layer to keep imports stable and readable inside
example contracts that run under the in-browser VM.
"""

from . import storage as storage
from . import events as events
from . import hash as hash
from . import abi as abi
from . import treasury as treasury

__all__ = ["storage", "events", "hash", "abi", "treasury"]
