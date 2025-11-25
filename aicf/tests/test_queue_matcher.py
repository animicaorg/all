import time
from datetime import datetime, timedelta, timezone

import pytest

from aicf.aitypes.provider import Capability, ProviderStatus
from aicf.aitypes.job import JobKind, JobRecord
from aicf.queue import priority as qprio
from aicf.queue import quotas as qquotas
from aicf.queue import assignment as qassign


# --------------------------------------------------------------------------------------
# NOTE TO IMPLEMENTERS
# These tests assert behavior rather than exact function signatures. They assume:
#
# 1) aicf.queue.priority
#    - rank(jobs, now: float | int | datetime, seed: int | None = None) -> list[JobRecord]
#      Returns jobs sorted by decreasing priority with a deterministic tie-breaker.
#    - (Optionally) score(job, now) -> float for introspection; not required by tests.
#
# 2) aicf.queue.quotas
#    - QuotaTracker(default_concurrent: int = 1)
#      .has_capacity(provider_id: str, kind: JobKind) -> bool
#      .consume(provider_id: str, kind: JobKind) -> None
#      .release(provider_id: str, kind: JobKind) -> None
#
# 3) aicf.queue.assignment
#    - is_eligible(provider, job) -> bool
#      Eligibility checks at least Capability vs JobKind and ProviderStatus.ACTIVE.
#    - match_once(providers: list[Provider], jobs: list[JobRecord],
#                 quotas: qquotas.QuotaTracker, now, seed: int | None = None,
#                 lease_ttl_s: int = 60)
#        -> list[tuple[str, str]]  # [(job_id, provider_id)]
#      Chooses highest-priority eligible jobs subject to provider quotas, one job
#      per provider per call unless additional capacity exists.
#
# If your actual API differs, adjust the adapter shims in the assignment/priority/quotas
# modules to satisfy these behaviors.
# --------------------------------------------------------------------------------------


class _Provider:
    """Tiny provider test stub that mirrors the fields used by assignment.is_eligible."""
    def __init__(self, provider_id: str, capabilities: Capability, status: ProviderStatus):
        self.provider_id = provider_id
        self.capabilities = capabilities
        self.status = status
        self.health = 1.0  # optional field some implementations may consult


def _mk_job(
    job_id: str,
    kind: JobKind,
    *,
    fee: int,
    size_bytes: int = 1024,
    created_at: float | None = None,
    tier: str = "standard",
) -> JobRecord:
    """
    Construct a JobRecord with the minimum attributes the matcher/priority use.
    Assumed JobRecord signature (for convenience in tests):
      JobRecord(job_id: str, kind: JobKind, fee: int, size_bytes: int, created_at: float, tier: str)
    If your implementation differs, consider supporting these attribute names for duck-typing.
    """
    if created_at is None:
        created_at = time.time()
    return JobRecord(
        job_id=job_id,
        kind=kind,
        fee=fee,
        size_bytes=size_bytes,
        created_at=created_at,
        tier=tier,
    )


def _now_ts() -> float:
    return datetime.now(tz=timezone.utc).timestamp()


def test_eligibility_matches_only_capable_providers(monkeypatch):
    """
    AI job should only be assigned to AI-capable providers; Quantum job likewise.
    """
    now = _now_ts()

    p_ai = _Provider("prov-ai", Capability.AI, ProviderStatus.ACTIVE)
    p_q = _Provider("prov-q", Capability.QUANTUM, ProviderStatus.ACTIVE)
    p_both = _Provider("prov-both", Capability.AI | Capability.QUANTUM, ProviderStatus.ACTIVE)
    providers = [p_ai, p_q, p_both]

    job_ai = _mk_job("job-ai-1", JobKind.AI, fee=10_000, created_at=now - 5)
    job_q = _mk_job("job-q-1", JobKind.QUANTUM, fee=12_000, created_at=now - 3)

    quotas = qquotas.QuotaTracker(default_concurrent=1)

    # Perform a single matching pass for each job kind (order by priority first)
    ranked = qprio.rank([job_ai, job_q], now=now, seed=1234)
    # We call match_once with both and let the matcher assign to eligible providers.
    assignments = qassign.match_once(providers, ranked, quotas, now=now, seed=42, lease_ttl_s=60)

    # Convert to dict job_id->provider_id for easy assertions
    amap = {j: p for (j, p) in assignments}

    # AI job can go to AI-only or BOTH; Quantum to Q-only or BOTH.
    assert amap["job-ai-1"] in {"prov-ai", "prov-both"}
    assert amap["job-q-1"] in {"prov-q", "prov-both"}
    # Ensure we did not assign AI job to Quantum-only, nor Quantum job to AI-only.
    assert amap.get("job-ai-1") != "prov-q"
    assert amap.get("job-q-1") != "prov-ai"


