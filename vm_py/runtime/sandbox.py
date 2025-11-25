"""
vm_py.runtime.sandbox — import guard; injects a synthetic stdlib package at runtime.

Goal
----
Contracts run in a deterministic VM and are only allowed to import a small,
blessed surface named `stdlib` (and its submodules). This module provides:

1) A *synthetic stdlib package* injected into `sys.modules`:
   - `stdlib.storage`  → vm_py.runtime.storage_api
   - `stdlib.events`   → vm_py.runtime.events_api
   - `stdlib.hash`     → vm_py.runtime.hash_api
   - `stdlib.abi`      → vm_py.runtime.abi
   - `stdlib.treasury` → vm_py.runtime.treasury_api
   - `stdlib.syscalls` → vm_py.runtime.syscalls_api
   - `stdlib.random`   → vm_py.runtime.random_api

   These are thin, lazy proxies so we don't import heavy modules unless used.

2) A *contract import guard* that blocks all imports except those under
   the allowed prefix(es), typically "stdlib". Implemented as a context
   manager that installs a highest-priority meta_path finder which raises
   ImportError for any disallowed module.

This file intentionally avoids policy decisions beyond import control.
Validation of AST/builtins happens in vm_py.validate; gas/determinism happen
in the runtime engine. Together, they form a locked-down execution model.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple


# ----------------------------- errors ------------------------------ #

class SandboxError(Exception):
    """Base class for sandbox-related failures."""


# ------------------------- stdlib injection ------------------------ #

# Map "stdlib.<name>" → fully qualified target module in this runtime.
_STDLIB_ROUTE: Mapping[str, str] = {
    "storage": "vm_py.runtime.storage_api",
    "events": "vm_py.runtime.events_api",
    "hash": "vm_py.runtime.hash_api",
    "abi": "vm_py.runtime.abi",
    "treasury": "vm_py.runtime.treasury_api",
    "syscalls": "vm_py.runtime.syscalls_api",
    "random": "vm_py.runtime.random_api",
}


def _lazy_proxy_module(public_name: str, target_qualname: str) -> types.ModuleType:
    """
    Create a lightweight module that lazily imports `target_qualname` on first use.
    """
    mod = types.ModuleType(public_name)
    mod.__package__ = public_name.rpartition(".")[0]
    mod.__doc__ = f"Lazy proxy for {target_qualname}"

    _loaded_target: Dict[str, types.ModuleType] = {}

    def _ensure_target() -> types.ModuleType:
        tgt = _loaded_target.get("t")
        if tgt is None:
            tgt = __import__(target_qualname, fromlist=["*"])
            _loaded_target["t"] = tgt
        return tgt

    def __getattr__(name: str):
        return getattr(_ensure_target(), name)

    def __dir__():
        tgt = _ensure_target()
        attrs = set(dir(tgt))
        # Keep module-ish names visible
        attrs |= {"__doc__", "__name__", "__package__", "__loader__", "__spec__"}
        return sorted(attrs)

    mod.__getattr__ = __getattr__  # type: ignore[attr-defined]
    mod.__dir__ = __dir__  # type: ignore[attr-defined]
    return mod


def _ensure_stdlib_root() -> types.ModuleType:
    """
    Ensure a root 'stdlib' package exists in sys.modules and return it.
    The root bears only metadata and acts as a container for submodules.
    """
    if "stdlib" in sys.modules:
        root = sys.modules["stdlib"]
        # If someone polluted it with non-sandbox loaders, we still keep our layout.
        if not isinstance(root, types.ModuleType):
            raise SandboxError("existing 'stdlib' is not a module")
        return root

    root = types.ModuleType("stdlib")
    root.__path__ = []  # mark as package
    root.__package__ = "stdlib"
    root.__doc__ = "Synthetic, deterministic contract stdlib (injected by vm_py.runtime.sandbox)"
    sys.modules["stdlib"] = root
    return root


def install_stdlib_proxy(overwrite: bool = False) -> None:
    """
    Inject or refresh the synthetic 'stdlib' package and its submodules.

    Idempotent: safe to call before each contract execution.
    """
    root = _ensure_stdlib_root()

    for name, target in _STDLIB_ROUTE.items():
        fq = f"stdlib.{name}"
        if fq in sys.modules and not overwrite:
            # Leave an existing compatible proxy/module intact.
            continue
        sys.modules[fq] = _lazy_proxy_module(fq, target)
        # Also expose as attribute on root so "from stdlib import storage" works.
        setattr(root, name, sys.modules[fq])

    # Export list
    root.__all__ = sorted(list(_STDLIB_ROUTE.keys()))  # type: ignore[attr-defined]


def uninstall_stdlib_proxy() -> None:
    """Remove synthetic stdlib modules from sys.modules (best-effort)."""
    sys.modules.pop("stdlib", None)
    for name in list(_STDLIB_ROUTE.keys()):
        sys.modules.pop(f"stdlib.{name}", None)


# ------------------------- import guard hook ----------------------- #

@dataclass(frozen=True)
class _GuardConfig:
    allowed_prefixes: Tuple[str, ...] = ("stdlib",)


class _DenyingLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return None  # pragma: no cover

    def exec_module(self, module):  # pragma: no cover
        raise ImportError("imports are disabled by Animica VM sandbox")


class _ContractImportGuard(importlib.abc.MetaPathFinder):
    """
    A first-position meta_path finder that *denies* importing any module whose
    fully-qualified name does not start with an allowed prefix.

    Returning a spec with _DenyingLoader ensures an ImportError is raised even
    if later finders could have loaded the module.
    """

    def __init__(self, cfg: _GuardConfig) -> None:
        self.cfg = cfg
        # Normalize allowed prefixes for quick checks
        self._allowed = tuple(p if p else "" for p in cfg.allowed_prefixes)

    def find_spec(self, fullname: str, path, target=None):
        # Always allow the bootstrap machinery itself to function.
        if fullname in ("importlib", "importlib.abc", "importlib.machinery", "types"):
            return None
        # Allow importing our synthetic stdlib (and submodules) if configured.
        for prefix in self._allowed:
            if fullname == prefix or fullname.startswith(prefix + "."):
                return None  # Defer to normal resolution (or our injected modules)

        # Deny everything else by returning a blocking spec
        return importlib.machinery.ModuleSpec(fullname, _DenyingLoader())


@contextmanager
def contract_import_guard(allowed_prefixes: Sequence[str] = ("stdlib",)) -> Iterator[None]:
    """
    Context manager that:
      - Installs/refreshes the synthetic stdlib proxies
      - Installs an import guard that denies non-stdlib imports
      - Removes the guard upon exit (stdlib proxies remain installed)

    Example
    -------
    with contract_import_guard():
        # user contract code that may execute "from stdlib import storage"
        run_contract(...)
    """
    install_stdlib_proxy(overwrite=False)
    guard = _ContractImportGuard(_GuardConfig(tuple(allowed_prefixes)))
    sys.meta_path.insert(0, guard)
    try:
        yield
    finally:
        # Remove our guard instance only (preserve any other hooks)
        try:
            sys.meta_path.remove(guard)
        except ValueError:
            pass


__all__ = [
    "SandboxError",
    "install_stdlib_proxy",
    "uninstall_stdlib_proxy",
    "contract_import_guard",
]
