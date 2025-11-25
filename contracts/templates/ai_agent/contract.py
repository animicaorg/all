# Deterministic AI Agent (template)
# - Stores a preferred model id (bytes)
# - Enqueues prompts to the AI Compute Fund (AICF)
# - Reads results on the next block deterministically (if available)
#
# ABI summary (docstrings drive abi_gen):
#   init(model: bytes)                -> None
#   set_model(model: bytes)           -> None
#   get_model()                       -> bytes
#   submit(prompt: bytes)             -> bytes   # returns task_id
#   last_task()                       -> bytes
#   read_last_result()                -> bytes   # reverts if not ready
#   get_last_prompt()                 -> bytes
#   get_last_result()                 -> bytes   # returns empty if none yet
#
# Events:
#   Configured(model: bytes)
#   Enqueued(task_id: bytes, model: bytes)
#   Completed(task_id: bytes)
#
# Errors (short, bytes):
#   MODEL_LEN, NOT_CONFIGURED, PROMPT_LEN, NO_TASK, NO_RESULT_YET

from stdlib.storage import get as sget, set as sset
from stdlib.events import emit
from stdlib.abi import require, revert
from stdlib.syscalls import ai_enqueue, read_result

# ---- storage keys -----------------------------------------------------------

K_MODEL       = b"ai:model"         # -> bytes (model id/name)
K_LAST_TASK   = b"ai:last_task"     # -> bytes (task id)
K_LAST_PROMPT = b"ai:last_prompt"   # -> bytes
K_LAST_RESULT = b"ai:last_result"   # -> bytes (cached latest result)


# ---- helpers ----------------------------------------------------------------

def _has_model() -> bool:
    m = sget(K_MODEL)
    return m is not None and len(m) > 0


def _require_model():
    require(_has_model(), b"NOT_CONFIGURED")


def _bounded(b: bytes, lo: int, hi: int) -> bool:
    n = len(b)
    return lo <= n <= hi


# ---- API --------------------------------------------------------------------

def init(model: bytes) -> None:
    """
    Initialize the agent with a preferred model id (bytes).
    Callable exactly once; use set_model to change later.
    """
    # Allow init if model not yet set (or set but empty).
    already = sget(K_MODEL)
    require(already is None or len(already) == 0, b"ALREADY_INIT")
    require(_bounded(model, 1, 64), b"MODEL_LEN")
    sset(K_MODEL, model)
    emit(b"Configured", {b"model": model})


def set_model(model: bytes) -> None:
    """
    Update the preferred model id. Length 1..64 bytes.
    """
    require(_bounded(model, 1, 64), b"MODEL_LEN")
    sset(K_MODEL, model)
    emit(b"Configured", {b"model": model})


def get_model() -> bytes:
    """
    Return the configured model id, or empty bytes if unset.
    """
    m = sget(K_MODEL)
    return m if m is not None else b""


def submit(prompt: bytes) -> bytes:
    """
    Enqueue a prompt to the AICF using the configured model.
    Returns the deterministic task_id (bytes).
    Constraints: prompt length 1..4096 bytes.
    Emits: Enqueued(task_id, model)
    """
    _require_model()
    require(_bounded(prompt, 1, 4096), b"PROMPT_LEN")
    model = sget(K_MODEL)
    # ai_enqueue is deterministic given (chainId|height|txHash|caller|payload)
    task_id = ai_enqueue(model, prompt)

    sset(K_LAST_TASK, task_id)
    sset(K_LAST_PROMPT, prompt)
    # Clear cached result slot to avoid stale reads.
    sset(K_LAST_RESULT, b"")

    emit(b"Enqueued", {b"task_id": task_id, b"model": model})
    return task_id


def last_task() -> bytes:
    """
    Return the last submitted task_id (bytes) or empty if none.
    """
    tid = sget(K_LAST_TASK)
    return tid if tid is not None else b""


def read_last_result() -> bytes:
    """
    Read the result for the last submitted task.
    Reverts with NO_TASK if no task exists.
    Reverts with NO_RESULT_YET if the result is not available (e.g., same block).
    On success, caches and returns the result bytes.
    Emits: Completed(task_id)
    """
    task_id = sget(K_LAST_TASK)
    require(task_id is not None and len(task_id) > 0, b"NO_TASK")

    # read_result returns None (or empty) if not ready yet, otherwise bytes
    res = read_result(task_id)
    require(res is not None and len(res) > 0, b"NO_RESULT_YET")

    sset(K_LAST_RESULT, res)
    emit(b"Completed", {b"task_id": task_id})
    return res


def get_last_prompt() -> bytes:
    """
    Return the last submitted prompt, or empty if none.
    """
    p = sget(K_LAST_PROMPT)
    return p if p is not None else b""


def get_last_result() -> bytes:
    """
    Return the cached last result if present, else empty bytes.
    Note: A fresh result is only guaranteed available one block after submit.
    """
    r = sget(K_LAST_RESULT)
    return r if r is not None else b""
