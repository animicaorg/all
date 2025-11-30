from __future__ import annotations

"""
AICF Treasury — provider balances & escrows
-------------------------------------------

This module maintains *internal*, deterministic ledgers for:
  • Provider balances (available, staked)
  • Job-scoped escrows (funds reserved to pay providers upon proof/settlement)

It is deliberately storage-agnostic and uses pure-Python data structures with
total ordering and explicit serialization helpers. Persistence is delegated to
higher layers (e.g., aicf.adapters.state_db) which can periodically snapshot
`TreasuryState.dump()` and restore via `TreasuryState.load()`.

Amounts are expressed as integer *base units* (no floats). All operations check:
  • Non-negativity
  • Sufficient balances before debits/transfers
  • Escrow invariants: sum(escrows.amount_open) == account.escrowed

Concurrency: a coarse `threading.RLock` protects mutating methods for safety.

Typical flow
~~~~~~~~~~~~
1) Requestor funds a job escrow (off-ledger or in a separate requestor ledger).
2) When a provider completes a job and an on-chain proof is accepted, the
   AICF settlement code calls `settle_job_to_provider(job_id, provider_id, amount)`
   which releases escrow and credits the provider `available` balance.
3) Providers can move funds into `staked` (lock) or out (unlock) according to
   registry policy enforced elsewhere.

Only provider-side accounting lives here. Requestor-side escrows (client funds)
can be tracked externally and mirrored into this module when a payout is due.

"""

from dataclasses import asdict, dataclass, field
from threading import RLock
from typing import Dict, Iterable, List, Optional, Tuple

from aicf.aitypes.job import JobRecord  # type: ignore
from aicf.aitypes.provider import ProviderId  # type: ignore
from aicf.errors import AICFError, InsufficientStake  # type: ignore

Amount = int
Height = int
OpName = Literal[
    "credit",
    "debit",
    "hold_escrow",
    "release_escrow",
    "settle_job",
    "stake_lock",
    "stake_unlock",
    "slash",
]


class TreasuryError(AICFError):
    """Base error for treasury operations."""


class InsufficientFunds(TreasuryError):
    """Raised when an account lacks available/escrowed balance for an operation."""


class EscrowNotFound(TreasuryError):
    """Raised when referencing a non-existent or closed escrow."""


class EscrowAlreadyClosed(TreasuryError):
    """Raised when attempting to release/settle a closed escrow."""


def _ensure_nonneg(x: int, name: str) -> None:
    if x < 0:
        raise TreasuryError(f"{name} must be non-negative, got {x}")


def _safe_add(a: int, b: int) -> int:
    c = a + b
    if c < 0:
        raise TreasuryError("integer overflow/underflow in addition")
    return c


def _safe_sub(a: int, b: int) -> int:
    c = a - b
    if c < 0:
        raise InsufficientFunds(f"insufficient balance: have {a}, need {b}")
    return c


@dataclass(frozen=True)
class EscrowId:
    """
    Deterministic escrow identifier.

    Convention: use the deterministic AICF Job id (`JobRecord.job_id`) for escrow ids
    when escrows are job-scoped (recommended). For custom escrows, the caller may
    construct a string value that is stable across nodes.
    """

    value: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass
class EscrowRecord:
    escrow_id: EscrowId
    job_id: Optional[str]  # link to JobRecord if applicable
    provider_id: ProviderId
    amount: Amount
    created_height: Height
    reason: str = "job"
    closed: bool = False
    closed_height: Optional[Height] = None

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["escrow_id"] = self.escrow_id.value
        d["provider_id"] = str(self.provider_id)
        return d

    @staticmethod
    def from_dict(d: Dict) -> "EscrowRecord":
        return EscrowRecord(
            escrow_id=EscrowId(d["escrow_id"]),
            job_id=d.get("job_id"),
            provider_id=ProviderId(d["provider_id"]),
            amount=int(d["amount"]),
            created_height=int(d["created_height"]),
            reason=d.get("reason", "job"),
            closed=bool(d.get("closed", False)),
            closed_height=d.get("closed_height"),
        )


