"""
rpc.methods.stake — read-only views over the PoS bond table.

Backs the explorer, the pool dashboard and the public site. Everything here is
derived from committed state (``core.staking``), so a caller sees exactly what
consensus sees. Writes happen only through TxKind.STAKE / TxKind.UNSTAKE — there
is deliberately no RPC that mutates stake.
"""

from __future__ import annotations

import logging
import typing as t

from rpc import deps
from rpc.methods import method

log = logging.getLogger("animica.rpc.stake")

# Address rendering: an account key is sha3_256(pubkey) with no algorithm id, so
# the bech32 form has to assume one. The PoS lane accepts only ML-DSA-65, whose
# address alg id is 0x1003, which makes that the correct assumption for every
# address that can actually mint.
_ML_DSA_65_ALG_ID = 0x1003


def _state():
    ctx = deps.get_ctx()
    return getattr(ctx, "state_db", None)


def _address_of(account_key: bytes) -> str:
    """Best-effort bech32m rendering of a 32-byte account key."""
    try:
        from core.utils.bytes import bech32m_encode

        payload = int(_ML_DSA_65_ALG_ID).to_bytes(2, "big") + bytes(account_key)
        return bech32m_encode("anim", payload)
    except Exception:  # pragma: no cover - rendering must never break a read
        return ""


def _record_view(account_key: bytes, rec: t.Any, now: int) -> dict:
    bonds = [
        {"amount": str(int(b.amount)), "unlockAt": int(b.unlock_at)}
        for b in rec.bonds
    ]
    return {
        "address": _address_of(account_key),
        "accountKey": "0x" + bytes(account_key).hex(),
        "name": rec.name or "",
        "staked": str(int(rec.total)),
        "matured": str(int(rec.matured(now))),
        "locked": str(int(rec.total) - int(rec.matured(now))),
        "bondCount": len(bonds),
        "bonds": bonds,
    }


def _now() -> int:
    import time

    return int(time.time())


@method(
    "stake.summary",
    desc="Totals for the PoS bond table: staked supply, staker count, fork state.",
    aliases=("stake_summary",),
)
def stake_summary() -> dict:
    from core.network_params import (
        FORK_POS_MINTING,
        get_activation_height,
        is_fork_active,
    )
    from core.staking import (
        MIN_STAKE_NANM,
        STAKE_SYSTEM_ADDR,
        bootstrap_exhausted,
        is_bootstrap_leader_set,
        real_stakers,
    )

    ctx = deps.get_ctx()
    state = _state()
    if state is None:
        return {"available": False, "reason": "state unavailable"}

    height = 0
    try:
        head = ctx.block_db.get_canonical_head()
        height = int(head[0]) if head else 0
    except Exception:
        height = 0

    stakers = real_stakers(state)
    total = sum(int(r.total) for _, r in stakers)
    chain_id = 1
    try:
        chain_id = int(getattr(ctx.cfg, "chain_id", 1) or 1)
    except Exception:
        pass

    return {
        "available": True,
        "height": height,
        "forkHeight": get_activation_height(FORK_POS_MINTING, chain_id=chain_id),
        "posActive": bool(is_fork_active(FORK_POS_MINTING, height, chain_id=chain_id)),
        "consensus": "hybrid-pow-pos",
        "stakerCount": len(stakers),
        "totalStaked": str(total),
        "minStake": str(int(MIN_STAKE_NANM)),
        "pooledBalance": str(int(state.get_balance(STAKE_SYSTEM_ADDR) or 0)),
        "stakeAddress": "0x" + STAKE_SYSTEM_ADDR.hex(),
        "bootstrapActive": bool(is_bootstrap_leader_set(state)),
        "bootstrapExhausted": bool(bootstrap_exhausted(state)),
    }


@method(
    "stake.list",
    desc="Every staker with a live bond: name, amount staked, bond schedule.",
    aliases=("stake_list",),
)
def stake_list(limit: int | None = None, offset: int | None = None) -> dict:
    from core.staking import real_stakers

    state = _state()
    if state is None:
        return {"available": False, "stakers": [], "reason": "state unavailable"}

    now = _now()
    rows = real_stakers(state)
    rows.sort(key=lambda kv: int(kv[1].total), reverse=True)
    total_count = len(rows)

    off = max(0, int(offset or 0))
    lim = int(limit) if limit else 100
    lim = max(1, min(lim, 1000))
    page = rows[off : off + lim]

    return {
        "available": True,
        "count": total_count,
        "offset": off,
        "limit": lim,
        "totalStaked": str(sum(int(r.total) for _, r in rows)),
        "stakers": [_record_view(k, r, now) for k, r in page],
    }


@method(
    "stake.get",
    desc="The bond record for one address (empty record when not staking).",
    aliases=("stake_get",),
)
def stake_get(address: str | None = None, **kwargs: t.Any) -> dict:
    from core.staking import read_stake
    from core.utils.address_codec import account_key_from_any

    addr = address or kwargs.get("addr") or kwargs.get("account")
    if not addr:
        return {"available": False, "reason": "address required"}

    state = _state()
    if state is None:
        return {"available": False, "reason": "state unavailable"}

    try:
        key = account_key_from_any(addr)
    except Exception as exc:
        return {"available": False, "reason": f"bad address: {exc}"}

    rec = read_stake(state, key)
    view = _record_view(key, rec, _now())
    view["available"] = True
    view["staking"] = bool(rec.bonds)
    return view


@method(
    "stake.leaderForSlot",
    desc="The stake-weighted PoS leader for a slot on top of the current head.",
    aliases=("stake_leaderForSlot",),
)
def stake_leader_for_slot(slot: int | None = None, **kwargs: t.Any) -> dict:
    """
    Who may mint a PoS block for `slot` (default: the current wall-clock slot)
    as the child of the current canonical head.

    Lets a minter skip slots it does not lead instead of signing and submitting
    a block the importer is certain to reject. Computed exactly as the importer
    does for a head-extending block: committed state at the head, the head hash
    as the parent, and the slot.
    """
    from core.staking import leader_for_slot, slot_for_timestamp

    state = _state()
    if state is None:
        return {"available": False, "reason": "state unavailable"}

    ctx = deps.get_ctx()
    block_db = getattr(ctx, "block_db", None)
    head = block_db.get_canonical_head() if block_db is not None else None
    if not head:
        return {"available": False, "reason": "no canonical head"}
    head_height, head_hash = int(head[0]), bytes(head[1])

    try:
        target_s = float(getattr(getattr(ctx, "params", None), "block", None).target_seconds)
    except Exception:
        target_s = 60.0
    if slot is None:
        slot = kwargs.get("slot")
    slot = int(slot) if slot is not None else slot_for_timestamp(_now(), target_s)

    leader = leader_for_slot(state, head_hash, slot)
    return {
        "available": True,
        "parentHeight": head_height,
        "parentHash": "0x" + head_hash.hex(),
        "slot": slot,
        "leader": ("0x" + bytes(leader).hex()) if leader is not None else None,
        "leaderAddress": _address_of(leader) if leader is not None else "",
    }
