from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Set

from vm_py.runtime.events_api import VmError


_CAP_BLOB_PIN = "blob.pin"
_CAP_AI_ENQUEUE = "compute.ai.enqueue"
_CAP_QUANTUM_ENQUEUE = "compute.quantum.enqueue"
_CAP_RESULT_READ = "compute.result.read"
_CAP_ZK_VERIFY = "zk.verify"
_CAP_RANDOM_READ = "random.read"
_CAP_TREASURY_TRANSFER = "treasury.transfer"


@dataclass
class ResourceLimits:
    """
    Per-contract resource limits derived from the manifest.

    Field names follow the doc/schema around:

      resources:
        caps: [...]
        limits:
          max_blob_bytes: ...
          max_ai_units: ...
          max_quantum_units: ...
          max_zk_proofs: ...
          max_random_bytes: ...
          max_treasury_transfers: ...

    All values default to 0 when absent.
    """

    max_blob_bytes: int = 0
    max_ai_units: int = 0
    max_quantum_units: int = 0
    max_zk_proofs: int = 0
    max_random_bytes: int = 0
    max_treasury_transfers: int = 0


@dataclass
class ResourceUsage:
    """
    Mutable counters tracking what a single execution / session has consumed
    so far. The host decides whether this is per-tx, per-block, etc.
    """

    blob_bytes: int = 0
    ai_units: int = 0
    quantum_units: int = 0
    zk_proofs: int = 0
    random_bytes: int = 0
    treasury_transfers: int = 0


@dataclass
class ResourceGuard:
    """
    Small helper used by the VM host to enforce manifest-declared resource
    caps at runtime.

    The VM or syscall shims are expected to call the appropriate use_*
    methods before dispatching to the underlying host service. If a call
    would exceed the configured limit, a VmError is raised with:

      - code == "resource_exhausted"
      - message describing the exhausted resource
      - context = {"kind", "used", "limit"}

    If a capability is not declared in `caps`, we raise a VmError with:

      - code == "capability_denied"
      - context = {"cap": "<cap-name>"}
    """

    limits: ResourceLimits
    usage: ResourceUsage
    caps: Set[str]

    # --------- construction helpers ---------

    @classmethod
    def from_manifest(cls, manifest: Dict[str, Any]) -> "ResourceGuard":
        """
        Extract caps + limits from a manifest structure.

        Expected shape (per docs):

          {
            "resources": {
              "caps": ["blob.pin", "compute.ai.enqueue", ...],
              "limits": {
                "max_blob_bytes": 123,
                "max_ai_units": 456,
                ...
              }
            }
          }

        Omitted fields default to zero / empty.
        """
        resources = (manifest.get("resources") or {})  # type: ignore[assignment]

        caps_raw = resources.get("caps") or []
        if not isinstance(caps_raw, Iterable) or isinstance(caps_raw, (str, bytes)):
            caps: Set[str] = set()
        else:
            caps = {str(c) for c in caps_raw}

        limits_raw = resources.get("limits") or {}
        if not isinstance(limits_raw, dict):
            limits_raw = {}

        def _get_int(key: str) -> int:
            v = limits_raw.get(key, 0)
            try:
                return int(v)
            except Exception:
                return 0

        limits = ResourceLimits(
            max_blob_bytes=_get_int("max_blob_bytes"),
            max_ai_units=_get_int("max_ai_units"),
            max_quantum_units=_get_int("max_quantum_units"),
            max_zk_proofs=_get_int("max_zk_proofs"),
            max_random_bytes=_get_int("max_random_bytes"),
            max_treasury_transfers=_get_int("max_treasury_transfers"),
        )

        return cls(limits=limits, usage=ResourceUsage(), caps=caps)

    # --------- internal helpers ---------

    def _require_cap(self, cap: str) -> None:
        if cap not in self.caps:
            raise VmError(
                f"capability denied: {cap}",
                code="capability_denied",
                context={"cap": cap},
            )

    def _exhausted(self, kind: str, used: int, limit: int) -> None:
        raise VmError(
            f"resource exhausted for {kind}: used {used} > limit {limit}",
            code="resource_exhausted",
            context={"kind": kind, "used": used, "limit": limit},
        )

    # --------- public “use” methods (called by syscall shims) ---------

    def use_blob_pin(self, n_bytes: int) -> None:
        if n_bytes <= 0:
            return
        self._require_cap(_CAP_BLOB_PIN)
        new = self.usage.blob_bytes + n_bytes
        if new > self.limits.max_blob_bytes:
            self._exhausted(_CAP_BLOB_PIN, new, self.limits.max_blob_bytes)
        self.usage.blob_bytes = new

    def use_ai_units(self, units: int) -> None:
        if units <= 0:
            return
        self._require_cap(_CAP_AI_ENQUEUE)
        new = self.usage.ai_units + units
        if new > self.limits.max_ai_units:
            self._exhausted(_CAP_AI_ENQUEUE, new, self.limits.max_ai_units)
        self.usage.ai_units = new

    def use_quantum_units(self, units: int) -> None:
        if units <= 0:
            return
        self._require_cap(_CAP_QUANTUM_ENQUEUE)
        new = self.usage.quantum_units + units
        if new > self.limits.max_quantum_units:
            self._exhausted(_CAP_QUANTUM_ENQUEUE, new, self.limits.max_quantum_units)
        self.usage.quantum_units = new

    def use_zk_verify(self, proofs: int = 1) -> None:
        if proofs <= 0:
            return
        self._require_cap(_CAP_ZK_VERIFY)
        new = self.usage.zk_proofs + proofs
        if new > self.limits.max_zk_proofs:
            self._exhausted(_CAP_ZK_VERIFY, new, self.limits.max_zk_proofs)
        self.usage.zk_proofs = new

    def use_random_bytes(self, n_bytes: int) -> None:
        if n_bytes <= 0:
            return
        self._require_cap(_CAP_RANDOM_READ)
        new = self.usage.random_bytes + n_bytes
        if new > self.limits.max_random_bytes:
            self._exhausted(_CAP_RANDOM_READ, new, self.limits.max_random_bytes)
        self.usage.random_bytes = new

    def use_treasury_transfer(self) -> None:
        self._require_cap(_CAP_TREASURY_TRANSFER)
        new = self.usage.treasury_transfers + 1
        if new > self.limits.max_treasury_transfers:
            self._exhausted(
                _CAP_TREASURY_TRANSFER,
                new,
                self.limits.max_treasury_transfers,
            )
        self.usage.treasury_transfers = new


def new_guard_from_manifest(manifest: Dict[str, Any]) -> ResourceGuard:
    """
    Convenience wrapper used by hosts when wiring up a contract instance.
    """
    return ResourceGuard.from_manifest(manifest)
