from __future__ import annotations

from types import SimpleNamespace

from rpc import mempool_service


def test_current_height_prefers_canonical_height_when_primary_height_is_zero(
    monkeypatch,
) -> None:
    class _Ctx:
        def get_head(self):
            return {"height": 0, "canonicalHeight": 128}

    monkeypatch.setattr(mempool_service.deps, "get_ctx", lambda: _Ctx())
    assert mempool_service._current_height() == 128


def test_current_height_reads_height_from_header_object(monkeypatch) -> None:
    header = SimpleNamespace(number=64)

    class _Ctx:
        def get_head(self):
            return {"height": None, "header": header}

    monkeypatch.setattr(mempool_service.deps, "get_ctx", lambda: _Ctx())
    assert mempool_service._current_height() == 64


def test_current_height_falls_back_to_block_db_head_tuple(monkeypatch) -> None:
    class _BlockDb:
        def get_head(self):
            return (33, b"\x00" * 32)

    class _Ctx:
        block_db = _BlockDb()

        def get_head(self):
            return {"height": None, "hash": None, "header": None}

    monkeypatch.setattr(mempool_service.deps, "get_ctx", lambda: _Ctx())
    assert mempool_service._current_height() == 33
