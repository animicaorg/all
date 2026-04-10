"""Regression tests for PeerStore robustness when reading persisted rows."""

from __future__ import annotations

import logging
import sqlite3
import time

import pytest

from p2p.peer import p2p_store
from p2p.peer import peerstore
from p2p.peer.peer import PeerStatus


def test_row_to_peer_tolerates_corrupt_snapshot(tmp_path) -> None:
    store = peerstore.PeerStore(tmp_path)
    now = time.time()

    with store._locked_conn() as conn:  # type: ignore[attr-defined]
        conn.execute(
            "INSERT INTO peers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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


def test_ensure_writable_skips_group_ownership_when_unsupported(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _unexpected_chown(*_args, **_kwargs) -> None:
        raise AssertionError("os.chown should not be called when ownership is unsupported")

    monkeypatch.setattr(p2p_store, "_supports_group_ownership", lambda: False)
    monkeypatch.setattr(
        p2p_store.os,
        "chown",
        _unexpected_chown,
        raising=False,
    )

    writable = p2p_store.ensure_writable(tmp_path / "peerstore")

    assert writable.path == tmp_path / "peerstore"
    assert writable.used_fallback is False


def test_ensure_writable_tolerates_optional_chown_failures(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _raise_permission_error(*_args, **_kwargs) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(p2p_store, "_supports_group_ownership", lambda: True)
    monkeypatch.setattr(p2p_store.os, "getgid", lambda: 1234, raising=False)
    monkeypatch.setattr(
        p2p_store.os,
        "chown",
        _raise_permission_error,
        raising=False,
    )

    with caplog.at_level(logging.DEBUG, logger="animica.p2p.store"):
        writable = p2p_store.ensure_writable(tmp_path / "peerstore")

    assert writable.path == tmp_path / "peerstore"
    assert writable.used_fallback is False
    assert "Skipping optional peerstore group ownership update" in caplog.text
