import pytest

from aicf.errors import InsufficientStake, RegistryError
from aicf.registry.staking import Staking
from aicf.registry.registry import Registry
from aicf.registry.allowlist import Allowlist
from aicf.aitypes.provider import Capability, ProviderStatus


def _mk_registry() -> Registry:
    """Permissive registry with attestation stubbed to succeed."""
    reg = Registry(allowlist=Allowlist())
    return reg


def _mk_staking() -> Staking:
    """
    Staking with clear thresholds and a short unlock delay for tests.
    Assumptions about Staking API:
      - Staking(min_stake_ai, min_stake_quantum, unlock_delay_blocks)
      - stake(provider_id, amount) -> int (new total stake)
      - increase(provider_id, amount) -> int
      - request_unstake(provider_id, amount, current_height) -> dict with 'release_height'
      - process_unlocks(current_height) -> list of matured unlock records
      - total_stake(provider_id) -> int
      - effective_stake(provider_id) -> int (excludes pending unstakes)
      - ensure_minimum(provider_id, caps: Capability) -> None or raises InsufficientStake
    """
    return Staking(min_stake_ai=50_000, min_stake_quantum=80_000, unlock_delay_blocks=5)


@pytest.fixture(autouse=True)
def stub_attestation(monkeypatch):
    # Make attestation verification always succeed for these tests.
    monkeypatch.setattr("aicf.registry.verify_attest.verify_attestation", lambda _: True)


def test_stake_increase_and_minimums():
    reg = _mk_registry()
    staking = _mk_staking()

    # Register a provider with zero initial stake; ACTIVE after register.
    pid = "prov-stake-001"
    reg.register_provider(
        provider_id=pid,
        capabilities=Capability.AI,  # start AI-only
        endpoints={"ai": "https://ai.example/ep"},
        attestation=b"ok",
        stake=0,
        region="us-east-1",
    )

    # Stake some funds
    total = staking.stake(pid, 60_000)
    assert total == 60_000
    assert staking.total_stake(pid) == 60_000
    assert staking.effective_stake(pid) == 60_000

    # Meets AI minimum, not QUANTUM
    staking.ensure_minimum(pid, Capability.AI)
    with pytest.raises(InsufficientStake):
        staking.ensure_minimum(pid, Capability.QUANTUM)

    # Increase stake to meet QUANTUM too
    total = staking.increase(pid, 25_000)
    assert total == 85_000
    staking.ensure_minimum(pid, Capability.QUANTUM)


def test_unstake_lock_and_release_affects_effective_stake():
    reg = _mk_registry()
    staking = _mk_staking()

    pid = "prov-stake-002"
    reg.register_provider(
        provider_id=pid,
        capabilities=Capability.AI,
        endpoints={"ai": "https://ai.example/ep"},
        attestation=b"ok",
        stake=0,
        region="us-west-2",
    )

    staking.stake(pid, 60_000)
    # Schedule an unstake that drops below AI minimum in *effective* terms.
    h = 100
    req = staking.request_unstake(pid, amount=15_000, current_height=h)
    assert isinstance(req, dict) and "release_height" in req
    assert staking.total_stake(pid) == 60_000
    # Pending unstake reduces *effective* stake
    assert staking.effective_stake(pid) == 45_000

    with pytest.raises(InsufficientStake):
        staking.ensure_minimum(pid, Capability.AI)

    # Before release height, nothing should change
    staking.process_unlocks(h + 4)
    assert staking.total_stake(pid) == 60_000
    assert staking.effective_stake(pid) == 45_000

    # At/after release height, the unstake matures and reduces total stake
    matured = staking.process_unlocks(req["release_height"])
    assert matured, "Expected the scheduled unstake to mature"
    assert staking.total_stake(pid) == 45_000
    assert staking.effective_stake(pid) == 45_000


def test_unstake_more_than_staked_is_rejected():
    reg = _mk_registry()
    staking = _mk_staking()

    pid = "prov-stake-003"
    reg.register_provider(
        provider_id=pid,
        capabilities=Capability.AI | Capability.QUANTUM,
        endpoints={"ai": "https://ai.example/ep", "quantum": "https://q.example/ep"},
        attestation=b"ok",
        stake=0,
        region="eu-central-1",
    )

    staking.stake(pid, 30_000)
    with pytest.raises(RegistryError):
        staking.request_unstake(pid, amount=40_000, current_height=5)


def test_capability_upgrade_blocked_by_insufficient_stake():
    """
    If a provider attempts to upgrade capabilities without meeting stake minimums,
    it should be rejected by the staking rules.
    """
    reg = _mk_registry()
    staking = _mk_staking()

    pid = "prov-stake-004"
    reg.register_provider(
        provider_id=pid,
        capabilities=Capability.AI,  # AI only initially
        endpoints={"ai": "https://ai.example/ep"},
        attestation=b"ok",
        stake=0,
        region="ap-south-1",
    )
    staking.stake(pid, 55_000)  # Enough for AI, not for QUANTUM

    # Sanity: AI allowed, QUANTUM not allowed
    staking.ensure_minimum(pid, Capability.AI)
    with pytest.raises(InsufficientStake):
        staking.ensure_minimum(pid, Capability.QUANTUM)

    # If the Registry enforces the staking check on upgrade, the update should fail.
    with pytest.raises(InsufficientStake):
        # Registry is expected to consult Staking.ensure_minimum internally.
        reg.update_capabilities(pid, Capability.AI | Capability.QUANTUM)


def test_effective_stake_recovers_after_unlock_then_upgrade_allowed():
    """
    After an unstake matures and we top back up, capability upgrades should pass.
    """
    reg = _mk_registry()
    staking = _mk_staking()

    pid = "prov-stake-005"
    reg.register_provider(
        provider_id=pid,
        capabilities=Capability.AI,
        endpoints={"ai": "https://ai.example/ep"},
        attestation=b"ok",
        stake=0,
        region="eu-west-1",
    )

    # Start with enough for AI, not QUANTUM
    staking.stake(pid, 60_000)
    with pytest.raises(InsufficientStake):
        staking.ensure_minimum(pid, Capability.QUANTUM)

    # Top-up to meet QUANTUM and try the upgrade via Registry
    staking.increase(pid, 25_000)
    staking.ensure_minimum(pid, Capability.QUANTUM)  # now should pass
    updated = reg.update_capabilities(pid, Capability.AI | Capability.QUANTUM)
    assert updated.status == ProviderStatus.ACTIVE
    assert updated.capabilities & Capability.QUANTUM
