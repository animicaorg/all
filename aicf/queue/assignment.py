from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from weakref import WeakKeyDictionary

from aicf.aitypes.job import JobRecord
from aicf.aitypes.provider import Capability, ProviderStatus
from aicf.queue.jobkind import JobKind

# Per-quotas state; entries auto-GC when quotas objects disappear.
_STATE: "WeakKeyDictionary[Any, Dict[str, Any]]" = WeakKeyDictionary()
_CUR: Optional[Dict[str, Any]] = None  # currently bound state (last used quotas)


def _state_for(quotas: Any) -> Dict[str, Any]:
    st = _STATE.get(quotas)
    if st is None:
        st = {
            "leases": {},  # type: Dict[str, Tuple[str, float]]  # job_id -> (provider_id, expires_at)
            "jobs": {},  # type: Dict[str, JobRecord]
            "completed": set(),  # type: Set[str]
        }
        _STATE[quotas] = st
    return st


def _bind_quotas(quotas: Any) -> None:
    global _CUR
    _CUR = _state_for(quotas)


def _prov_id(p: Any) -> str:
    return getattr(p, "id", None) or getattr(p, "provider_id", None) or str(p)


def _prov_caps(p: Any) -> Capability:
    return getattr(p, "capabilities", Capability(0))


def _prov_status(p: Any) -> ProviderStatus:
    return getattr(p, "status", ProviderStatus.ACTIVE)


def _required_cap(kind: JobKind) -> Capability:
    return Capability.AI if kind == JobKind.AI else Capability.QUANTUM


def _provider_busy(
    leases: Dict[str, Tuple[str, float]], provider_id: str, now: float
) -> bool:
    for _, (pid, exp) in leases.items():
        if pid == provider_id and exp > now:
            return True
    return False


def _eligible(p: Any, job: JobRecord) -> bool:
    if _prov_status(p) != ProviderStatus.ACTIVE:
        return False
    return bool(_prov_caps(p) & _required_cap(job.kind))


def match_once(
    providers: Iterable[Any],
    ranked_jobs: Iterable[JobRecord],
    quotas: Any,
    now: float,
    *,
    seed: int = 0,
    lease_ttl_s: int = 60,
) -> List[Tuple[str, str]]:
    """
    Greedy single-pass matcher with at most one assignment per provider.
    Respects ACTIVE status/capability and avoids re-assigning active leases
    or jobs marked completed on this quotas instance.
    """
    _bind_quotas(quotas)
    st = _CUR  # type: ignore[assignment]
    leases: Dict[str, Tuple[str, float]] = st["leases"]
    jobs_cache: Dict[str, JobRecord] = st["jobs"]
    completed: Set[str] = st["completed"]

    # Drop expired leases up-front
    expire_leases(quotas, now=now)

    assigns: List[Tuple[str, str]] = []
    taken: set[str] = set()
    prov_list = list(providers)
    jobs = list(ranked_jobs)

    for job in jobs:
        jid = job.job_id
        if jid in completed:
            continue
        holder = leases.get(jid)
        if holder and holder[1] > now:
            continue  # alive lease

        for p in prov_list:
            pid = _prov_id(p)
            if pid in taken:
                continue
            if not _eligible(p, job):
                continue
            if _provider_busy(leases, pid, now):
                continue

            # grant lease
            leases[jid] = (pid, now + float(lease_ttl_s))
            jobs_cache[jid] = job
            assigns.append((jid, pid))
            taken.add(pid)
            break

    return assigns


def get_lease(job_id: str) -> Tuple[Optional[str], Optional[float]]:
    st = _CUR
    if not st:
        return (None, None)
    rec = st["leases"].get(job_id)
    return (rec[0], rec[1]) if rec else (None, None)


def renew_lease(job_id: str, provider_id: str, *, extend_s: int, now: float) -> float:
    st = _CUR
    if not st:
        return float("nan")
    leases: Dict[str, Tuple[str, float]] = st["leases"]
    holder, exp = leases.get(job_id, (None, None))
    if holder != provider_id or exp is None:
        return float("nan")
    base = exp if exp > now else now
    new_exp = base + float(extend_s)
    leases[job_id] = (provider_id, new_exp)
    return new_exp


def cancel_lease(job_id: str, provider_id: str, quotas: Any) -> bool:
    _bind_quotas(quotas)
    st = _CUR  # type: ignore[assignment]
    leases: Dict[str, Tuple[str, float]] = st["leases"]
    holder, _ = leases.get(job_id, (None, None))
    if holder != provider_id:
        return False
    leases.pop(job_id, None)
    return True  # re-queued (not completed)


def expire_leases(quotas: Any, *, now: float) -> List[str]:
    _bind_quotas(quotas)
    st = _CUR  # type: ignore[assignment]
    leases: Dict[str, Tuple[str, float]] = st["leases"]
    expired: List[str] = []
    for jid, (pid, exp) in list(leases.items()):
        if exp <= now:
            leases.pop(jid, None)
            expired.append(jid)
    return expired


# ---- compatibility hooks used by quotas.release(...) ----


def _release_by_provider_for(quotas: Any, provider_id: str) -> Optional[str]:
    """
    Free ONE active lease held by provider_id for the given quotas instance and
    mark that job as completed so it won't be re-selected on the next pass.
    Select the lexicographically-smallest job_id to keep behavior stable.
    """
    st = _state_for(quotas)
    leases: Dict[str, Tuple[str, float]] = st["leases"]
    completed: Set[str] = st["completed"]

    # Deterministic choice for tests
    for jid, (pid, _) in sorted(leases.items(), key=lambda kv: kv[0]):
        if pid == provider_id:
            leases.pop(jid, None)
            completed.add(jid)
            return jid
    return None


# Legacy form (falls back to the currently bound quotas state, if any)
def _release_by_provider(provider_id: str) -> Optional[str]:
    st = _CUR
    if not st:
        return None

    # Fabricate a minimal quotas proxy to reuse the helper
    class _Q:
        pass

    # Not used because we go via st directly:
    leases: Dict[str, Tuple[str, float]] = st["leases"]
    completed: Set[str] = st["completed"]
    for jid, (pid, _) in sorted(leases.items(), key=lambda kv: kv[0]):
        if pid == provider_id:
            leases.pop(jid, None)
            completed.add(jid)
            return jid
    return None
