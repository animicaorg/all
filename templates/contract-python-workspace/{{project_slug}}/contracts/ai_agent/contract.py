# -*- coding: utf-8 -*-
"""
AI Agent (template)
-------------------

A minimal, deterministic contract that demonstrates how to enqueue an AI job
through the Animica capabilities surface and consume the result in a later block.

State (keys -> bytes):
- 0x00: owner (address-encoded bytes)
- 0x01: model identifier (bytes)
- 0x02: last task id (bytes)
- 0x03: last prompt (bytes)
- 0x04: last result (bytes)  # opaque; producer-defined payload

Events:
- b"AIRequested" { "task_id": bytes, "model": bytes, "prompt": bytes }
- b"AIResult"    { "task_id": bytes, "size": uint, "ok": bool }

Determinism notes:
- The enqueue syscall returns a deterministic task_id derived from chain/height/tx hash, etc.
- The result is available in/after the **next block** once a valid proof is included on-chain.
- If no result is ready yet, `consume` returns (False) without mutating result state.
"""

from stdlib import storage, events, abi
from stdlib import syscalls  # exposes ai_enqueue(...) and read_result(...)

# ---- storage keys -----------------------------------------------------------

K_OWNER = b"\x00"
K_MODEL = b"\x01"
K_LAST_TASK = b"\x02"
K_LAST_PROMPT = b"\x03"
K_LAST_RESULT = b"\x04"


# ---- helpers ----------------------------------------------------------------

def _get_owner() -> bytes:
    owner = storage.get(K_OWNER)
    return owner if owner is not None else b""


def _only_owner(caller: bytes) -> None:
    abi.require(caller == _get_owner(), b"NOT_OWNER")


# ---- public API --------------------------------------------------------------

def init(owner_addr: bytes, model: bytes) -> None:
    """
    Initialize the contract.
    """
    abi.require(storage.get(K_OWNER) is None, b"ALREADY_INIT")
    abi.require(len(owner_addr) > 0, b"OWNER_EMPTY")
    storage.set(K_OWNER, owner_addr)
    storage.set(K_MODEL, model)


def set_model(caller: bytes, model: bytes) -> None:
    """
    Update the model identifier (only owner).
    """
    _only_owner(caller)
    storage.set(K_MODEL, model)


def request(prompt: bytes) -> bytes:
    """
    Enqueue an AI job with the configured model and given prompt.
    Returns the deterministic task_id (bytes).
    """
    model = storage.get(K_MODEL) or b""
    abi.require(len(model) > 0, b"MODEL_NOT_SET")
    abi.require(len(prompt) > 0, b"PROMPT_EMPTY")

    # Ask the host to enqueue the job; returns deterministic task_id (bytes).
    task_id = syscalls.ai_enqueue(model, prompt)

    # Persist a tiny trace for UX and debugging.
    storage.set(K_LAST_TASK, task_id)
    storage.set(K_LAST_PROMPT, prompt)

    # Emit an event for off-chain UIs/indexers.
    events.emit(b"AIRequested", {b"task_id": task_id, b"model": model, b"prompt": prompt})
    return task_id


def consume(task_id: bytes) -> bool:
    """
    Try to read the result for an existing task. If present, stores it and emits AIResult.
    Returns True iff a new result was consumed and stored; False otherwise.
    """
    abi.require(len(task_id) > 0, b"BAD_TASK")

    # read_result returns b"" (or None) when not ready yet.
    result = syscalls.read_result(task_id)
    if result is None or len(result) == 0:
        # No state change if not ready; caller can retry next block.
        return False

    storage.set(K_LAST_RESULT, result)
    events.emit(b"AIResult", {b"task_id": task_id, b"size": len(result), b"ok": True})
    return True


# ---- views ------------------------------------------------------------------

def owner() -> bytes:
    return _get_owner()


def model() -> bytes:
    return storage.get(K_MODEL) or b""


def last_task() -> bytes:
    return storage.get(K_LAST_TASK) or b""


def last_prompt() -> bytes:
    return storage.get(K_LAST_PROMPT) or b""


def last_result() -> bytes:
    return storage.get(K_LAST_RESULT) or b""