def test_quotas_limit_concurrent_leases_and_release_allows_more(monkeypatch):
    """
    With a quota of 1 concurrent lease, a provider must not receive a second job
    until the first lease is released.
    """
    now = _now_ts()

    p_ai = _Provider("prov-ai", Capability.AI, ProviderStatus.ACTIVE)
    providers = [p_ai]

    job1 = _mk_job("job-ai-1", JobKind.AI, fee=5_000, created_at=now - 10)
    job2 = _mk_job("job-ai-2", JobKind.AI, fee=4_000, created_at=now - 9)

    quotas = qquotas.QuotaTracker(default_concurrent=1)

    # First pass: should assign only one job to prov-ai
    assignments_1 = qassign.match_once(providers, qprio.rank([job1, job2], now, seed=1), quotas, now, seed=1)
    assert assignments_1 == [("job-ai-1", "prov-ai")] or assignments_1 == [("job-ai-2", "prov-ai")]

    # Second pass without releasing: no capacity, so no new assignment
    assignments_2 = qassign.match_once(providers, qprio.rank([job1, job2], now, seed=1), quotas, now, seed=2)
    assert assignments_2 == []

    # Release the lease capacity for prov-ai and try again
    quotas.release("prov-ai", JobKind.AI)
    assignments_3 = qassign.match_once(providers, qprio.rank([job1, job2], now, seed=1), quotas, now, seed=3)
    assert assignments_3, "Expected second job to be assigned after releasing quota"
    # Ensure the other (previously unassigned) job is now assigned
    assigned_job_ids = {jid for (jid, _) in assignments_1} | {jid for (jid, _) in assignments_3}
    assert assigned_job_ids == {"job-ai-1", "job-ai-2"}


def test_priority_tie_breaker_is_deterministic(monkeypatch):
    """
    When two jobs have identical (fee, age, size, tier), the priority module must
    apply a stable deterministic tie-breaker. We expect lexicographic job_id to win.
    """
    base = _now_ts()
    # Same fee/size/age/tier; only IDs differ
    j_a = _mk_job("job-0001", JobKind.AI, fee=10_000, size_bytes=2048, created_at=base - 7, tier="gold")
    j_b = _mk_job("job-0002", JobKind.AI, fee=10_000, size_bytes=2048, created_at=base - 7, tier="gold")

    ranked = qprio.rank([j_b, j_a], now=base, seed=999)  # order input oddly to ensure rank() normalizes
    assert [j.job_id for j in ranked] == ["job-0001", "job-0002"], "Expected lexicographic ID tie-breaker"

    # With a single-capacity provider, the matcher should pick job-0001 first.
    quotas = qquotas.QuotaTracker(default_concurrent=1)
    prov = _Provider("prov-ai", Capability.AI, ProviderStatus.ACTIVE)
    first = qassign.match_once([prov], ranked, quotas, now=base, seed=999)
    assert first == [("job-0001", "prov-ai")]

    # After releasing capacity, the second job should be chosen.
    quotas.release("prov-ai", JobKind.AI)
    second = qassign.match_once([prov], ranked, quotas, now=base, seed=999)
    assert second == [("job-0002", "prov-ai")]


def test_ineligible_providers_are_skipped_even_if_high_capacity(monkeypatch):
    """
    Providers lacking the required capability or not ACTIVE must be ignored, even if they
    have ample quotas.
    """
    now = _now_ts()

    prov_inactive = _Provider("prov-inactive", Capability.AI, ProviderStatus.JAILED)
    prov_wrong_cap = _Provider("prov-q", Capability.QUANTUM, ProviderStatus.ACTIVE)
    prov_ok = _Provider("prov-ai", Capability.AI, ProviderStatus.ACTIVE)
    providers = [prov_inactive, prov_wrong_cap, prov_ok]

    job = _mk_job("job-ai-1", JobKind.AI, fee=9_000, created_at=now - 2)

    quotas = qquotas.QuotaTracker(default_concurrent=5)

    ranked = qprio.rank([job], now, seed=123)
    assigns = qassign.match_once(providers, ranked, quotas, now, seed=123)
    assert assigns == [("job-ai-1", "prov-ai")], "Only ACTIVE AI-capable provider should receive the job"


def test_priority_prefers_higher_fee_then_older_age(monkeypatch):
    """
    Sanity test for priority: higher fee dominates; with equal fees, older job wins.
    """
    now = _now_ts()

    older = _mk_job("job-old", JobKind.AI, fee=10_000, created_at=now - 50)
    newer_higher_fee = _mk_job("job-newer-pricier", JobKind.AI, fee=11_000, created_at=now - 5)
    newer_equal_fee = _mk_job("job-newer", JobKind.AI, fee=10_000, created_at=now - 5)

    r1 = qprio.rank([older, newer_higher_fee], now, seed=7)
    assert [j.job_id for j in r1][0] == "job-newer-pricier", "Higher fee should outrank older age"

    r2 = qprio.rank([older, newer_equal_fee], now, seed=7)
    assert [j.job_id for j in r2][0] == "job-old", "With equal fees, older job should outrank newer"


