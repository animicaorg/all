# AI Agent template contract
# Deterministic facade over the AI capability:
# - configure a preferred `model`
# - enqueue a job with `request(prompt)` -> task_id
# - later fetch the result with `read(task_id)` or `consume_last()`
#
# This follows the VM stdlib surface:
#   from stdlib import storage, events, abi, hash, syscalls
# Required syscalls:
#   syscalls.ai_enqueue(model: bytes, prompt: bytes) -> bytes(task_id)
#   syscalls.read_result(task_id: bytes) -> bytes(result) | b"" if not ready
#
# Storage keys are compact single-bytes to reduce overhead.

from stdlib import storage, events, abi, hash, syscalls  # type: ignore

# ---- constants & keys -------------------------------------------------------

MAX_BYTES = 4096  # conservative cap for prompts/results to keep deterministic bounds

KEY_MODEL = b"m"        # preferred model id (bytes)
KEY_LAST_TASK = b"t"    # last enqueued task_id (bytes)
KEY_LAST_PROMPT = b"p"  # last prompt (truncated copy) (bytes)
KEY_LAST_RESULT = b"r"  # cached last result (bytes)


# ---- helpers ----------------------------------------------------------------

def _clamp(b: bytes, limit: int = MAX_BYTES) -> bytes:
    if len(b) > limit:
        # Keep prefix to limit but record that clamping happened for transparency.
        return b[:limit]
    return b


def _require(cond: bool, err: bytes) -> None:
    if not cond:
        abi.revert(err)  # deterministic revert with short error byte-string


def _emit(name: bytes, args: dict) -> None:
    # events.emit requires bytes for the name; values should be ABI-safe scalars/bytes.
    events.emit(name, args)


# ---- view functions ---------------------------------------------------------

def get_model() -> bytes:
    """Return the configured model id (bytes); empty if not set."""
    return storage.get(KEY_MODEL)


def status() -> tuple[bytes, bytes, bytes]:
    """
    Return (model, last_task_id, last_result_digest).
    The result digest is keccak256 of cached last result (or 32 zero-bytes if none).
    """
    model = storage.get(KEY_MODEL)
    task = storage.get(KEY_LAST_TASK)
    last_res = storage.get(KEY_LAST_RESULT)
    res_digest = hash.keccak256(last_res) if last_res else b"\x00" * 32
    return (model, task, res_digest)


def last_prompt() -> bytes:
    """Return the stored last prompt (possibly truncated to MAX_BYTES)."""
    return storage.get(KEY_LAST_PROMPT)


def last_result() -> bytes:
    """Return the cached last result bytes, if any."""
    return storage.get(KEY_LAST_RESULT)


# ---- mutating functions -----------------------------------------------------

def set_model(model: bytes) -> None:
    """
    Configure the preferred model id used by request().
    """
    model = _clamp(model)
    _require(len(model) > 0, b"MODEL_EMPTY")
    storage.set(KEY_MODEL, model)
    _emit(b"ModelSet", {
        b"model": model,
        b"model_hash": hash.keccak256(model),
    })


def request(prompt: bytes) -> bytes:
    """
    Enqueue an AI job for the configured model with the given prompt.
    Returns the task_id (bytes). The task result is expected to be readable
    next block via read(task_id) (chain-deterministic availability).
    """
    model = storage.get(KEY_MODEL)
    _require(len(model) > 0, b"NO_MODEL_CONFIGURED")

    prompt = _clamp(prompt)
    _require(len(prompt) > 0, b"PROMPT_EMPTY")

    task_id = syscalls.ai_enqueue(model, prompt)  # deterministic id: H(chainId|height|tx|caller|payload)

    storage.set(KEY_LAST_TASK, task_id)
    storage.set(KEY_LAST_PROMPT, prompt)

    _emit(b"AIRequested", {
        b"model": model,
        b"prompt_hash": hash.keccak256(prompt),
        b"task_id": task_id,
    })
    return task_id


def read(task_id: bytes) -> bytes:
    """
    Attempt to read the result for a given task_id without mutating cached state.
    Returns b"" if the result is not yet available.
    """
    _require(len(task_id) > 0, b"TASK_ID_EMPTY")
    res = syscalls.read_result(task_id)
    # Do not store; caller may poll or use consume_last().
    return _clamp(res)


def consume_last() -> bytes:
    """
    Read and cache the result for the last enqueued task (if available).
    Returns b"" if not yet available.
    Emits AIResult event when a non-empty result is observed.
    """
    task_id = storage.get(KEY_LAST_TASK)
    _require(len(task_id) > 0, b"NO_LAST_TASK")

    res = _clamp(syscalls.read_result(task_id))
    if len(res) == 0:
        return b""

    storage.set(KEY_LAST_RESULT, res)
    _emit(b"AIResult", {
        b"task_id": task_id,
        b"result_hash": hash.keccak256(res),
        b"size": len(res),
    })
    return res


def clear_cache() -> None:
    """
    Clear cached last_result (does not affect model or last_task).
    Useful to keep on-chain storage bounded for long-lived agents.
    """
    had = storage.get(KEY_LAST_RESULT)
    if len(had) > 0:
        storage.set(KEY_LAST_RESULT, b"")
        _emit(b"CacheCleared", {
            b"cleared_hash": hash.keccak256(had),
            b"prev_size": len(had),
        })
