"""
core.staking — Proof-of-Stake bond table and leader selection (Animica PoS)
===========================================================================

Introduced with FORK_POS_MINTING. Animica becomes **hybrid**: a block is valid
if it carries either a PoW proof (``workType == 0``, the existing theta rule) or
a PoS proof (``workType == WORKTYPE_POS``, see ``core.pos``).

Storage model
-------------
Bonds live in ordinary account *storage* under a fixed system address, so they
are covered by ``compute_state_root`` and therefore consensus-verified like any
other state. Nothing about ``Account`` changes, so the state encoding of every
existing account is untouched.

    storage[STAKE_SYSTEM_ADDR][staker_account_key] = CBOR({
        "b": [[amount, unlock_at], ...],   # bonds, ascending unlock_at
        "n": "display name",               # label from `animica stake`, never an identity
    })

`staker_account_key` is the canonical 32-byte account key — the same key used
for balances — so stake can never be attributed to an address the signer does
not control.

Weight
------
A staker's weight is the sum of **all** its bonds, matured or not: the coins
stay in the stake account until an explicit ``TxUnstake`` moves them back to
spendable balance, so they are still at stake. Only maturity gates *withdrawal*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .encoding.cbor import cbor_dumps, cbor_loads
from .utils.hash import sha3_256

# Fixed 32-byte system address holding the bond table. Derived from a domain
# string so it is reproducible and cannot collide with a real (pubkey-derived)
# account key.
STAKE_SYSTEM_ADDR: bytes = sha3_256(b"animica/pos/stake-table/v1")

# Minimum bond, in base units (nANM; 9 decimals => 1 ANM = 1_000_000_000).
# A floor keeps leader selection from being dominated by dust entries and keeps
# the staker set small enough to walk deterministically on every block.
MIN_STAKE_NANM: int = 1_000_000_000  # 1 ANM

# Hard cap on distinct bonds per staker, so the CBOR blob and the selection walk
# stay bounded no matter how many times an address stakes.
MAX_BONDS_PER_STAKER: int = 64

SECONDS_PER_DAY: int = 86_400

# ---------------------------------------------------------------------------
# bootstrap validator
# ---------------------------------------------------------------------------
#
# THE BOOTSTRAP PROBLEM: minting a PoS block requires stake in state, but
# getting stake into state requires a block to carry the TxStake — and at the
# fork height the chain is frozen with zero hashrate, so no block is coming.
# Seeding the bond during the first PoS block does not help either: the proof is
# verified BEFORE the block executes, so the table is still empty at that point.
#
# THE RESOLUTION: while the bond table is completely empty, the leader set is a
# single hardcoded bootstrap validator — the treasury. It mints the first PoS
# block, that block carries the treasury's own TxStake, and from the next height
# the table is non-empty and this fallback is inert forever. It is a pure
# function of committed state ("is the table empty?"), so every node replaying
# history reaches the same verdict, and it cannot be re-entered by unstaking:
# see `_BOOTSTRAP_EXHAUSTED_KEY` below.
#
# Treasury account key, verified against the wallet's recorded ml_dsa_65 pubkey:
#   address     anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga
#   account key 0de5187830c1493cb6ce5341e2cf23901084cffd6cff37e404c727a301036229
BOOTSTRAP_STAKER: bytes = bytes.fromhex(
    "0de5187830c1493cb6ce5341e2cf23901084cffd6cff37e404c727a301036229"
)

# Synthetic weight for the bootstrap validator. Never credited to any balance
# and never written to state — it exists only to make the first slot's leader
# selection well-defined while the real table is empty.
BOOTSTRAP_WEIGHT: int = MIN_STAKE_NANM

# Once any real bond has existed, the bootstrap fallback must never come back —
# otherwise every staker unbonding would silently hand block production to a
# single hardcoded key. The flag is set by the stake handler on the first real
# bond and is part of the stake address's storage, so it is covered by the state
# root like everything else here.
_BOOTSTRAP_EXHAUSTED_KEY: bytes = b"__bootstrap_exhausted__"

# ---------------------------------------------------------------------------
# stake intent carried in the transaction's `data` field
# ---------------------------------------------------------------------------
#
# WHY `data` AND NOT A TOP-LEVEL BODY FIELD: the mempool normalises every
# incoming body to a canonical field set (`core.utils.normalize_tx_body`) and
# DISCARDS unknown keys, so a `kind` / `days` / `name` written beside `value`
# never reaches execution — the transaction silently executes as a transfer.
# `data` survives that normalisation AND sits inside `payload.v`, which is what
# the signature covers, so the stake intent is authenticated: nobody can turn a
# signed transfer into a stake by editing it in flight.
#
# Amount always comes from the transaction's `value`, never from this blob.
STAKE_DATA_MAGIC: str = "anm"
STAKE_DATA_VERSION: int = 1


def encode_stake_data(kind: int, *, days: int = 0, name: str = "") -> bytes:
    """CBOR blob placed in a stake/unstake transaction's `data` field."""
    obj: Dict[str, Any] = {STAKE_DATA_MAGIC: STAKE_DATA_VERSION, "kind": int(kind)}
    if days:
        obj["days"] = int(days)
    if name:
        obj["name"] = str(name)
    return cbor_dumps(obj)


