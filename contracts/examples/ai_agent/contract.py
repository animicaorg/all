# -*- coding: utf-8 -*-
"""
AI Agent example contract.

This contract demonstrates a minimal, deterministic integration with the Animica
capabilities layer to:
  1) enqueue an AI job (model + prompt) and return a deterministic task_id;
  2) consume the result starting the *next* block (deterministically).

Persistent state (simple KV layout):
  p:{task_id} -> original prompt bytes
  m:{task_id} -> model tag as bytes (normalized/truncated)
  o:{task_id} -> last known output bytes (set on successful read)
  s:{task_id} -> b"1" if a result has been seen/consumed, else missing

Events:
  JobRequested(task_id, model_tag, prompt_hash)
  JobResult(task_id, ok, output_hash, units_used)

Notes:
- This file intentionally keeps logic small and audit-friendly. It enforces basic
  length caps for inputs; larger artifacts should be pinned with DA and referenced
  by hash in the prompt.
- The next-block determinism rule is enforced by the host runtime; this contract
  simply surfaces the result via `read(task_id)`.
"""

from stdlib import (abi, events, hash,  # type: ignore[reportMissingImports]
                    storage, syscalls)

# ---- caps & prefixes ---------------------------------------------------------

# Conservative caps to bound per-tx cost. Tweak to your chain policy.
_MAX_MODEL_LEN = 96  # bytes (e.g., b"gemma-2b-instruct" or catalog id)
_MAX_PROMPT_LEN = 4096  # bytes (UTF-8 text OK)

_P_PREFIX = b"p:"  # prompts
_M_PREFIX = b"m:"  # model tag
_O_PREFIX = b"o:"  # outputs
_S_PREFIX = b"s:"  # seen/consumed flag (b"1")


# ---- helpers -----------------------------------------------------------------


def _clamp_model_tag(model: bytes) -> bytes:
    """
    Normalize model tag for storage/event readability.
    - strip leading/trailing whitespace (if textual),
    - truncate to _MAX_MODEL_LEN,
    - leave raw bytes otherwise (ABI stays bytes).
    """
    m = model[:_MAX_MODEL_LEN]
    # cheap printable trim (doesn't change determinism for binary tags)
    try:
        ms = m.decode("utf-8").strip()
        return ms.encode("utf-8")
    except Exception:
        return m


def _keccak32(b: bytes) -> bytes:
    """Return 32-byte Keccak-256 digest of b."""
    return hash.keccak256(b)  # stdlib.hash API returns 32 bytes


def _sha3_32(b: bytes) -> bytes:
    """Return 32-byte SHA3-256 digest of b."""
    return hash.sha3_256(b)  # provided by stdlib.hash


def _key(prefix: bytes, task_id: bytes) -> bytes:
    return prefix + task_id


# ---- public interface ---------------------------------------------------------


def request(model: bytes, prompt: bytes) -> bytes:
    """
    Enqueue an AI job.

    Args:
      model:  short model tag/catalog id (bytes)
      prompt: input bytes (UTF-8 text allowed)

    Returns:
      task_id: deterministic identifier for this request (bytes)
    """
    # Input caps (deterministic safety). The ABI keeps type=bytes.
    if len(model) == 0 or len(model) > _MAX_MODEL_LEN:
        abi.revert(b"model_len_invalid")
    if len(prompt) == 0 or len(prompt) > _MAX_PROMPT_LEN:
        abi.revert(b"prompt_len_invalid")

    model_tag = _clamp_model_tag(model)
    prompt_hash = _sha3_32(prompt)

    # Host syscall performs deterministic task_id derivation & queues the job.
    # Expected shape: ai_enqueue(model: bytes, prompt: bytes) -> bytes (task_id)
    task_id = syscalls.ai_enqueue(model_tag, prompt)

    # Persist minimal provenance to aid explorers and users.
    storage.set(_key(_P_PREFIX, task_id), prompt)
    storage.set(_key(_M_PREFIX, task_id), model_tag)

    # Emit canonical request event.
    events.emit(
        b"JobRequested",
        {b"task_id": task_id, b"model_tag": model_tag, b"prompt_hash": prompt_hash},
    )

    return task_id


def read(task_id: bytes) -> tuple:
    """
    Consume a result for a previously enqueued task_id (next-block or later).

    Args:
      task_id: bytes returned by `request(...)`

    Returns:
      (ok: bool, output: bytes)
        ok=False if a verifiable result is not yet available.
        When ok=True, `output` contains provider result bytes.
    """
    if len(task_id) == 0:
        abi.revert(b"task_id_empty")

    # Expected shape: read_result(task_id: bytes) -> (ok: bool, output: bytes, units_used: int)
    # - ok is True only after the provider's proof landed on-chain in a prior block.
    ok, output, units_used = syscalls.read_result(task_id)

    if ok:
        # Persist for auditability & off-chain indexing.
        storage.set(_key(_O_PREFIX, task_id), output)
        storage.set(_key(_S_PREFIX, task_id), b"1")
        output_hash = _sha3_32(output)
        units = units_used if isinstance(units_used, int) else 0
        events.emit(
            b"JobResult",
            {
                b"task_id": task_id,
                b"ok": True,
                b"output_hash": output_hash,
                b"units_used": units,
            },
        )
        return True, output

    # Not yet available (deterministic outcome).
    events.emit(
        b"JobResult",
        {
            b"task_id": task_id,
            b"ok": False,
            b"output_hash": b"\x00" * 32,
            b"units_used": 0,
        },
    )
    return False, b""


# ---- optional view helpers (read-only) ---------------------------------------


def get_prompt(task_id: bytes) -> bytes:
    """
    Return the original prompt bytes for a task_id, or b"" if unknown.
    """
    if len(task_id) == 0:
        return b""
    v = storage.get(_key(_P_PREFIX, task_id))
    return v if v is not None else b""


def get_model(task_id: bytes) -> bytes:
    """
    Return the normalized model tag for a task_id, or b"" if unknown.
    """
    if len(task_id) == 0:
        return b""
    v = storage.get(_key(_M_PREFIX, task_id))
    return v if v is not None else b""


def get_output(task_id: bytes) -> bytes:
    """
    Return the last stored output for a task_id, or b"" if no successful read yet.
    """
    if len(task_id) == 0:
        return b""
    v = storage.get(_key(_O_PREFIX, task_id))
    return v if v is not None else b""


def has_result(task_id: bytes) -> bool:
    """
    True iff a successful result was seen and stored for task_id.
    """
    if len(task_id) == 0:
        return False
    v = storage.get(_key(_S_PREFIX, task_id))
    return v == b"1"
