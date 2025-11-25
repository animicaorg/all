from __future__ import annotations

from typing import Any, Dict

import pytest

from vm_py.runtime.events_api import VmError
from vm_py.runtime.resource_caps import ResourceGuard, new_guard_from_manifest


def _mk_manifest(
    *,
    caps: list[str],
    max_blob_bytes: int = 0,
    max_ai_units: int = 0,
    max_zk_proofs: int = 0,
    max_random_bytes: int = 0,
    max_treasury_transfers: int = 0,
) -> Dict[str, Any]:
    """
    Minimal manifest stub focusing on the `resources` section.

    We keep it small so tests don't depend on the full manifest schema.
    """
    return {
        "name": "CapsExample",
        "version": "1.0.0",
        "language": "python-vm",
        "entry": "contract.py",
        "resources": {
            "caps": caps,
            "limits": {
                "max_blob_bytes": max_blob_bytes,
                "max_ai_units": max_ai_units,
                "max_zk_proofs": max_zk_proofs,
                "max_random_bytes": max_random_bytes,
                "max_treasury_transfers": max_treasury_transfers,
            },
        },
    }


def test_blob_pin_respects_max_blob_bytes_limit() -> None:
    """
    A contract that declares the blob.pin capability with a finite
    max_blob_bytes limit must not be able to exceed that budget.
    """
    manifest = _mk_manifest(
        caps=["blob.pin"],
        max_blob_bytes=1024,
    )
    guard = new_guard_from_manifest(manifest)

    # Two calls that exactly fill the limit are allowed.
    guard.use_blob_pin(600)
    guard.use_blob_pin(424)
    assert guard.usage.blob_bytes == 1024

    # Any further blob.pin usage should fail with a resource_exhausted VmError.
    with pytest.raises(VmError) as excinfo:
        guard.use_blob_pin(1)

    err = excinfo.value
    assert getattr(err, "code", None) == "resource_exhausted"
    ctx = getattr(err, "context", {}) or {}
    assert ctx.get("kind") == "blob.pin"
    assert ctx.get("used") == 1025
    assert ctx.get("limit") == 1024


def test_compute_ai_enqueue_respects_max_ai_units() -> None:
    """
    compute.ai.enqueue should be gated by max_ai_units and raise a structured
    VmError when the limit is exceeded.
    """
    manifest = _mk_manifest(
        caps=["compute.ai.enqueue"],
        max_ai_units=100,
    )
    guard = new_guard_from_manifest(manifest)

    guard.use_ai_units(40)
    guard.use_ai_units(60)
    assert guard.usage.ai_units == 100

    # 1 more unit is over budget.
    with pytest.raises(VmError) as excinfo:
        guard.use_ai_units(1)

    err = excinfo.value
    assert getattr(err, "code", None) == "resource_exhausted"
    ctx = getattr(err, "context", {}) or {}
    assert ctx.get("kind") == "compute.ai.enqueue"
    assert ctx.get("used") == 101
    assert ctx.get("limit") == 100


def test_zk_verify_respects_max_zk_proofs() -> None:
    """
    zk.verify calls should be counted and clamped by max_zk_proofs.
    """
    manifest = _mk_manifest(
        caps=["zk.verify"],
        max_zk_proofs=3,
    )
    guard = ResourceGuard.from_manifest(manifest)

    # Three proofs are fine (default proofs=1 per call).
    guard.use_zk_verify()
    guard.use_zk_verify()
    guard.use_zk_verify()
    assert guard.usage.zk_proofs == 3

    # Fourth proof breaks the limit.
    with pytest.raises(VmError) as excinfo:
        guard.use_zk_verify()

    err = excinfo.value
    assert getattr(err, "code", None) == "resource_exhausted"
    ctx = getattr(err, "context", {}) or {}
    assert ctx.get("kind") == "zk.verify"
    assert ctx.get("used") == 4
    assert ctx.get("limit") == 3


def test_missing_capability_denies_syscall_even_with_large_limit() -> None:
    """
    If a manifest omits a capability from `resources.caps`, the corresponding
    syscall must be rejected *even if* the numeric limit would have allowed it.
    """
    manifest = _mk_manifest(
        caps=[],  # blob.pin is not declared
        max_blob_bytes=10_000,
    )
    guard = new_guard_from_manifest(manifest)

    with pytest.raises(VmError) as excinfo:
        guard.use_blob_pin(1)

    err = excinfo.value
    assert getattr(err, "code", None) == "capability_denied"
    ctx = getattr(err, "context", {}) or {}
    assert ctx.get("cap") == "blob.pin"


def test_defaults_are_zero_caps_and_zero_limits() -> None:
    """
    When the manifest has no resources section, the guard should behave as a
    hard-deny for all capped syscalls: no caps and zero limits.
    """
    manifest: Dict[str, Any] = {
        "name": "NoResources",
        "version": "1.0.0",
        "language": "python-vm",
        "entry": "contract.py",
        # No "resources" key at all.
    }

    guard = ResourceGuard.from_manifest(manifest)

    # No caps should be active.
    assert guard.caps == set()

    # Any attempt to use a resource should be blocked with capability_denied.
    with pytest.raises(VmError) as excinfo:
        guard.use_ai_units(10)
    assert getattr(excinfo.value, "code", None) == "capability_denied"

    with pytest.raises(VmError) as excinfo2:
        guard.use_zk_verify()
    assert getattr(excinfo2.value, "code", None) == "capability_denied"


