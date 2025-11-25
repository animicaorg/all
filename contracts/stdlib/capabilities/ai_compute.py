# -*- coding: utf-8 -*-
"""
contracts.stdlib.capabilities.ai_compute
=======================================

High-level, deterministic helpers for **AI compute** from contracts.

This module wraps the low-level capability syscalls exposed via
``contracts.stdlib.capabilities`` and adds a small amount of ergonomic sugar:
- concise enqueue helper (returns a deterministic ``task_id``)
- "tagged" workflows that persist the task id under a bounded key in contract
  storage so it can be consumed on the **next block**
- strict input validation & crisp revert reasons
- consistent events for indexers

Round-trip pattern (within a contract)
--------------------------------------
1) In block N, **enqueue** the job:

    >>> from contracts.stdlib.capabilities import ai_compute as ai
    >>> task_id = ai.request(b"tiny-llm", b"count to three")
    # or persist under a tag for later consumption:
    >>> ai.request_store(b"demo", b"tiny-llm", b"count to three")

   This emits CAP:AIEnqueued (from the lower-level capability wrapper) and,
   for the tag helper, CAP:AITagSaved.

2) In block N+1, **consume** the result:

    >>> status, output = ai.consume(task_id)
    # or, if using a tag:
    >>> status, output = ai.consume_tag(b"demo")

   This emits CAP:AIConsumed and CAP:AITagCleared for the tag variant.

Determinism notes
-----------------
- Reading a result **in the same block** is not supported; doing so will
  revert with b"CAP:NORESULT" per the host provider semantics.
- Tags are bounded (<= 32 bytes) and stored under a namespaced key to avoid
  collisions with user storage.

Events (binary names; fields are bytes)
---------------------------------------
- b"CAP:AITagSaved"   {b"tag", b"task_id"}
- b"CAP:AITagCleared" {b"tag"}
- b"CAP:AIConsumed"   {b"task_id", b"status", b"output_hash", b"size"}

Revert reasons (subset)
-----------------------
- b"CAP:TAG"      — tag invalid (empty/too long)
- b"CAP:NOTASK"   — tag lookup had no task_id
- b"CAP:NORESULT" — consume before host recorded a result (propagated)
- b"CAP:TYPE"/b"CAP:LEN" — input type/length guardrails from lower layer
"""
from __future__ import annotations

from typing import Final, Tuple

from stdlib import abi, events, storage, hash as _hash  # type: ignore

# Import the validated, low-level capability shims (enforce lengths, emit base events)
from . import (
    ai_enqueue,
    read_result,
    MAX_MODEL_LEN,
    MAX_PROMPT_LEN,
    MIN_PROMPT_LEN,
)

# -----------------------------------------------------------------------------
# Bounded constants for tag usage
# -----------------------------------------------------------------------------

TAG_NS_PREFIX: Final[bytes] = b"cap:ai:"
MAX_TAG_LEN: Final[int] = 32  # small, indexer-friendly label

# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------

def _ensure_bytes(x: object) -> bytes:
    if not isinstance(x, (bytes, bytearray)):
        abi.revert(b"CAP:TYPE")
    return bytes(x)


def _ensure_nonempty(b: bytes) -> None:
    if len(b) == 0:
        abi.revert(b"CAP:TAG")


def _ensure_tag(tag: bytes) -> bytes:
    t = _ensure_bytes(tag)
    _ensure_nonempty(t)
    if len(t) > MAX_TAG_LEN:
        abi.revert(b"CAP:TAG")
    return t


def _key_for(tag: bytes) -> bytes:
    return TAG_NS_PREFIX + tag


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def request(model: bytes, prompt: bytes) -> bytes:
    """
    Enqueue an AI job and return its deterministic task id.

    Parameters
    ----------
    model : bytes
        Model identifier (<= MAX_MODEL_LEN).
    prompt : bytes
        Input prompt bytes (MIN_PROMPT_LEN..MAX_PROMPT_LEN).

    Returns
    -------
    bytes
        task_id assigned by the host (deterministic for the tx).

    Emits
    -----
    - b"CAP:AIEnqueued" from the low-level wrapper (model, prompt_hash, task_id)
    """
    # The low-level wrapper performs all length/type validation and emits the
    # CAP:AIEnqueued event with the prompt hash. We simply forward.
    return ai_enqueue(model, prompt)


