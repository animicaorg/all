"""
Animica mining.bench
====================

Tiny registry for mining micro-benchmarks.

- Imports optional bench modules lazily (so numpy/numba or GPU libs are not hard deps).
- Exposes a small discovery API so external runners can list/execute available benches.

Conventions
-----------
Each bench module should expose one or more callables with the signature:

    def run(**kwargs) -> dict:

Returning a dict of summary metrics (e.g., {"ops_per_sec": ..., "samples": N, ...}).
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Callable, Dict, Optional

__all__ = [
    "get",
    "available",
    "require",
    "DEFAULT_BENCHES",
]

# Bench modules we try to expose by default
DEFAULT_BENCHES = (
    "mining.bench.cpu_hashrate",
    "mining.bench.template_latency",
)


def _try_import(name: str) -> Optional[ModuleType]:
    try:
        return import_module(name)
    except Exception:
        return None


def available() -> Dict[str, ModuleType]:
    """
    Return a mapping of bench short-names → imported module for all benches
    that successfully import in the current environment.
    """
    mods: Dict[str, ModuleType] = {}
    for fq in DEFAULT_BENCHES:
        mod = _try_import(fq)
        if mod is not None:
            short = fq.rsplit(".", 1)[-1]
            mods[short] = mod
    return mods


def get(name: str) -> Optional[ModuleType]:
    """
    Get a bench module by short-name ("cpu_hashrate") or fully-qualified name.
    Returns None if unavailable.
    """
    if "." not in name:
        # short-name path
        for fq in DEFAULT_BENCHES:
            if fq.endswith("." + name):
                return _try_import(fq)
        return None
    return _try_import(name)


def require(name: str) -> ModuleType:
    """
    Same as get(), but raises ImportError if the bench is not available.
    """
    mod = get(name)
    if mod is None:
        raise ImportError(
            f"Benchmark '{name}' is not available on this platform or missing optional deps"
        )
    return mod


if __name__ == "__main__":
    # Simple CLI-ish listing for ad-hoc runs:
    mods = available()
    if not mods:
        print("No mining benchmarks available (optional dependencies may be missing).")
    else:
        print("Available mining benchmarks:")
        for k in sorted(mods):
            m = mods[k]
            run = getattr(m, "run", None)
            sig = "(no run())"
            if callable(run):
                sig = "run(**kwargs)"
            print(f" - {k}: {m.__name__} {sig}")
