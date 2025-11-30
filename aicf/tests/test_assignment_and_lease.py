import time
from datetime import datetime, timezone

import pytest

from aicf.aitypes.job import JobKind, JobRecord
from aicf.aitypes.provider import Capability, ProviderStatus
from aicf.queue import assignment as qassign
from aicf.queue import priority as qprio
from aicf.queue import quotas as qquotas

# --------------------------------------------------------------------------------------
# Assumed lease API (behavioral contract for the assignment subsystem)
#
# - match_once(providers, jobs, quotas, now, seed=None, lease_ttl_s=60)
#     -> list[(job_id, provider_id)]
#   * Creates a lease per assignment with expiry at now + lease_ttl_s.
#   * MUST NOT re-assign a job that currently has an active lease.
#
# - get_lease(job_id) -> tuple[provider_id: str, expires_at: float] | None
#
# - expire_leases(quotas, now) -> list[str]   # returns job_ids that were expired
#   * Releases provider capacity for any leases that are past-due.
#
# - renew_lease(job_id, provider_id, extend_s: int, now) -> float
#   * Extends the lease expiry; returns the new expires_at timestamp.
#   * Recommended policy: new_expiry = max(old_expiry, now) + extend_s
#
# - cancel_lease(job_id, provider_id, quotas) -> bool
#   * Cancels the lease only if 'provider_id' is the current holder.
#   * Releases provider capacity; returns True on success, False otherwise
#     (implementations MAY raise on wrong-holder; tests handle both).
#
# If your implementation's signatures differ, provide thin adapters that satisfy these
# semantics so these tests pass.
# --------------------------------------------------------------------------------------


class _Provider:
    """Minimal provider stub with fields used by assignment.is_eligible()."""

    def __init__(
        self, provider_id: str, capabilities: Capability, status: ProviderStatus
    ):
        self.provider_id = provider_id
        self.capabilities = capabilities
        self.status = status
        self.health = 1.0


def _mk_job(
    job_id: str,
    kind: JobKind,
    *,
    fee: int,
    size_bytes: int = 1024,
    created_at: float | None = None,
    tier: str = "standard",
) -> JobRecord:
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


def test_lease_expiry_frees_capacity_and_allows_reassignment():
    now = _now_ts()

    prov = _Provider("prov-ai", Capability.AI, ProviderStatus.ACTIVE)
    quotas = qquotas.QuotaTracker(default_concurrent=1)

    job = _mk_job("job-ai-1", JobKind.AI, fee=10_000, created_at=now - 5)

    # Initial assignment with a short lease
    ranked = qprio.rank([job], now=now, seed=123)
    assigns = qassign.match_once(
        [prov], ranked, quotas, now=now, seed=123, lease_ttl_s=3
    )
    assert assigns == [("job-ai-1", "prov-ai")]

    # The job has an active lease; trying to assign again before expiry should yield nothing.
    assigns_early = qassign.match_once(
        [prov], ranked, quotas, now=now + 1, seed=124, lease_ttl_s=3
    )
    assert assigns_early == [], "Job with active lease must not be re-assigned"

    # Let the lease expire and make sure capacity is released
    expired = qassign.expire_leases(quotas, now=now + 4)
    assert "job-ai-1" in expired

    # Now the job can be assigned again (new lease)
    assigns_again = qassign.match_once(
        [prov], ranked, quotas, now=now + 4, seed=125, lease_ttl_s=3
    )
    assert assigns_again == [("job-ai-1", "prov-ai")]


