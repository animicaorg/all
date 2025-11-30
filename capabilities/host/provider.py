"""
capabilities.host.provider
==========================

Central registry and deterministic syscall dispatch for contract-facing
capabilities (blob pinning, AI/Quantum enqueue, zk.verify, randomness,
treasury hooks, and result reads).

This module provides a *single* process-wide registry that providers
(implemented in sibling modules) can register with, and that the VM /
execution layer can call through in a deterministic way.

Canonical operation keys (stable API surface):

- "blob.pin"                     → (ns: int, data: bytes) -> Receipt/Commitment
- "compute.ai.enqueue"           → (model: str, prompt: bytes, **opts) -> JobReceipt
- "compute.quantum.enqueue"      → (circuit: dict|bytes, shots: int, **opts) -> JobReceipt
- "result.read"                  → (task_id: str|bytes) -> ResultRecord | raises NoResultYet
- "zk.verify"                    → (circuit: Any, proof: Any, public_input: Any) -> bool
- "random.bytes"                 → (n: int) -> bytes
- "treasury.debit"               → (amount: int) -> None
- "treasury.credit"              → (to: bytes, amount: int) -> None

Providers should expose callables with the signature
    func(ctx: SyscallContext, **kwargs) -> Any
or positional equivalents, and register them under the above keys.

Determinism & Safety
--------------------
- All dispatches require a SyscallContext that pins chainId, height, txHash,
  and caller address. Providers must derive any IDs deterministically from
  this context and inputs (e.g., task_id = H(chainId|height|txHash|caller|payload)).
- Providers must *not* perform non-deterministic I/O in the dispatch path.
  Off-chain work should be queued with deterministic identifiers; final
  consumption occurs after inclusion of on-chain proofs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import RLock
from typing import (Any, Callable, Dict, Iterable, Optional, Protocol,
                    runtime_checkable)

from ..errors import (CapError, LimitExceeded,  # re-exported exceptions
                      NoResultYet, NotDeterministic)

try:
    # Optional metrics; provider calls should best-effort bump these if available.
    from .. import metrics as _metrics  # type: ignore
except Exception:  # pragma: no cover
    _metrics = None  # graceful noop


log = logging.getLogger("capabilities.host.provider")


# ----------------------------
# Context & typing
# ----------------------------


@dataclass(frozen=True)
class SyscallContext:
    """
    Deterministic syscall context. Every host call *must* include this.

    Attributes:
        chain_id:   Integer network identifier.
        height:     Current block height (or candidate height during execution).
        tx_hash:    32-byte transaction hash (bytes).
        caller:     32-byte address / account id (bytes).
        gas_left:   Optional gas-left hint for metering/logging (not authoritative).
    """

    chain_id: int
    height: int
    tx_hash: bytes
    caller: bytes
    gas_left: Optional[int] = None


@runtime_checkable
class ProviderFn(Protocol):
    def __call__(self, ctx: SyscallContext, *args: Any, **kwargs: Any) -> Any: ...


# Canonical operation keys (string constants to avoid typos)
BLOB_PIN = "blob.pin"
AI_ENQ = "compute.ai.enqueue"
Q_ENQ = "compute.quantum.enqueue"
RESULT_READ = "result.read"
ZK_VERIFY = "zk.verify"
RAND_BYTES = "random.bytes"
TREASURY_DEBIT = "treasury.debit"
TREASURY_CREDIT = "treasury.credit"

CANONICAL_KEYS: tuple[str, ...] = (
    BLOB_PIN,
    AI_ENQ,
    Q_ENQ,
    RESULT_READ,
    ZK_VERIFY,
    RAND_BYTES,
    TREASURY_DEBIT,
    TREASURY_CREDIT,
)


# ----------------------------
# Registry
# ----------------------------


class ProviderRegistry:
    """
    Thread-safe registry keyed by canonical operation strings.

    Providers register functions; the registry handles basic invariants,
    logs structured events, and wraps exceptions into CapError where helpful.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._handlers: Dict[str, ProviderFn] = {}

    # ---- registration API ----

    def register(self, key: str, fn: ProviderFn) -> None:
        """
        Register a provider function under `key`.

        If a handler already exists for the key, it will be replaced.
        """
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        if not callable(fn):
            raise TypeError("fn must be callable")

        # Optional determinism hint: allow providers to set attribute `_deterministic = True`
        deterministic = getattr(fn, "_deterministic", None)
        if deterministic is not True:
            log.warning(
                "registering provider without explicit _deterministic=True",
                extra={"key": key, "fn": getattr(fn, "__qualname__", repr(fn))},
            )

        with self._lock:
            self._handlers[key] = fn
            log.info(
                "provider_registered",
                extra={"key": key, "fn": getattr(fn, "__qualname__", repr(fn))},
            )

    def unregister(self, key: str) -> None:
        with self._lock:
            self._handlers.pop(key, None)
            log.info("provider_unregistered", extra={"key": key})

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._handlers

    def names(self) -> Iterable[str]:
        with self._lock:
            return tuple(self._handlers.keys())

    def require(self, key: str) -> ProviderFn:
        with self._lock:
            if key not in self._handlers:
                raise CapError(f"no provider registered for {key!r}")
            return self._handlers[key]

    # ---- dispatch API ----

    def call(self, key: str, ctx: SyscallContext, /, *args: Any, **kwargs: Any) -> Any:
        """
        Dispatch to the registered provider.

        Wraps unexpected exceptions as CapError while preserving known subclasses.
        """
        fn = self.require(key)
        if _metrics and hasattr(_metrics, "host_call_started"):
            try:
                _metrics.host_call_started.labels(key=key).inc()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover
                pass

        try:
            result = fn(ctx, *args, **kwargs)
            if _metrics and hasattr(_metrics, "host_call_succeeded"):
                try:
                    _metrics.host_call_succeeded.labels(key=key).inc()  # type: ignore[attr-defined]
                except Exception:  # pragma: no cover
                    pass
            return result

        except (CapError, NotDeterministic, LimitExceeded, NoResultYet):
            # Known, intentional surfaces: bubble up unchanged.
            if _metrics and hasattr(_metrics, "host_call_failed"):
                try:
                    _metrics.host_call_failed.labels(key=key, kind="expected").inc()  # type: ignore[attr-defined]
                except Exception:  # pragma: no cover
                    pass
            raise

        except Exception as e:  # pragma: no cover - unexpected error path
            if _metrics and hasattr(_metrics, "host_call_failed"):
                try:
                    _metrics.host_call_failed.labels(key=key, kind="unexpected").inc()  # type: ignore[attr-defined]
                except Exception:
                    pass
            log.exception(
                "host_call_unexpected_error",
                extra={
                    "key": key,
                    "error": repr(e),
                    "chain_id": ctx.chain_id,
                    "height": ctx.height,
                },
            )
            raise CapError(f"unexpected error in provider {key}: {e}") from e

    # ---- typed convenience wrappers (sugar) ----

    def blob_pin(self, ctx: SyscallContext, namespace: int, data: bytes) -> Any:
        return self.call(BLOB_PIN, ctx, namespace=namespace, data=data)

    def ai_enqueue(
        self, ctx: SyscallContext, model: str, prompt: bytes | str, **opts: Any
    ) -> Any:
        return self.call(AI_ENQ, ctx, model=model, prompt=prompt, **opts)

    def quantum_enqueue(
        self, ctx: SyscallContext, circuit: Any, shots: int, **opts: Any
    ) -> Any:
        return self.call(Q_ENQ, ctx, circuit=circuit, shots=shots, **opts)

    def result_read(self, ctx: SyscallContext, task_id: str | bytes) -> Any:
        return self.call(RESULT_READ, ctx, task_id=task_id)

    def zk_verify(
        self, ctx: SyscallContext, circuit: Any, proof: Any, public_input: Any
    ) -> bool:
        out = self.call(
            ZK_VERIFY, ctx, circuit=circuit, proof=proof, public_input=public_input
        )
        if not isinstance(out, bool):
            raise CapError("zk.verify provider must return a boolean")
        return out

    def random_bytes(self, ctx: SyscallContext, n: int) -> bytes:
        out = self.call(RAND_BYTES, ctx, n=n)
        if not isinstance(out, (bytes, bytearray)):
            raise CapError("random.bytes provider must return bytes")
        return bytes(out)

    def treasury_debit(self, ctx: SyscallContext, amount: int) -> None:
        self.call(TREASURY_DEBIT, ctx, amount=amount)

    def treasury_credit(self, ctx: SyscallContext, to: bytes, amount: int) -> None:
        self.call(TREASURY_CREDIT, ctx, to=to, amount=amount)


