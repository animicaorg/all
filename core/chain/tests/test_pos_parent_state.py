"""
11.3.0 regression: a PoS block's leader check must be judged against the state
as of its PARENT, never against whatever head the importing node is on.

Before 11.3.0 the importer ran the whole PoS check at header time against its
own head state. For a block on a competing branch that is the wrong state: a
stake/unstake tx on the node's tip changed the stake-weighted draw, the node
rejected the network's real block, every descendant stayed orphaned, and the
node sat on its own fork forever ("ran 15 minutes, then forked").
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Dict, Tuple

import pytest

from core import pos
from core.pos import (
    POS_ALLOWED_SCHEMES,
    WORKTYPE_POS,
    build_pos_extra,
    verify_pos_header,
    verify_pos_header_stateless,
    verify_pos_leader,
)
from core.staking import (
    Bond,
    StakeRecord,
    leader_for_slot,
    mark_bootstrap_exhausted,
    slot_for_timestamp,
    write_stake,
)
from core.types.header import Header
from core.utils.address_codec import account_key_from_pubkey

mldsa = pytest.importorskip("pq.py.algs.ml_dsa_65")

TARGET_S = 60.0
SCHEME = next(iter(POS_ALLOWED_SCHEMES))


class DictState:
    """Minimal storage-only state: exactly the accessors core.staking uses."""

    def __init__(self) -> None:
        self._sto: Dict[Tuple[bytes, bytes], bytes] = {}

    def get_storage(self, addr: bytes, key: bytes) -> bytes:
        return self._sto.get((bytes(addr), bytes(key)), b"")

    def set_storage(self, addr: bytes, key: bytes, value: bytes) -> None:
        self._sto[(bytes(addr), bytes(key))] = bytes(value)

    def iter_storage(self, addr: bytes):
        for (a, k), v in sorted(self._sto.items()):
            if a == bytes(addr) and v:
                yield a, k, v


def _validator(seed: int):
    sk, pk = mldsa.keypair(bytes([seed]) * 32)  # returns (sk, pk)
    return SimpleNamespace(pk=pk, sk=sk, key=account_key_from_pubkey(pk, None))


def _state(stakes: Dict[bytes, int]) -> DictState:
    st = DictState()
    mark_bootstrap_exhausted(st)
    for key, amount in stakes.items():
        write_stake(st, key, StakeRecord(bonds=(Bond(amount=amount, unlock_at=0),)))
    return st


def _header(parent: bytes, timestamp: int) -> Header:
    return Header(
        v=1,
        chainId=1,
        height=110_100,
        parentHash=parent,
        timestamp=timestamp,
        stateRoot=b"\x00" * 32,
        txsRoot=b"\x00" * 32,
        receiptsRoot=b"\x00" * 32,
        proofsRoot=b"\x00" * 32,
        daRoot=b"\x00" * 32,
        mixSeed=b"\x00" * 32,
        poiesPolicyRoot=b"\x00" * 32,
        pqAlgPolicyRoot=b"\x00" * 32,
        thetaMicro=1_000_000,
        nonce=0,
        extra=b"",
        workType=WORKTYPE_POS,
    )


def _signed(v, parent: bytes, timestamp: int) -> Header:
    h = _header(parent, timestamp)
    extra = build_pos_extra(
        h,
        staker=v.key,
        slot=slot_for_timestamp(timestamp, TARGET_S),
        scheme=SCHEME,
        pubkey=v.pk,
        sign=lambda m: mldsa.sign(v.sk, m),
    )
    return replace(h, extra=extra)


def _split_scenario():
    """
    Two stake tables that differ only by one extra bond (what a stake tx on a
    node's own tip does), plus a slot whose leader differs between them.
    """
    a, b = _validator(1), _validator(2)
    parent_state = _state({a.key: 10 * 10**9, b.key: 10 * 10**9})
    tip_state = _state({a.key: 10 * 10**9, b.key: 30 * 10**9})
    parent = b"\x11" * 32
    for ts in range(1_790_000_000, 1_790_000_000 + 600 * 60, 60):
        slot = slot_for_timestamp(ts, TARGET_S)
        if (
            leader_for_slot(parent_state, parent, slot) == a.key
            and leader_for_slot(tip_state, parent, slot) == b.key
        ):
            return a, parent_state, tip_state, parent, ts
    pytest.fail("no slot whose leader differs between the two stake tables")


def test_leader_verdict_depends_on_the_state_it_is_given():
    a, parent_state, tip_state, parent, ts = _split_scenario()
    hdr = _signed(a, parent, ts)
    # Valid against the parent's stake table ...
    assert verify_pos_header(hdr, parent_state, target_block_time_s=TARGET_S) is None
    # ... and "not the leader" against a tip that saw one more stake tx. This
    # is the verdict the old importer reached for a side-branch block.
    assert verify_pos_leader(hdr, tip_state) == "pos staker is not the leader for this slot"


def test_stateless_half_needs_no_state_and_still_rejects_forgeries():
    a, _parent_state, _tip_state, parent, ts = _split_scenario()
    hdr = _signed(a, parent, ts)
    assert verify_pos_header_stateless(hdr, target_block_time_s=TARGET_S) is None

    # Tampering with any signed field breaks the signature.
    forged = replace(hdr, thetaMicro=hdr.thetaMicro + 1)
    reason = verify_pos_header_stateless(forged, target_block_time_s=TARGET_S)
    assert reason is not None and reason.startswith("pos signature invalid")

    # A timestamp outside the claimed slot is rejected without any state.
    moved = _signed(a, parent, ts)
    moved = replace(moved, timestamp=ts + 10 * int(TARGET_S))
    reason = verify_pos_header_stateless(moved, target_block_time_s=TARGET_S)
    assert reason is not None


# ---------------------------------------------------------------------------
# importer routing
# ---------------------------------------------------------------------------


def _importer_stub(head_hash: bytes, state):
    from core.chain.block_import import BlockImporter

    imp = BlockImporter.__new__(BlockImporter)
    imp.state_db = state
    imp.params = SimpleNamespace(chain_id=1, block=SimpleNamespace(target_seconds=TARGET_S))
    imp.block_db = SimpleNamespace(get_canonical_head=lambda: (110_099, head_hash))
    imp._invalid_blocks = set()
    imp.fork_choice = None
    return imp


def test_side_branch_pos_block_is_not_judged_against_our_tip(monkeypatch):
    """The exact split: our tip is a sibling that carried a stake tx."""
    import core.network_params as np_mod

    monkeypatch.setattr(np_mod, "is_fork_active", lambda *a, **k: True)
    a, parent_state, tip_state, parent, ts = _split_scenario()
    hdr = _signed(a, parent, ts)

    our_tip = b"\x22" * 32  # a sibling of `parent`'s child, NOT `parent`
    imp = _importer_stub(our_tip, tip_state)
    assert imp._pow_sanity(header=hdr, header_hash=b"\x33" * 32, payload={}) is None


def test_head_extending_pos_block_is_still_rejected_early(monkeypatch):
    import core.network_params as np_mod

    monkeypatch.setattr(np_mod, "is_fork_active", lambda *a, **k: True)
    a, _parent_state, tip_state, parent, ts = _split_scenario()
    hdr = _signed(a, parent, ts)

    # Our head IS the parent and its state says `a` does not lead: reject now.
    imp = _importer_stub(parent, tip_state)
    reason = imp._pow_sanity(header=hdr, header_hash=b"\x33" * 32, payload={})
    assert reason == "pos proof invalid: pos staker is not the leader for this slot"


def test_attach_time_check_uses_the_state_it_is_applied_on(monkeypatch):
    import core.network_params as np_mod

    monkeypatch.setattr(np_mod, "is_fork_active", lambda *a, **k: True)
    a, parent_state, tip_state, parent, ts = _split_scenario()
    hdr = _signed(a, parent, ts)
    block = SimpleNamespace(header=hdr)

    # Applied on the parent's state: accepted.
    assert _importer_stub(parent, parent_state)._pos_leader_reject_reason(block) is None

    # Applied on a state where `a` does not lead: rejected and remembered.
    imp = _importer_stub(parent, tip_state)
    assert imp._apply_block_state(block) is False
    assert hdr.hash() in imp._invalid_blocks


# ---------------------------------------------------------------------------
# activation gating (FORK_POS_PARENT_STATE_LEADER)
# ---------------------------------------------------------------------------


def _only_pos_minting_active(monkeypatch):
    """PoS minting active, the parent-state rule NOT yet active (below H)."""
    import core.network_params as np_mod

    monkeypatch.setattr(
        np_mod,
        "is_fork_active",
        lambda name, *a, **k: name != np_mod.FORK_POS_PARENT_STATE_LEADER,
    )


def test_below_activation_side_branch_keeps_the_old_verdict(monkeypatch):
    """Below H nothing is re-judged: the 11.2.3 head-state check still applies."""
    _only_pos_minting_active(monkeypatch)
    a, _parent_state, tip_state, parent, ts = _split_scenario()
    hdr = _signed(a, parent, ts)
    imp = _importer_stub(b"\x22" * 32, tip_state)
    reason = imp._pow_sanity(header=hdr, header_hash=b"\x33" * 32, payload={})
    assert reason == "pos proof invalid: pos staker is not the leader for this slot"


def test_below_activation_attach_never_rejudges_history(monkeypatch):
    """A state rebuild over pre-H blocks must not reject them (mainnet history
    below the 114,276 checkpoint does not replay under the leader rule)."""
    _only_pos_minting_active(monkeypatch)
    a, _parent_state, tip_state, parent, ts = _split_scenario()
    block = SimpleNamespace(header=_signed(a, parent, ts))
    assert _importer_stub(parent, tip_state)._pos_leader_reject_reason(block) is None


def test_mainnet_activation_is_right_after_the_bootstrap_checkpoint():
    from core.network_params import (
        ACTIVATION_HEIGHTS_BY_NETWORK,
        FORK_POS_PARENT_STATE_LEADER,
        PINNED_CHECKPOINTS_BY_NETWORK,
    )

    h = ACTIVATION_HEIGHTS_BY_NETWORK[("mainnet", 1)][FORK_POS_PARENT_STATE_LEADER]
    assert h - 1 in PINNED_CHECKPOINTS_BY_NETWORK[("mainnet", 1)]