@dataclass
class ProviderAccount:
    provider_id: ProviderId
    available: Amount = 0
    escrowed: Amount = 0
    staked: Amount = 0
    jailed: bool = False
    escrows: Dict[str, EscrowRecord] = field(default_factory=dict)
    journal_seq: int = 0

    def snapshot(self) -> Dict:
        return {
            "provider_id": str(self.provider_id),
            "available": self.available,
            "escrowed": self.escrowed,
            "staked": self.staked,
            "jailed": self.jailed,
            "journal_seq": self.journal_seq,
            "escrows": {k: v.to_dict() for k, v in sorted(self.escrows.items())},
        }

    @staticmethod
    def restore(d: Dict) -> "ProviderAccount":
        acct = ProviderAccount(
            provider_id=ProviderId(d["provider_id"]),
            available=int(d["available"]),
            escrowed=int(d["escrowed"]),
            staked=int(d["staked"]),
            jailed=bool(d.get("jailed", False)),
            journal_seq=int(d.get("journal_seq", 0)),
        )
        for k, ed in d.get("escrows", {}).items():
            acct.escrows[k] = EscrowRecord.from_dict(ed)
        acct._assert_escrow_invariant()
        return acct

    # --- internal helpers ---

    def _bump_journal(self) -> int:
        self.journal_seq += 1
        return self.journal_seq

    def _assert_escrow_invariant(self) -> None:
        open_sum = sum(e.amount for e in self.escrows.values() if not e.closed)
        if open_sum != self.escrowed:
            raise TreasuryError(
                f"escrow invariant violated for {self.provider_id}: "
                f"escrowed={self.escrowed} != sum(open escrows)={open_sum}"
            )


@dataclass(frozen=True)
class JournalEntry:
    seq: int
    provider_id: ProviderId
    op: OpName
    amount: Amount
    height: Height
    meta: Dict[str, str] = field(default_factory=dict)
    available_after: Amount = 0
    escrowed_after: Amount = 0
    staked_after: Amount = 0