# ----------------------------
# Singleton accessor
# ----------------------------

_REGISTRY: Optional[ProviderRegistry] = None


def get_registry() -> ProviderRegistry:
    """Return the global provider registry (creating it on first use)."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ProviderRegistry()
        _maybe_autoload_default_providers(_REGISTRY)
    return _REGISTRY


def _maybe_autoload_default_providers(reg: ProviderRegistry) -> None:
    """
    Best-effort lazy import of sibling provider modules to self-register
    their handlers. Each module may call `get_registry().register(...)`
    in its module body or expose a `register(registry)` function.

    Absence of modules is not an error (feature-gated builds).
    """
    import importlib

    for modname in (
        "capabilities.host.blob",
        "capabilities.host.compute",
        "capabilities.host.result_read",
        "capabilities.host.zk",
        "capabilities.host.random",
        "capabilities.host.treasury",
    ):
        try:
            mod = importlib.import_module(modname)
            # If module exposes an explicit register(reg) hook, invoke it.
            register_fn = getattr(mod, "register", None)
            if callable(register_fn):
                register_fn(reg)
        except Exception as e:  # pragma: no cover
            # Modules may be missing in a minimal build; log at DEBUG.
            log.debug(
                "autoload_provider_skipped",
                extra={"module": modname, "reason": repr(e)},
            )


__all__ = [
    # context / typing
    "SyscallContext",
    "ProviderFn",
    # constants
    "BLOB_PIN",
    "AI_ENQ",
    "Q_ENQ",
    "RESULT_READ",
    "ZK_VERIFY",
    "RAND_BYTES",
    "TREASURY_DEBIT",
    "TREASURY_CREDIT",
    "CANONICAL_KEYS",
    # registry API
    "ProviderRegistry",
    "get_registry",
]