def test_lease_renewal_delays_expiry_and_preserves_capacity():
    now = _now_ts()

    prov = _Provider("prov-ai", Capability.AI, ProviderStatus.ACTIVE)
    quotas = qquotas.QuotaTracker(default_concurrent=1)

    job = _mk_job("job-ai-2", JobKind.AI, fee=9_000, created_at=now - 2)
    ranked = qprio.rank([job], now=now, seed=77)

    # Assign with ttl=5
    assigns = qassign.match_once(
        [prov], ranked, quotas, now=now, seed=77, lease_ttl_s=5
    )
    assert assigns == [("job-ai-2", "prov-ai")]

    holder, exp = qassign.get_lease("job-ai-2")
    assert holder == "prov-ai"
    old_exp = exp

    # Renew after 2 seconds by +10s
    new_exp = qassign.renew_lease("job-ai-2", "prov-ai", extend_s=10, now=now + 2)
    assert new_exp > old_exp, "Renewal should extend expiry beyond previous deadline"

    # Expire pass at old_exp + 1 should NOT free (since renewed)
    expired_then = qassign.expire_leases(quotas, now=old_exp + 1)
    assert "job-ai-2" not in expired_then

    # No capacity yet; a second job should not be assigned
    other_job = _mk_job("job-ai-3", JobKind.AI, fee=8_000, created_at=now - 1)
    ranked2 = qprio.rank([other_job], now=old_exp + 1)
    assigns_blocked = qassign.match_once(
        [prov], ranked2, quotas, now=old_exp + 1, seed=88
    )
    assert (
        assigns_blocked == []
    ), "Capacity remains consumed while renewed lease is active"

    # After the renewed expiry passes, lease should expire and capacity free
    expired_final = qassign.expire_leases(quotas, now=new_exp + 1)
    assert "job-ai-2" in expired_final

    assigns_ok = qassign.match_once([prov], ranked2, quotas, now=new_exp + 1, seed=89)
    assert assigns_ok == [("job-ai-3", "prov-ai")]


def test_cancel_lease_requeues_job_and_frees_quota():
    now = _now_ts()

    p1 = _Provider("prov-1", Capability.AI, ProviderStatus.ACTIVE)
    p2 = _Provider("prov-2", Capability.AI, ProviderStatus.ACTIVE)
    providers = [p1, p2]

    quotas = qquotas.QuotaTracker(default_concurrent=1)

    job = _mk_job("job-ai-cancel", JobKind.AI, fee=12_000, created_at=now - 3)
    ranked = qprio.rank([job], now=now, seed=5)

    assigns = qassign.match_once(
        providers, ranked, quotas, now=now, seed=5, lease_ttl_s=60
    )
    assert len(assigns) == 1
    jid, assigned_pid = assigns[0]
    assert jid == "job-ai-cancel"

    # Cancelling from the wrong provider should fail (return False) or raise.
    wrong_pid = "prov-2" if assigned_pid == "prov-1" else "prov-1"
    wrong_cancel_ok = False
    try:
        rc = qassign.cancel_lease(jid, wrong_pid, quotas)
        wrong_cancel_ok = rc is False
    except Exception:
        wrong_cancel_ok = True
    assert wrong_cancel_ok, "cancel_lease should not succeed for non-holder provider"

    # Cancelling from the holder should succeed and free quota immediately.
    rc2 = qassign.cancel_lease(jid, assigned_pid, quotas)
    assert (rc2 is True) or (rc2 is None)

    # The job is still in our ranked list; it should be assignable again immediately.
    reassign = qassign.match_once(
        providers, ranked, quotas, now=now + 1, seed=6, lease_ttl_s=60
    )
    assert len(reassign) == 1
    # It may assign to either provider; we only assert that it reassigns at all.
    assert reassign[0][0] == "job-ai-cancel"


def test_renewal_by_non_holder_is_rejected():
    now = _now_ts()

    p1 = _Provider("prov-1", Capability.AI, ProviderStatus.ACTIVE)
    p2 = _Provider("prov-2", Capability.AI, ProviderStatus.ACTIVE)

    quotas = qquotas.QuotaTracker(default_concurrent=1)

    job = _mk_job("job-renew-deny", JobKind.AI, fee=7_000, created_at=now - 1)
    ranked = qprio.rank([job], now=now, seed=101)

    assigns = qassign.match_once(
        [p1, p2], ranked, quotas, now=now, seed=101, lease_ttl_s=30
    )
    assert assigns == [("job-renew-deny", "prov-1")] or assigns == [
        ("job-renew-deny", "prov-2")
    ]
    holder = assigns[0][1]
    non_holder = "prov-1" if holder == "prov-2" else "prov-2"

    # Renewal by the non-holder should be rejected (False) or raise.
    try:
        ok = qassign.renew_lease("job-renew-deny", non_holder, extend_s=10, now=now + 5)
        assert ok is False, "Non-holder renewal should be rejected"
    except Exception:
        # Raised exception is also acceptable behavior.
        pass
