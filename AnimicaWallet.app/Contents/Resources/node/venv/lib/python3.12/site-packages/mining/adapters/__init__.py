"""
Animica mining.adapters

Thin glue between the miner and the rest of the node stack. These adapters
provide strongly-typed, minimal surfaces to:
  • read chain head / mempool snapshot / persist submissions      → CoreChainAdapter
  • read current Θ/Γ/caps/escort-q and score previews             → ConsensusViewAdapter
  • locally verify proofs & convert to ψ-input metrics            → ProofsViewAdapter
  • enqueue AI/Quantum jobs and poll for completion (AICF)        → AICFQueueAdapter

Each adapter is intentionally small, easy to mock in tests, and import-safe:
if an optional dependency is missing, the import is skipped but the package
remains importable (so tools like `omni miner getwork` still function).

Use `available_adapters()` to introspect what is present in the current env.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Dict, List, Tuple

__all__: List[str] = []

# Internal: (public-name, module-path, symbol)
_TO_IMPORT: Tuple[Tuple[str, str, str], ...] = (
    ("CoreChainAdapter", "mining.adapters.core_chain", "CoreChainAdapter"),
    ("ConsensusViewAdapter", "mining.adapters.consensus_view", "ConsensusViewAdapter"),
    ("ProofsViewAdapter", "mining.adapters.proofs_view", "ProofsViewAdapter"),
    ("AICFQueueAdapter", "mining.adapters.aicf_queue", "AICFQueueAdapter"),
)


def _safe_bind(public_name: str, module_path: str, symbol: str) -> bool:
    """
    Try to import `symbol` from `module_path` and bind it into this module's globals
    under `public_name`. Returns True on success, False if the module is unavailable.
    """
    try:
        mod = import_module(module_path)
        obj = getattr(mod, symbol)
        globals()[public_name] = obj  # type: ignore[assignment]
        __all__.append(public_name)
        return True
    except Exception:  # noqa: BLE001 - keep adapters import-resilient
        return False


# Attempt to bind all adapters; missing deps won't crash the import of this package.
for public_name, module_path, symbol in _TO_IMPORT:
    _safe_bind(public_name, module_path, symbol)


def available_adapters() -> Dict[str, bool]:
    """
    Report which adapters were successfully imported.
    Returns a mapping: { "CoreChainAdapter": True/False, ... }.
    """
    out: Dict[str, bool] = {}
    for public_name, module_path, symbol in _TO_IMPORT:
        out[public_name] = public_name in globals()
    return out


def require(adapter_name: str) -> Any:
    """
    Fetch a bound adapter class by public name, raising an informative error if
    it is not available in this environment.

    Example:
        CoreChain = require("CoreChainAdapter")
        core = CoreChain(db=..., rpc=...)
    """
    if adapter_name in globals():
        return globals()[adapter_name]
    have = available_adapters()
    missing = [n for n, ok in have.items() if not ok]
    raise ImportError(
        f"Adapter '{adapter_name}' is not available. Missing adapters: {missing}. "
        f"Ensure optional dependencies for '{adapter_name}' are installed."
    )
