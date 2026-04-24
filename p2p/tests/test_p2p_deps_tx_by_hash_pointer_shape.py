from __future__ import annotations

from types import SimpleNamespace

import p2p.deps as deps_mod
from core.db.tx_index import TxPointer


class _TxIndexStub:
    def __init__(self, loc):
        self._loc = loc

    def get(self, _tx_hash: bytes):
        return self._loc


def test_p2p_deps_tx_by_hash_accepts_txpointer_location(monkeypatch):
    expected = object()
    dep = object.__new__(deps_mod.P2PDeps)
    dep._tx_index = _TxIndexStub(TxPointer(height=7, index=1, block_hash=b"\x11" * 32))
    monkeypatch.setattr(
        deps_mod.P2PDeps,
        "block_by_number",
        lambda _self, height: (
            SimpleNamespace(txs=[object(), expected]) if height == 7 else None
        ),
    )

    out = deps_mod.P2PDeps.tx_by_hash(dep, b"\x22" * 32)

    assert out is expected


def test_p2p_deps_tx_by_hash_still_accepts_tuple_location(monkeypatch):
    expected = object()
    dep = object.__new__(deps_mod.P2PDeps)
    dep._tx_index = _TxIndexStub((9, 0))
    monkeypatch.setattr(
        deps_mod.P2PDeps,
        "block_by_number",
        lambda _self, height: (
            SimpleNamespace(txs=[expected, object()]) if height == 9 else None
        ),
    )

    out = deps_mod.P2PDeps.tx_by_hash(dep, b"\x33" * 32)

    assert out is expected


def test_p2p_deps_tx_by_hash_ignores_unreadable_location(monkeypatch):
    dep = object.__new__(deps_mod.P2PDeps)
    dep._tx_index = _TxIndexStub(object())
    monkeypatch.setattr(
        deps_mod.P2PDeps,
        "block_by_number",
        lambda _self, _height: SimpleNamespace(txs=[object()]),
    )

    out = deps_mod.P2PDeps.tx_by_hash(dep, b"\x44" * 32)

    assert out is None
