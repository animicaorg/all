# -*- coding: utf-8 -*-
"""
AI agent flow (mocked host): enqueue → result only available next block → consume.

We compile a tiny inline "AI agent" contract that uses stdlib.syscalls
(ai_enqueue/read_result). The test monkeypatches those syscalls with a deterministic
mock that enforces "next block" availability: a result can only be read once the
mock's block height has advanced by at least one tick after enqueue.

Assumptions (match vm_py stdlib stubs from this repo):
- ai_enqueue(model: bytes|str, prompt: bytes) -> bytes task_id
- read_result(task_id: bytes) -> Optional[bytes]
- The LocalContract harness from tests/conftest.py exposes:
    c = compile_contract(path)
    c.call("fn", *args) -> return value
    c.events -> list of {"name": bytes, "args": dict}
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Optional, Tuple

import pytest

# ---------------------------- inline AI agent contract -------------------------

CONTRACT_SOURCE = r"""
from stdlib.storage import get, set
from stdlib.events import emit
from stdlib.abi import require
from stdlib.syscalls import ai_enqueue, read_result

K_LAST = b"last_tid"
K_RES  = b"last_result"

def init() -> None:
    set(K_LAST, b"")
    set(K_RES, b"")

def last_task() -> bytes:
    v = get(K_LAST)
    return v if v is not None else b""

def last_result() -> bytes:
    v = get(K_RES)
    return v if v is not None else b""

def request(prompt: bytes) -> bytes:
    # model id is illustrative; the mock ignores it but we exercise the API surface
    tid = ai_enqueue(b"tiny-llm", prompt)
    set(K_LAST, tid)
    emit(b"AIEnqueued", {"task": tid})
    return tid

def consume() -> bytes:
    tid = get(K_LAST)
    require(tid is not None and len(tid) > 0, "no task")
    res = read_result(tid)
    if res is None:
        # contract-visible "no result yet" sentinel; caller can retry next block
        return b""
    set(K_RES, res)
    emit(b"AIResult", {"task": tid, "size": len(res)})
    return res
"""


def _write_contract(tmp_path: Path) -> Path:
    p = tmp_path / "ai_agent_inline.py"
    p.write_text(CONTRACT_SOURCE, encoding="utf-8")
    return p


# ------------------------------- mocked provider -------------------------------


class MockAIHost:
    """
    Deterministic AI host with "next-block" availability.

    - ai_enqueue(model, prompt) -> task_id = b"TASK-" + sha3_256(prompt)[:8]
    - read_result(task_id) -> None until self.height >= available_at[task_id]
                            -> b"MOCK:" + prompt thereafter
    """

    def __init__(self) -> None:
        self.height: int = 0
        # task_id -> (prompt, available_at_height)
        self._tasks: Dict[bytes, Tuple[bytes, int]] = {}

    # Signature mirrors stdlib.syscalls.ai_enqueue
    def ai_enqueue(self, model, prompt: bytes) -> bytes:
        if isinstance(model, bytes):
            _ = model  # ignored, but ensures bytes are accepted
        elif isinstance(model, str):
            _ = model.encode("utf-8")
        else:  # pragma: no cover - defensive
            raise TypeError("model must be bytes or str")

        tid = b"TASK-" + hashlib.sha3_256(prompt).digest()[:8]
        self._tasks[tid] = (prompt, self.height + 1)
        return tid

    # Signature mirrors stdlib.syscalls.read_result
    def read_result(self, task_id: bytes) -> Optional[bytes]:
        entry = self._tasks.get(task_id)
        if not entry:
            return None
        prompt, available_at = entry
        if self.height < available_at:
            return None
        return b"MOCK:" + prompt

    def advance_block(self, n: int = 1) -> None:
        self.height += int(n)


# ------------------------------------ tests ------------------------------------


@pytest.fixture()
def mock_ai(monkeypatch) -> MockAIHost:
    """
    Patch vm_py stdlib syscalls with the MockAIHost methods.
    We import the concrete module path used by the VM stdlib and replace callables.
    """
    host = MockAIHost()
    # Import where the VM exposes its stdlib; patch in-place so contract sees it.
    import vm_py.stdlib.syscalls as sc  # type: ignore

    monkeypatch.setattr(sc, "ai_enqueue", host.ai_enqueue, raising=True)
    monkeypatch.setattr(sc, "read_result", host.read_result, raising=True)
    return host


def test_ai_agent_enqueue_and_consume_next_block(
    tmp_path: Path, compile_contract, mock_ai: MockAIHost
):
    """
    End-to-end within the local VM:
      - init
      - request(prompt) -> emits AIEnqueued + returns task_id
      - consume() before next block -> b"" (no result yet)
      - advance block on host; consume() -> b"MOCK:" + prompt; emits AIResult
    """
    src = _write_contract(tmp_path)
    c = compile_contract(src)

    # init
    c.call("init")
    assert c.call("last_task") == b""
    assert c.call("last_result") == b""

    # enqueue
    prompt = b"what is love?"
    tid = c.call("request", prompt)
    assert isinstance(tid, (bytes, bytearray)) and tid.startswith(b"TASK-")
    assert c.call("last_task").startswith(b"TASK-")

    # not available yet
    res0 = c.call("consume")
    assert res0 == b""
    # Only AIEnqueued should be present so far
    assert [e["name"] for e in c.events] == [b"AIEnqueued"]

    # simulate next block; now result should be readable
    mock_ai.advance_block()
    res1 = c.call("consume")
    assert res1 == b"MOCK:" + prompt
    assert c.call("last_result") == res1

    # Event sequence & basic args sanity
    names = [e["name"] for e in c.events]
    assert names == [b"AIEnqueued", b"AIResult"]
    # Ensure second event carries a size field that matches the mock payload
    last_args = c.events[-1]["args"]
    assert int(last_args["size"]) == len(res1)


def test_multiple_enqueues_and_height_rules(
    tmp_path: Path, compile_contract, mock_ai: MockAIHost
):
    """
    Two enqueues across different heights:
      - T1 at h=0 (available >= 1), T2 at h=0 (available >= 1)
      - consume at h=0 → none
      - advance to h=1 → both available; consume returns payload
    """
    src = _write_contract(tmp_path)
    c = compile_contract(src)

    c.call("init")
    t1 = c.call("request", b"alpha")
    t2 = c.call("request", b"beta")
    assert t1 != t2

    # Neither available yet
    assert c.call("consume") == b""

    # Move height; now readable
    mock_ai.advance_block()
    res = c.call("consume")
    assert res in (b"MOCK:alpha", b"MOCK:beta")  # whichever was last_task
    assert [e["name"] for e in c.events].count(b"AIEnqueued") == 2
    assert [e["name"] for e in c.events].count(b"AIResult") == 1
