"""Regression tests for PeerStore robustness when reading persisted rows."""

from __future__ import annotations

import sqlite3
import time

from p2p.peer import peerstore
from p2p.peer.peer import PeerStatus


def test_row_to_peer_tolerates_corrupt_snapshot(tmp_path) -> None:
    store = peerstore.PeerStore(tmp_path)
    now = time.time()

    with store._locked_conn() as conn:  # type: ignore[attr-defined]
        conn.execute(
            "INSERT INTO peers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "peer_bad",
                "/ip4/1.1.1.1/tcp/30333",
                1,
                1,
                sqlite3.Binary(b"\x00" * 32),
                42,
                "not-json",  # caps
                "bogus",  # status
                now,
                now,
                None,
                None,
                None,
                0.0,
                "{not json",  # snapshot
                "outbound",
            ),
        )
        row = conn.execute("SELECT * FROM peers").fetchone()

    peer = store._row_to_peer(row)  # type: ignore[attr-defined]

    assert peer.peer_id == "peer_bad"
    assert peer.caps == set()
    assert peer.status == PeerStatus.DISCONNECTED
    assert peer.chain_id == 1
