"""
Animica VM (Python) — runtime package

This package contains the deterministic interpreter and the host-facing APIs
(storage/events/hash/treasury/syscalls/random) that contracts can use via the
VM's injected `stdlib` surface.

Convenience re-exports live here so callers can do:

    from vm_py.runtime import Engine, GasMeter, BlockEnv, TxEnv
    from vm_py.runtime import abi, storage, events, hashing  # module namespaces

Notes
-----
- All code that can affect determinism is behind explicit APIs.
- No wall-clock I/O or system randomness is exposed here.
- For contract code, import **only** from the injected `stdlib` inside the VM.
"""

from __future__ import annotations

# Public version string (mirrors vm_py.__init__)
from ..version import __version__  # re-export
# Expose API namespaces as modules for ergonomic access
from . import abi as abi  # encode/decode & dispatch helpers
from . import events_api as events
from . import hash_api as hashing  # avoid shadowing builtin `hash`
from . import loader as loader
from . import random_api as random
from . import sandbox as sandbox
from . import state_adapter as state_adapter
from . import storage_api as storage
from . import syscalls_api as syscalls
from . import treasury_api as treasury
from .context import BlockEnv, TxEnv  # type: ignore
# Re-export core runtime classes
from .engine import Engine  # type: ignore
from .gasmeter import GasMeter  # type: ignore

__all__ = [
    "__version__",
    # Core classes
    "Engine",
    "GasMeter",
    "BlockEnv",
    "TxEnv",
    # Namespaces (modules)
    "abi",
    "storage",
    "events",
    "hashing",
    "treasury",
    "syscalls",
    "random",
    "loader",
    "sandbox",
    "state_adapter",
]
