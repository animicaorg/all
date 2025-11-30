"""
vm_py.stdlib
============

Contract-facing standard library surface.

Contracts can do:

    from stdlib import storage, events, hash, abi, treasury, syscalls

This module tries to import rich stdlib submodules (./storage.py, ./events.py,
./hash.py, ./abi.py, ./treasury.py, ./syscalls.py). If any are not present yet,
it falls back to thin shims backed by the runtime APIs
(vm_py.runtime.*_api). That lets local simulations work even before the full
stdlib files are added.

Exports
-------
- storage  : get(key)->bytes, set(key, value)->None (address is implicit in higher layers)
- events   : emit(name: bytes, args: dict)->None
- hash     : keccak256(b), sha3_256(b), sha3_512(b)
- abi      : helpers like revert(...), require(...), (encode/decode live in the full module)
- treasury : balance(address: bytes)->int, transfer(from_addr: bytes, to_addr: bytes, amount: int)->None
- syscalls : blob_pin(ns: int, data: bytes)->commitment, ai_enqueue(...), quantum_enqueue(...), read_result(...), zk_verify(...)

Notes
-----
- The *fallback* APIs are intentionally minimal. When the concrete stdlib/*.py
  files are added, their richer functionality will be used automatically.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

__all__ = ("storage", "events", "hash", "abi", "treasury", "syscalls")

# -- storage -----------------------------------------------------------------
try:
    from . import storage as storage  # type: ignore
except Exception:
    # Fallback shim -> runtime.storage_api
    try:
        from vm_py.runtime import storage_api as _storage_api  # type: ignore

        storage = SimpleNamespace(  # type: ignore[assignment]
            get=lambda key: (
                _storage_api.get(  # address-binding handled by higher layer
                    getattr(_storage_api, "ADDRESS", b""), key
                )
                if hasattr(_storage_api, "ADDRESS")
                else _storage_api.get(b"", key)
            ),
            set=lambda key, value: (
                _storage_api.set(getattr(_storage_api, "ADDRESS", b""), key, value)
                if hasattr(_storage_api, "ADDRESS")
                else _storage_api.set(b"", key, value)
            ),
        )
    except Exception:  # pragma: no cover
        storage = SimpleNamespace(  # type: ignore[assignment]
            get=lambda key: b"",
            set=lambda key, value: None,
        )

# -- events ------------------------------------------------------------------
try:
    from . import events as events  # type: ignore
except Exception:
    try:
        from vm_py.runtime import events_api as _events_api  # type: ignore

        events = SimpleNamespace(emit=lambda name, args: _events_api.emit(name, args))  # type: ignore[assignment]
    except Exception:  # pragma: no cover
        events = SimpleNamespace(emit=lambda name, args: None)  # type: ignore[assignment]

# -- hash --------------------------------------------------------------------
try:
    from . import hash as hash  # type: ignore
except Exception:
    try:
        from vm_py.runtime import hash_api as _hash_api  # type: ignore

        hash = SimpleNamespace(  # type: ignore[assignment]
            keccak256=_hash_api.keccak256,
            sha3_256=_hash_api.sha3_256,
            sha3_512=_hash_api.sha3_512,
        )
    except Exception:  # pragma: no cover
        import hashlib

        def _sha3_256(b: bytes) -> bytes:
            return hashlib.sha3_256(b).digest()

        def _sha3_512(b: bytes) -> bytes:
            return hashlib.sha3_512(b).digest()

        # keccak is not in hashlib; provide sha3 as placeholder
        hash = SimpleNamespace(  # type: ignore[assignment]
            keccak256=_sha3_256,
            sha3_256=_sha3_256,
            sha3_512=_sha3_512,
        )

# -- abi ---------------------------------------------------------------------
try:
    from . import abi as abi  # type: ignore
except Exception:
    try:
        from vm_py.runtime import abi as _abi_rt  # type: ignore

        abi = SimpleNamespace(  # type: ignore[assignment]
            revert=_abi_rt.revert if hasattr(_abi_rt, "revert") else (lambda msg=b"": (_ for _ in ()).throw(RuntimeError(msg))),  # type: ignore
            require=_abi_rt.require if hasattr(_abi_rt, "require") else (lambda cond, msg=b"": (_ for _ in ()).throw(RuntimeError(msg)) if not cond else None),  # type: ignore
        )
    except Exception:  # pragma: no cover
        abi = SimpleNamespace(  # type: ignore[assignment]
            revert=lambda msg=b"": (_ for _ in ()).throw(RuntimeError(msg)),
            require=lambda cond, msg=b"": (
                (_ for _ in ()).throw(RuntimeError(msg)) if not cond else None
            ),
        )

# -- treasury ----------------------------------------------------------------
try:
    from . import treasury as treasury  # type: ignore
except Exception:
    try:
        from vm_py.runtime import treasury_api as _treasury_api  # type: ignore

        treasury = SimpleNamespace(  # type: ignore[assignment]
            balance=_treasury_api.balance,
            transfer=_treasury_api.transfer,
        )
    except Exception:  # pragma: no cover
        treasury = SimpleNamespace(  # type: ignore[assignment]
            balance=lambda addr: 0,
            transfer=lambda from_addr, to_addr, amount: None,
        )

# -- syscalls ----------------------------------------------------------------
try:
    from . import syscalls as syscalls  # type: ignore
except Exception:
    try:
        from vm_py.runtime import syscalls_api as _syscalls_api  # type: ignore

        # Expose a stable subset; missing functions become no-ops.
        syscalls = SimpleNamespace(  # type: ignore[assignment]
            blob_pin=getattr(_syscalls_api, "blob_pin", lambda ns, data: None),
            ai_enqueue=getattr(_syscalls_api, "ai_enqueue", lambda *a, **k: None),
            quantum_enqueue=getattr(
                _syscalls_api, "quantum_enqueue", lambda *a, **k: None
            ),
            read_result=getattr(_syscalls_api, "read_result", lambda task_id: None),
            zk_verify=getattr(_syscalls_api, "zk_verify", lambda *a, **k: False),
            random=getattr(_syscalls_api, "random", lambda n: b""),
        )
    except Exception:  # pragma: no cover
        syscalls = SimpleNamespace(  # type: ignore[assignment]
            blob_pin=lambda ns, data: None,
            ai_enqueue=lambda *a, **k: None,
            quantum_enqueue=lambda *a, **k: None,
            read_result=lambda task_id: None,
            zk_verify=lambda *a, **k: False,
            random=lambda n: b"",
        )