def decode_stake_data(data: Any) -> Optional[Dict[str, Any]]:
    """
    Return the stake intent from a transaction's `data`, or None when this is
    not a stake transaction. Never raises: arbitrary user bytes land here.
    """
    if not data:
        return None
    if isinstance(data, str):
        try:
            data = bytes.fromhex(data[2:] if data.startswith("0x") else data)
        except ValueError:
            return None
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return None
    try:
        m = cbor_loads(bytes(data))
    except Exception:
        return None
    if not isinstance(m, dict):
        return None
    if int(m.get(STAKE_DATA_MAGIC, 0) or 0) != STAKE_DATA_VERSION:
        return None
    try:
        kind = int(m.get("kind"))
    except (TypeError, ValueError):
        return None
    return {
        "kind": kind,
        "days": int(m.get("days", 0) or 0),
        "name": str(m.get("name", "") or ""),
    }


@dataclass(frozen=True)
class Bond:
    """A single time-locked bond."""

    amount: int
    unlock_at: int  # unix seconds; withdrawable once block timestamp >= this

    def to_obj(self) -> List[int]:
        return [int(self.amount), int(self.unlock_at)]

    @staticmethod
    def from_obj(o: Any) -> "Bond":
        return Bond(amount=int(o[0]), unlock_at=int(o[1]))


@dataclass(frozen=True)
class StakeRecord:
    """All bonds for one staker, plus its display label."""

    bonds: Tuple[Bond, ...] = ()
    name: str = ""

    @property
    def total(self) -> int:
        return sum(int(b.amount) for b in self.bonds)

    def matured(self, now: int) -> int:
        return sum(int(b.amount) for b in self.bonds if int(b.unlock_at) <= int(now))

    def to_cbor(self) -> bytes:
        return cbor_dumps({"b": [b.to_obj() for b in self.bonds], "n": str(self.name)})

    @staticmethod
    def from_cbor(raw: bytes) -> "StakeRecord":
        if not raw:
            return StakeRecord()
        m = cbor_loads(raw)
        bonds = tuple(Bond.from_obj(x) for x in (m.get("b") or []))
        return StakeRecord(bonds=bonds, name=str(m.get("n", "") or ""))


# ---------------------------------------------------------------------------
# state access
# ---------------------------------------------------------------------------


def _storage_get(state: Any, addr: bytes, key: bytes) -> bytes:
    """Read storage through whichever accessor this state object exposes."""
    for name in ("get_storage", "storage"):
        fn = getattr(state, name, None)
        if callable(fn):
            try:
                return bytes(fn(addr, key) or b"")
            except Exception:
                return b""
    return b""


def _storage_set(state: Any, addr: bytes, key: bytes, value: bytes) -> None:
    fn = getattr(state, "set_storage", None)
    if not callable(fn):
        raise RuntimeError("state object exposes no set_storage")
    fn(addr, key, bytes(value))


def read_stake(state: Any, account_key: bytes) -> StakeRecord:
    """Return the bond record for one staker (empty record when unstaked)."""
    return StakeRecord.from_cbor(
        _storage_get(state, STAKE_SYSTEM_ADDR, bytes(account_key))
    )


def write_stake(state: Any, account_key: bytes, rec: StakeRecord) -> None:
    """
    Persist a staker's bond record. A record with no bonds is stored as an empty
    value, which `read_stake` maps back to an empty record — that keeps the
    staker out of `iter_stakers` instead of leaving a zero-weight ghost behind.
    """
    payload = b"" if not rec.bonds else rec.to_cbor()
    _storage_set(state, STAKE_SYSTEM_ADDR, bytes(account_key), payload)


def total_stake(state: Any, account_key: bytes) -> int:
    return read_stake(state, account_key).total


def iter_stakers(state: Any) -> Iterator[Tuple[bytes, StakeRecord]]:
    """
    Yield (account_key, record) for every staker with a non-empty bond set,
    ordered by account key so every node walks the set identically.

    Requires an `iter_storage(addr)` accessor on the state object; callers that
    only have a write adapter should pass the underlying StateDB.
    """
    fn = getattr(state, "iter_storage", None)
    if not callable(fn):
        return
    rows: List[Tuple[bytes, StakeRecord]] = []
    for row in fn(STAKE_SYSTEM_ADDR):
        # StateDB.iter_storage yields (addr, key, value); a plain mapping-style
        # adapter may yield (key, value). Accept both rather than depending on
        # which state object the caller happened to pass.
        if len(row) == 3:
            _addr, key, value = row
        else:
            key, value = row
        key = bytes(key)
        # This namespace also holds the bootstrap flag, whose key is not an
        # account key. Only 32-byte keys are stakers.
        if len(key) != 32:
            continue
        rec = StakeRecord.from_cbor(bytes(value or b""))
        if rec.bonds and rec.total > 0:
            rows.append((key, rec))
    for key, rec in sorted(rows, key=lambda kv: kv[0]):
        yield key, rec


