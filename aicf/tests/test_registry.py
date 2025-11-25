import pytest

from aicf.errors import RegistryError
from aicf.registry.registry import Registry
from aicf.registry.allowlist import Allowlist

# Types/enums used by the registry
from aicf.aitypes.provider import (
    ProviderId,
    ProviderStatus,
    Capability,  # IntFlag: Capability.AI | Capability.QUANTUM
)


def _mk_registry(allow: Allowlist | None = None) -> Registry:
    """
    Helper to construct a Registry with a provided Allowlist (or a permissive default).
    """
    return Registry(allowlist=allow or Allowlist())


def test_register_and_attest_success(monkeypatch):
    """
    Provider with valid attestation should be registered; caps and endpoints persisted.
    """
    reg = _mk_registry()

    # Stub attestation verifier to succeed
    monkeypatch.setattr(
        "aicf.registry.verify_attest.verify_attestation", lambda att: True
    )

    pid: ProviderId = "prov-001"
    endpoints = {
        "ai": "https://ai.example.com/endpoint",
        "quantum": "https://q.example.com/endpoint",
        "heartbeat": "https://ai.example.com/hb",
    }
    caps = Capability.AI | Capability.QUANTUM

    prov = reg.register_provider(
        provider_id=pid,
        capabilities=caps,
        endpoints=endpoints,
        attestation=b"dummy-attestation",
        stake=100_000,
        region="us-east-1",
    )

    assert prov.id == pid
    assert prov.status == ProviderStatus.ACTIVE
    assert prov.capabilities & Capability.AI
    assert prov.capabilities & Capability.QUANTUM
    assert prov.endpoints["ai"] == endpoints["ai"]

    # Fetch via API
    fetched = reg.get_provider(pid)
    assert fetched.id == pid
    assert fetched.capabilities == caps
    assert fetched.endpoints == endpoints

    # List should contain our provider
    ids = [p.id for p in reg.list_providers()]
    assert pid in ids


def test_capability_updates(monkeypatch):
    """
    Capability changes should be applied and observable.
    """
    reg = _mk_registry()
    monkeypatch.setattr(
        "aicf.registry.verify_attest.verify_attestation", lambda att: True
    )

    pid: ProviderId = "prov-002"
    prov = reg.register_provider(
        provider_id=pid,
        capabilities=Capability.AI,  # start AI-only
        endpoints={"ai": "https://ai.example/endpoint"},
        attestation=b"ok",
        stake=50_000,
        region="eu-west-1",
    )
    assert prov.capabilities == Capability.AI

    # Add QUANTUM
    updated = reg.update_capabilities(pid, Capability.AI | Capability.QUANTUM)
    assert updated.capabilities & Capability.QUANTUM
    assert updated.capabilities & Capability.AI

    # Remove AI, keep QUANTUM only
    updated = reg.update_capabilities(pid, Capability.QUANTUM)
    assert updated.capabilities == Capability.QUANTUM


def test_endpoints_update_and_status_transition(monkeypatch):
    """
    Endpoints can be updated; status transitions should be enforced and reflected.
    """
    reg = _mk_registry()
    monkeypatch.setattr(
        "aicf.registry.verify_attest.verify_attestation", lambda att: True
    )

    pid: ProviderId = "prov-003"
    reg.register_provider(
        provider_id=pid,
        capabilities=Capability.AI,
        endpoints={"ai": "https://ai.old/ep"},
        attestation=b"ok",
        stake=10_000,
        region="ap-south-1",
    )

    # Update endpoints
    ep2 = {"ai": "https://ai.new/ep"}
    p2 = reg.update_endpoints(pid, ep2)
    assert p2.endpoints == ep2

    # Transition to PAUSED/INACTIVE then back to ACTIVE
    p3 = reg.set_status(pid, ProviderStatus.PAUSED)
    assert p3.status == ProviderStatus.PAUSED
    p4 = reg.set_status(pid, ProviderStatus.ACTIVE)
    assert p4.status == ProviderStatus.ACTIVE


def test_allowlist_blocks_registration(monkeypatch):
    """
    Denied providers (by id or region) must not be registered.
    """
    # Allowlist denies a specific provider id and a region
    allow = Allowlist(denied_ids={"bad-prov"}, denied_regions={"cn-north-1"})
    reg = _mk_registry(allow)

    monkeypatch.setattr(
        "aicf.registry.verify_attest.verify_attestation", lambda att: True
    )

    # Denied by id
    with pytest.raises(RegistryError):
        reg.register_provider(
            provider_id="bad-prov",
            capabilities=Capability.AI,
            endpoints={"ai": "https://ai.example/ep"},
            attestation=b"ok",
            stake=10_000,
            region="us-east-1",
        )

    # Denied by region
    with pytest.raises(RegistryError):
        reg.register_provider(
            provider_id="ok-prov",
            capabilities=Capability.AI,
            endpoints={"ai": "https://ai.example/ep"},
            attestation=b"ok",
            stake=10_000,
            region="cn-north-1",
        )


def test_attestation_failure_rejected(monkeypatch):
    """
    If attestation verification fails, registration must fail.
    """
    reg = _mk_registry()

    # Stub verifier to fail
    def _fail(_):
        return False

    monkeypatch.setattr("aicf.registry.verify_attest.verify_attestation", _fail)

    with pytest.raises(RegistryError):
        reg.register_provider(
            provider_id="prov-attest-fail",
            capabilities=Capability.AI,
            endpoints={"ai": "https://ai.example/ep"},
            attestation=b"invalid",
            stake=5_000,
            region="us-west-2",
        )
