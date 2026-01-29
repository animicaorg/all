"""
capabilities.runtime
====================

Tiny convenience layer for wiring the VM stdlib syscalls to the host-side
capabilities provider.

This package exposes *lazy* helpers so importing `capabilities.runtime`
does not pull heavy dependencies until you actually need them.

Primary entrypoints
-------------------
- make_runtime_bindings(provider=None, *, strict=True, limits=None) -> Mapping[str, Any]
    Build the binding table that the VM runtime expects (names → callables).
    Delegates to `capabilities.runtime.abi_bindings.build_stdlib_bindings`.

- get_state_cache() -> "StateCache"
    Access the per-block state/result cache used to speed up read_result lookups.
    Delegates to `capabilities.runtime.state_cache.get_cache`.

- default_provider() -> "SyscallProvider"
    Return the process-global/default syscall provider instance, suitable for local runs
    and simple single-node setups.

See also:
    - capabilities.runtime.abi_bindings
    - capabilities.runtime.determinism
    - capabilities.runtime.state_cache
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Mapping, Optional

__all__ = [
    "make_runtime_bindings",
    "get_state_cache",
    "default_provider",
]


def make_runtime_bindings(
    provider: Optional[Any] = None,
    *,
    strict: bool = True,
    limits: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    """
    Lazily import and call `build_stdlib_bindings` to construct the stdlib syscall table.

    Args:
        provider: A SyscallProvider (from capabilities.host.provider). If None, uses default_provider().
        strict:   If True, enforce deterministic input/size caps (see determinism module).
        limits:   Optional per-syscall limits/overrides (passed through to abi_bindings).

    Returns:
        Mapping of stdlib names → callables, ready to be injected into the VM runtime.
    """
    if provider is None:
        provider = default_provider()
    mod = import_module("capabilities.runtime.abi_bindings")
    return mod.build_stdlib_bindings(provider=provider, strict=strict, limits=limits)  # type: ignore[attr-defined]


def get_state_cache() -> Any:
    """
    Return the process-global state cache instance used by the runtime to memoize
    deterministic reads (e.g., read_result(task_id)) within a block.
    """
    mod = import_module("capabilities.runtime.state_cache")
    return mod.get_cache()  # type: ignore[attr-defined]


def default_provider() -> Any:
    """
    Convenience accessor for the default global SyscallProvider.
    """
    host = import_module("capabilities.host.provider")
    return host.get_default_provider()  # type: ignore[attr-defined]
