from __future__ import annotations

from core.utils.hash import sha3_256
from p2p.deps import P2PDeps
from rpc.pending_pool import pool as pending_pool


def test_p2p_admit_tx_accepts_raw_bytes() -> None:
    raw = b"raw-tx-bytes"
    tx_hash_hex = "0x" + sha3_256(raw).hex()

    with pending_pool._lock:  # type: ignore[attr-defined]
        pending_pool._entries.clear()  # type: ignore[attr-defined]

    deps = P2PDeps.__new__(P2PDeps)
    accepted, reason = deps.admit_tx(raw)

    assert accepted is True, f"expected accept, got reason={reason}"
    assert pending_pool.get_raw(tx_hash_hex) == raw
