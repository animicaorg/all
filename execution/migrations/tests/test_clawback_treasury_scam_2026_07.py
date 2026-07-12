"""ANM-2026-07 treasury-scam clawback: value-preserving, height/chain-gated,
deterministic, non-fatal.
"""
from __future__ import annotations

from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from execution.migrations import clawback_treasury_scam_2026_07 as cb
from execution.migrations._recovery_policy import FUTURE_CLAWBACK_RECOVERY_KEY as DEST

SRC1, SRC2, SRC3 = cb._SCAM_SOURCES
H = cb._DEFAULT_MAINNET_HEIGHT  # 44_444
A1 = 4_000_799_836_852_220  # addr1 build-time nano
A2 = 9_248_761_917_558       # addr2 build-time nano
A3 = 1_643_614_048_777_355   # addr3 build-time nano
ATOTAL = A1 + A2 + A3


def _mk(bal1=A1, bal2=A2, bal3=A3, treasury=1_000):
    st = StateDB(SQLiteKV(":memory:"))
    if bal1:
        st.set_balance(SRC1, bal1)
    if bal2:
        st.set_balance(SRC2, bal2)
    if bal3:
        st.set_balance(SRC3, bal3)
    if treasury:
        st.set_balance(DEST, treasury)
    return st


def test_moves_whole_balances_to_treasury_and_preserves_value():
    st = _mk()
    before = sum(st.get_balance(s) for s in (SRC1, SRC2, SRC3)) + st.get_balance(DEST)

    cb.apply_clawback_if_active(st, H, chain_id=1)

    assert st.get_balance(SRC1) == 0
    assert st.get_balance(SRC2) == 0
    assert st.get_balance(SRC3) == 0
    assert st.get_balance(DEST) == 1_000 + ATOTAL
    # Value-preserving: nothing minted or burned.
    after = sum(st.get_balance(s) for s in (SRC1, SRC2, SRC3)) + st.get_balance(DEST)
    assert after == before


def test_noop_off_height_and_off_chain():
    st = _mk()
    cb.apply_clawback_if_active(st, H - 1, chain_id=1)      # wrong height
    cb.apply_clawback_if_active(st, H, chain_id=1337)       # wrong chain
    assert st.get_balance(SRC1) == A1
    assert st.get_balance(SRC2) == A2
    assert st.get_balance(SRC3) == A3
    assert st.get_balance(DEST) == 1_000


def test_partial_empty_source_moves_only_what_is_present():
    st = _mk(bal1=0)  # bulk source already emptied (e.g. spent before H)
    cb.apply_clawback_if_active(st, H, chain_id=1)
    assert st.get_balance(SRC2) == 0
    assert st.get_balance(SRC3) == 0
    assert st.get_balance(DEST) == 1_000 + A2 + A3


def test_all_sources_empty_is_a_clean_noop():
    st = _mk(bal1=0, bal2=0, bal3=0)
    cb.apply_clawback_if_active(st, H, chain_id=1)
    assert st.get_balance(DEST) == 1_000  # no phantom credit


def test_deterministic_repeat_on_fresh_state():
    r = []
    for _ in range(2):
        st = _mk()
        cb.apply_clawback_if_active(st, H, chain_id=1)
        r.append(st.get_balance(DEST))
    assert r[0] == r[1] == 1_000 + ATOTAL


def test_destination_is_the_foundation_treasury():
    # Guard: the scam clawback credits the treasury, never a source or a zero key.
    assert DEST not in cb._SCAM_SOURCES
    assert DEST != b"\x00" * 32
    assert len(DEST) == 32


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