def request_store(tag: bytes, model: bytes, prompt: bytes) -> bytes:
    """
    Enqueue an AI job and store its task id under a bounded **tag** in contract storage.

    Use this in block N, then call ``consume_tag(tag)`` in block N+1.

    Emits
    -----
    - b"CAP:AIEnqueued" (base layer)
    - b"CAP:AITagSaved" with {b"tag", b"task_id"}
    """
    t = _ensure_tag(tag)
    tid = ai_enqueue(model, prompt)
    storage.set(_key_for(t), tid)
    events.emit(b"CAP:AITagSaved", {b"tag": t, b"task_id": tid})
    return tid


def load_task_id(tag: bytes) -> bytes:
    """
    Load a previously stored task id for a tag (without consuming).

    Reverts with b"CAP:NOTASK" if the tag does not exist.
    """
    t = _ensure_tag(tag)
    tid = storage.get(_key_for(t))
    if not isinstance(tid, (bytes, bytearray)) or len(tid) == 0:
        abi.revert(b"CAP:NOTASK")
    return bytes(tid)


def clear_task_id(tag: bytes) -> None:
    """
    Clear the stored task id for a tag (best-effort; no-op if absent).

    Emits b"CAP:AITagCleared" {b"tag"} when a record existed.
    """
    t = _ensure_tag(tag)
    k = _key_for(t)
    tid = storage.get(k)
    if isinstance(tid, (bytes, bytearray)) and len(tid) > 0:
        storage.set(k, b"")  # idempotent clear via empty sentinel
        events.emit(b"CAP:AITagCleared", {b"tag": t})


def consume(task_id: bytes) -> Tuple[bytes, bytes]:
    """
    Read the result for a given task id.

    Call this in **block N+1** (or later). If the host has not recorded a
    result yet, this reverts with b"CAP:NORESULT".

    Returns
    -------
    (status, output) : tuple[bytes, bytes]

    Emits
    -----
    b"CAP:AIConsumed" {b"task_id", b"status", b"output_hash", b"size"}
    """
    tid = _ensure_bytes(task_id)
    status, output = read_result(tid)
    # The lower-level read_result already emits CAP:ResultRead; we add a
    # domain-specific mirror for AI to ease indexers that only follow AI flows.
    events.emit(
        b"CAP:AIConsumed",
        {
            b"task_id": tid,
            b"status": status,
            b"output_hash": _hash.sha3_256(output),
            b"size": len(output).to_bytes(4, "big"),
        },
    )
    return (status, output)


def consume_tag(tag: bytes) -> Tuple[bytes, bytes]:
    """
    Load the task id for ``tag``, read the result, and clear the tag.

    Typical use in block N+1 after calling ``request_store(tag, ...)`` in block N.

    Reverts
    -------
    - b"CAP:NOTASK" if no task id stored under the tag.
    - b"CAP:NORESULT" if the result is not yet available.
    """
    t = _ensure_tag(tag)
    k = _key_for(t)
    tid = storage.get(k)
    if not isinstance(tid, (bytes, bytearray)) or len(tid) == 0:
        abi.revert(b"CAP:NOTASK")
    status, output = consume(bytes(tid))
    # Clear after successful consume (single-use semantics)
    storage.set(k, b"")
    events.emit(b"CAP:AITagCleared", {b"tag": t})
    return (status, output)


# -----------------------------------------------------------------------------
# Re-exports of bounds for callers/tests
# -----------------------------------------------------------------------------

__all__ = [
    "request",
    "request_store",
    "load_task_id",
    "clear_task_id",
    "consume",
    "consume_tag",
    # bounds re-exports
    "MAX_TAG_LEN",
    "MAX_MODEL_LEN",
    "MAX_PROMPT_LEN",
    "MIN_PROMPT_LEN",
]
