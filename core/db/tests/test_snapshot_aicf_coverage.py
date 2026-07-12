"""snapshot()/revert() must restore PFX_AICF pool/epoch state, not just accounts.

Without this a reorg reverts balances but leaves stale AICF bookkeeping — a
silent emission/balance divergence (AICF moves value EOA -> pool -> miner EOAs).
This is also a prerequisite for cleanly rejecting a block on a committed-root
mismatch in the forthcoming state-commitment fork.
"""
from __future__ import annotations

from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB

A = bytes.fromhex("aa" * 32)
B = bytes.fromhex("bb" * 32)


def _mk():
    return StateDB(SQLiteKV(":memory:"))


def test_aicf_state_round_trips_through_snapshot_revert():
    st = _mk()
    st.set_balance(A, 1000)
    st.put("aicf.epoch.5.credits_total", 777)
    st.put("aicf.pool.balance", 12345)

    snap = st.snapshot()

    # Mutate BOTH accounts and AICF after the snapshot.
    st.set_balance(A, 1)
    st.set_balance(B, 999)
    st.put("aicf.epoch.5.credits_total", 0)
    st.put("aicf.pool.balance", 0)
    st.put("aicf.epoch.6.credits_total", 42)  # a key that did not exist at snapshot

    st.revert(snap)

    # Accounts restored (existing behavior).
    assert st.get_balance(A) == 1000
    assert st.get_balance(B) == 0
    # AICF values restored...
    assert st.get("aicf.epoch.5.credits_total") == 777
    assert st.get("aicf.pool.balance") == 12345
    # ...and keys created after the snapshot are wiped, not left behind.
    assert st.get("aicf.epoch.6.credits_total") in (None, 0)


def test_snapshot_with_no_aicf_state_is_fine():
    st = _mk()
    st.set_balance(A, 500)
    snap = st.snapshot()
    st.put("aicf.pool.balance", 5)
    st.revert(snap)
    assert st.get("aicf.pool.balance") in (None, 0)
    assert st.get_balance(A) == 500


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
