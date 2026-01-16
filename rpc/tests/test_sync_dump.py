from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import pytest

from rpc.methods import sync as sync_methods


@dataclass
class DummyCustom:
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name}


def test_to_jsonable_serialization() -> None:
    now = datetime(2024, 1, 1, 0, 0, 0)
    payload = {
        "when": now,
        "raw": b"abc",
        "buffer": bytearray(b"def"),
        "items": {1, 2, 3},
        "tuple": (1, 2),
        "custom": DummyCustom("ok"),
    }
    jsonable = sync_methods._to_jsonable(payload)
    assert jsonable["when"] == now.isoformat()
    assert jsonable["raw"] == "616263"
    assert jsonable["buffer"] == "646566"
    assert sorted(jsonable["items"]) == [1, 2, 3]
    assert jsonable["tuple"] == [1, 2]
    assert jsonable["custom"] == {"name": "ok"}
    json.dumps(jsonable)


class DummyPeer:
    def __init__(self, remote: str) -> None:
        self.remote = remote
        self.peer_id = f"peer-{remote}"
        self.direction = "outbound"
        self.hello_done = threading.Event()
        self.hello_done.set()
        self.ready_for_sync = True
        self.hello = {"head_height": 12, "head_hash": b"\x12\x34"}
        self.last_msg_at = time.time()
        self.last_progress_at = time.time()


class DummyService:
    def __init__(self) -> None:
        self._sync_lock = asyncio.Lock()
        self._peer_lock = asyncio.Lock()
        self._sync_phase = "SYNCING"
        self._sync_best_header = None
        self._sync_target_height = 25
        self._sync_last_progress_at = time.time()
        self._sync_last_header_error = None
        self._sync_last_block_error = None
        self._sync_last_block_error_peer = None
        self._sync_recovery_attempts = 1
        self._sync_last_recovery_action = "retry_blocks"
        self._sync_last_checkpoint_action = None
        self._sync_header_queue = deque()
        self._sync_block_queue = deque()
        self._sync_block_buffer: dict[bytes, dict[str, object]] = {}
        self._sync_inflight_headers = 0
        self._sync_inflight_blocks: dict[bytes, float] = {}
        self._sync_retries_by_peer = {"peer-a": 1}
        self._sync_timeouts_by_peer = {"peer-a": 2}
        self._peers = {"peer-a": DummyPeer("peer-a")}
        self._sync_status_cache_at = time.time()
        self._sync_status_cache_hits = 0
        self._sync_status_cache_refreshes = 0
        self._sync_status_cache_interval = 1.0

    def _network_best_height(self) -> int:
        return 30

    def _inflight_block_samples(self, *, limit: int = 10) -> list[dict[str, object]]:
        return [
            {
                "hash": "aa",
                "parent_hash": "bb",
                "requested_at": time.time(),
                "from_peer": "peer-a",
            }
        ][:limit]

    def _orphan_block_samples(self, *, limit: int = 10) -> list[dict[str, object]]:
        return [
            {"hash": "cc", "parent_hash": "dd", "age_s": 1.0, "from_peer": "peer-a"}
        ][:limit]

    def _peer_score_snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "remote": "peer-a",
                "score": 1,
                "penalty_score": 0,
                "sync_penalties": 0,
                "last_response_at": time.time(),
            }
        ]


class DummyCtx:
    def get_head(self) -> dict[str, object]:
        return {"height": 10, "hash": "0xabc"}


@pytest.mark.asyncio
async def test_sync_dump_concurrent_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = DummyService()
    monkeypatch.setattr(sync_methods, "_get_p2p_service", lambda: svc)
    monkeypatch.setattr(sync_methods.deps, "get_ctx", lambda: DummyCtx())

    stop = asyncio.Event()

    async def mutate() -> None:
        while not stop.is_set():
            async with svc._sync_lock:
                svc._sync_inflight_blocks[b"\x01"] = time.time()
                svc._sync_block_queue.append(b"\x02")
                svc._sync_block_buffer[b"\x03"] = {"received_at": time.time()}
                svc._sync_last_progress_at = time.time()
            async with svc._peer_lock:
                svc._peers["peer-a"] = DummyPeer("peer-a")
            await asyncio.sleep(0)

    task = asyncio.create_task(mutate())
    try:
        for _ in range(100):
            dump = await sync_methods.sync_dump()
            assert isinstance(dump, dict)
            assert dump.get("timestamp")
            json.dumps(dump)
    finally:
        stop.set()
        await task