class TreasuryState:
    """
    In-memory treasury state for provider balances & escrows.

    Storage-agnostic: call `dump()` to serialize to a JSON-friendly dict, and `load()`
    to restore. The journal is retained only in-memory for observability; callers
    may persist it as needed.
    """

    def __init__(self) -> None:
        self._accounts: Dict[str, ProviderAccount] = {}
        self._journal: List[JournalEntry] = []
        self._lock = RLock()

    # --- load/save ---

    def dump(self) -> Dict:
        with self._lock:
            return {
                "accounts": {k: v.snapshot() for k, v in sorted(self._accounts.items())}
            }

    @classmethod
    def load(cls, data: Dict) -> "TreasuryState":
        st = cls()
        for k, v in data.get("accounts", {}).items():
            st._accounts[k] = ProviderAccount.restore(v)
        return st

    # --- introspection ---

    def get_account(self, provider_id: ProviderId) -> ProviderAccount:
        with self._lock:
            key = str(provider_id)
            if key not in self._accounts:
                self._accounts[key] = ProviderAccount(provider_id=provider_id)
            return self._accounts[key]

    def balances(self, provider_id: ProviderId) -> Tuple[Amount, Amount, Amount]:
        """Return (available, escrowed, staked)."""
        acct = self.get_account(provider_id)
        return acct.available, acct.escrowed, acct.staked

    def journal(self) -> Iterable[JournalEntry]:
        return tuple(self._journal)

    # --- mutations (all locked) ---

    def credit(
        self,
        provider_id: ProviderId,
        amount: Amount,
        *,
        height: Height,
        reason: str = "credit",
    ) -> JournalEntry:
        _ensure_nonneg(amount, "amount")
        with self._lock:
            acct = self.get_account(provider_id)
            acct.available = _safe_add(acct.available, amount)
            seq = acct._bump_journal()
            je = JournalEntry(
                seq=seq,
                provider_id=provider_id,
                op="credit",
                amount=amount,
                height=height,
                meta={"reason": reason},
                available_after=acct.available,
                escrowed_after=acct.escrowed,
                staked_after=acct.staked,
            )
            self._journal.append(je)
            return je

    def debit(
        self,
        provider_id: ProviderId,
        amount: Amount,
        *,
        height: Height,
        reason: str = "debit",
    ) -> JournalEntry:
        _ensure_nonneg(amount, "amount")
        with self._lock:
            acct = self.get_account(provider_id)
            acct.available = _safe_sub(acct.available, amount)
            seq = acct._bump_journal()
            je = JournalEntry(
                seq=seq,
                provider_id=provider_id,
                op="debit",
                amount=amount,
                height=height,
                meta={"reason": reason},
                available_after=acct.available,
                escrowed_after=acct.escrowed,
                staked_after=acct.staked,
            )
            self._journal.append(je)
            return je

    def hold_escrow(
        self,
        *,
        provider_id: ProviderId,
        job: Optional[JobRecord],
        escrow_id: Optional[EscrowId],
        amount: Amount,
        height: Height,
        reason: str = "job",
    ) -> EscrowRecord:
        """
        Move `amount` from available → escrowed and create an EscrowRecord.
        If `escrow_id` is not provided and `job` is, uses job.job_id.
        """
        _ensure_nonneg(amount, "amount")
        with self._lock:
            acct = self.get_account(provider_id)
            acct.available = _safe_sub(acct.available, amount)
            acct.escrowed = _safe_add(acct.escrowed, amount)

            eid = escrow_id or EscrowId(
                job.job_id if job else f"custom:{acct.journal_seq+1}"
            )
            key = eid.value
            if key in acct.escrows and not acct.escrows[key].closed:
                raise TreasuryError(f"escrow '{key}' already exists and is open")

            rec = EscrowRecord(
                escrow_id=eid,
                job_id=job.job_id if job else None,
                provider_id=provider_id,
                amount=amount,
                created_height=height,
                reason=reason,
            )
            acct.escrows[key] = rec
            acct._assert_escrow_invariant()

            seq = acct._bump_journal()
            self._journal.append(
                JournalEntry(
                    seq=seq,
                    provider_id=provider_id,
                    op="hold_escrow",
                    amount=amount,
                    height=height,
                    meta={
                        "escrow_id": key,
                        "reason": reason,
                        "job_id": rec.job_id or "",
                    },
                    available_after=acct.available,
                    escrowed_after=acct.escrowed,
                    staked_after=acct.staked,
                )
            )
            return rec

    def release_escrow(
        self,
        *,
        provider_id: ProviderId,
        escrow_id: EscrowId,
        height: Height,
        to_available: bool = True,
        reason: str = "release",
    ) -> JournalEntry:
        """
        Close an escrow and move funds:
          • to_available=True  → escrowed → available (refund to provider)
          • to_available=False → escrowed burned/redirected (e.g., penalty or external refund)
        """
        with self._lock:
            acct = self.get_account(provider_id)
            key = escrow_id.value
            rec = acct.escrows.get(key)
            if rec is None:
                raise EscrowNotFound(f"escrow '{key}' not found")
            if rec.closed:
                raise EscrowAlreadyClosed(f"escrow '{key}' is already closed")

            acct.escrowed = _safe_sub(acct.escrowed, rec.amount)
            if to_available:
                acct.available = _safe_add(acct.available, rec.amount)

            rec.closed = True
            rec.closed_height = height
            acct._assert_escrow_invariant()

            seq = acct._bump_journal()
            je = JournalEntry(
                seq=seq,
                provider_id=provider_id,
                op="release_escrow",
                amount=rec.amount,
                height=height,
                meta={
                    "escrow_id": key,
                    "reason": reason,
                    "to": "available" if to_available else "sink",
                },
                available_after=acct.available,
                escrowed_after=acct.escrowed,
                staked_after=acct.staked,
            )
            self._journal.append(je)
            return je

    def settle_job_to_provider(
        self,
        *,
        provider_id: ProviderId,
        escrow_id: EscrowId,
        height: Height,
    ) -> JournalEntry:
        """
        Close the escrow and credit the provider's available balance.
        Equivalent to `release_escrow(..., to_available=True)` but op is tagged as 'settle_job'.
        """
        with self._lock:
            acct = self.get_account(provider_id)
            key = escrow_id.value
            rec = acct.escrows.get(key)
            if rec is None:
                raise EscrowNotFound(f"escrow '{key}' not found")
            if rec.closed:
                raise EscrowAlreadyClosed(f"escrow '{key}' is already closed")

            # move escrowed → available
            acct.escrowed = _safe_sub(acct.escrowed, rec.amount)
            acct.available = _safe_add(acct.available, rec.amount)

            rec.closed = True
            rec.closed_height = height
            acct._assert_escrow_invariant()

            seq = acct._bump_journal()
            je = JournalEntry(
                seq=seq,
                provider_id=provider_id,
                op="settle_job",
                amount=rec.amount,
                height=height,
                meta={"escrow_id": key, "job_id": rec.job_id or ""},
                available_after=acct.available,
                escrowed_after=acct.escrowed,
                staked_after=acct.staked,
            )
            self._journal.append(je)
            return je

    def stake_lock(
        self,
        provider_id: ProviderId,
        amount: Amount,
        *,
        height: Height,
        reason: str = "stake",
    ) -> JournalEntry:
        """Move funds from available → staked."""
        _ensure_nonneg(amount, "amount")
        with self._lock:
            acct = self.get_account(provider_id)
            acct.available = _safe_sub(acct.available, amount)
            acct.staked = _safe_add(acct.staked, amount)
            seq = acct._bump_journal()
            je = JournalEntry(
                seq=seq,
                provider_id=provider_id,
                op="stake_lock",
                amount=amount,
                height=height,
                meta={"reason": reason},
                available_after=acct.available,
                escrowed_after=acct.escrowed,
                staked_after=acct.staked,
            )
            self._journal.append(je)
            return je

    def stake_unlock(
        self,
        provider_id: ProviderId,
        amount: Amount,
        *,
        height: Height,
        reason: str = "unstake",
    ) -> JournalEntry:
        """
        Move funds from staked → available (policy on lock periods is enforced upstream).
        """
        _ensure_nonneg(amount, "amount")
        with self._lock:
            acct = self.get_account(provider_id)
            acct.staked = _safe_sub(acct.staked, amount)
            acct.available = _safe_add(acct.available, amount)
            seq = acct._bump_journal()
            je = JournalEntry(
                seq=seq,
                provider_id=provider_id,
                op="stake_unlock",
                amount=amount,
                height=height,
                meta={"reason": reason},
                available_after=acct.available,
                escrowed_after=acct.escrowed,
                staked_after=acct.staked,
            )
            self._journal.append(je)
            return je

    def slash(
        self,
        provider_id: ProviderId,
        amount: Amount,
        *,
        height: Height,
        reason: str = "slash",
    ) -> JournalEntry:
        """
        Apply a slash against staked funds (preferred). If insufficient stake, the remainder is
        taken from available. Escrowed funds are *not* touched here.
        """
        _ensure_nonneg(amount, "amount")
        with self._lock:
            acct = self.get_account(provider_id)
            take_from_stake = min(acct.staked, amount)
            acct.staked -= take_from_stake
            remainder = amount - take_from_stake
            if remainder:
                if acct.available < remainder:
                    raise InsufficientStake(
                        f"slash requires {amount}, have staked={take_from_stake} + available={acct.available}"
                    )
                acct.available -= remainder

            seq = acct._bump_journal()
            je = JournalEntry(
                seq=seq,
                provider_id=provider_id,
                op="slash",
                amount=amount,
                height=height,
                meta={
                    "reason": reason,
                    "from_stake": str(take_from_stake),
                    "from_available": str(remainder),
                },
                available_after=acct.available,
                escrowed_after=acct.escrowed,
                staked_after=acct.staked,
            )
            self._journal.append(je)
            return je

    # --- utilities ---

    def assert_consistent(self) -> None:
        """Verify invariants across all accounts."""
        with self._lock:
            for acct in self._accounts.values():
                acct._assert_escrow_invariant()