def bootstrap_exhausted(state: Any) -> bool:
    """True once a real bond has existed, permanently disabling the fallback."""
    return bool(_storage_get(state, STAKE_SYSTEM_ADDR, _BOOTSTRAP_EXHAUSTED_KEY))


def mark_bootstrap_exhausted(state: Any) -> None:
    """Latch the bootstrap fallback off. Called on the first real bond."""
    _storage_set(state, STAKE_SYSTEM_ADDR, _BOOTSTRAP_EXHAUSTED_KEY, b"\x01")


def real_stakers(state: Any) -> List[Tuple[bytes, StakeRecord]]:
    """Stakers with a genuine bond at or above MIN_STAKE_NANM (no fallback)."""
    return [(k, r) for k, r in iter_stakers(state) if r.total >= MIN_STAKE_NANM]


def active_stakers(state: Any) -> List[Tuple[bytes, StakeRecord]]:
    """
    The leader set for this state.

    Normally the real bonded stakers. While the bond table has never held a
    bond, it is the single bootstrap validator instead — see "bootstrap
    validator" above for why that is necessary and why it self-extinguishes.
    """
    real = real_stakers(state)
    if real:
        return real
    if bootstrap_exhausted(state):
        return []
    return [
        (
            BOOTSTRAP_STAKER,
            StakeRecord(
                bonds=(Bond(amount=BOOTSTRAP_WEIGHT, unlock_at=0),),
                name="treasury (bootstrap)",
            ),
        )
    ]


def is_bootstrap_leader_set(state: Any) -> bool:
    """True when the leader set is the synthetic bootstrap one, not real stake."""
    return not real_stakers(state) and not bootstrap_exhausted(state)


# ---------------------------------------------------------------------------
# leader selection
# ---------------------------------------------------------------------------


def slot_for_timestamp(timestamp: int, target_block_time_s: float) -> int:
    """Slot index for a wall-clock timestamp."""
    step = max(1, int(target_block_time_s or 1))
    return int(timestamp) // step


def selection_seed(parent_hash: bytes, slot: int) -> bytes:
    """
    Deterministic per-slot randomness.

    Binding to the parent hash means the seed is unknown until the previous
    block exists, so a staker cannot grind future slots; binding to the slot
    means one draw per slot rather than one per attempt.
    """
    return sha3_256(
        b"animica/pos/leader/v1" + bytes(parent_hash) + int(slot).to_bytes(8, "big")
    )


def select_leader(
    stakers: List[Tuple[bytes, StakeRecord]],
    parent_hash: bytes,
    slot: int,
) -> Optional[bytes]:
    """
    Stake-weighted deterministic leader for a slot.

    Walks the (address-sorted) staker set accumulating weight and returns the
    staker whose interval contains the draw — so P(leader) is exactly that
    staker's share of total stake. Returns None when nothing is staked.
    """
    if not stakers:
        return None
    total = sum(int(r.total) for _, r in stakers)
    if total <= 0:
        return None
    draw = int.from_bytes(selection_seed(parent_hash, slot), "big") % total
    cursor = 0
    for key, rec in stakers:
        cursor += int(rec.total)
        if draw < cursor:
            return key
    return stakers[-1][0]  # unreachable while total > 0; defensive


def leader_for_slot(
    state: Any, parent_hash: bytes, slot: int
) -> Optional[bytes]:
    """Convenience: read the staker set from state and pick the slot leader."""
    return select_leader(active_stakers(state), parent_hash, slot)


__all__ = [
    "STAKE_SYSTEM_ADDR",
    "MIN_STAKE_NANM",
    "MAX_BONDS_PER_STAKER",
    "SECONDS_PER_DAY",
    "Bond",
    "StakeRecord",
    "read_stake",
    "write_stake",
    "total_stake",
    "iter_stakers",
    "real_stakers",
    "STAKE_DATA_MAGIC",
    "STAKE_DATA_VERSION",
    "encode_stake_data",
    "decode_stake_data",
    "active_stakers",
    "BOOTSTRAP_STAKER",
    "BOOTSTRAP_WEIGHT",
    "bootstrap_exhausted",
    "mark_bootstrap_exhausted",
    "is_bootstrap_leader_set",
    "slot_for_timestamp",
    "selection_seed",
    "select_leader",
    "leader_for_slot",
]
