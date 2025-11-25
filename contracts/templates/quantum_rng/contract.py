# Animica Template: Quantum RNG Mixer
# Deterministic subset only; relies on VM stdlib surface.
from stdlib import storage, events, abi, hash, syscalls

# ---- storage keys (bytes literals to avoid accidental collisions) ------------
K_INIT = b"\x01init"
K_CIRCUIT = b"\x02circuit"         # bytes: user-provided circuit (JSON or domain-specific)
K_SHOTS = b"\x03shots"             # u32 (big-endian) encoded
K_LAST_TASK = b"\x10last_task"     # bytes: deterministic task_id from enqueue
K_LAST_RESULT = b"\x11last_result" # bytes: cached recent result (bounded)
K_LAST_MIX = b"\x12last_mix"       # bytes32: sha3_256(rng|result_prefix)

# ---- constants & caps --------------------------------------------------------
MAX_CIRCUIT_LEN = 4096     # bytes; template-friendly ceiling
MIN_SHOTS = 1
MAX_SHOTS = 8192
RESULT_CACHE_MAX = 256     # cap how much result we persist (bytes)
MIX_PREFIX_MAX = 64        # limit result contribution to mix

# Errors
E_ALREADY_INIT = b"ALREADY_INIT"
E_NOT_CONFIGURED = b"NOT_CONFIGURED"
E_CIRCUIT_LEN = b"CIRCUIT_LEN"
E_SHOTS_RANGE = b"SHOTS_RANGE"
E_NO_TASK = b"NO_TASK"
E_NO_RESULT_YET = b"NO_RESULT_YET"

# ---- helpers ----------------------------------------------------------------
def _u32_to_be(n: int) -> bytes:
    if n < 0 or n > 0xFFFFFFFF:
        abi.revert(E_SHOTS_RANGE)
    b0 = (n >> 24) & 0xFF
    b1 = (n >> 16) & 0xFF
    b2 = (n >> 8) & 0xFF
    b3 = n & 0xFF
    return bytes([b0, b1, b2, b3])

def _be_to_u32(b: bytes) -> int:
    if len(b) != 4:
        return 0
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]

def _get_bytes(key: bytes) -> bytes:
    v = storage.get(key)
    return v if isinstance(v, bytes) else b""

def _set_bytes(key: bytes, v: bytes) -> None:
    storage.set(key, v)

def _require_configured() -> None:
    if storage.get(K_INIT) != b"\x01":
        abi.revert(E_NOT_CONFIGURED)

# ---- API --------------------------------------------------------------------
def init(circuit: bytes, shots: int) -> None:
    """
    Initialize the contract with a default quantum circuit and shot count.

    Determinism notes:
    - Validates input sizes to keep on-chain state bounded.
    - Emits a Configured event for indexers.
    """
    if storage.get(K_INIT) == b"\x01":
        abi.revert(E_ALREADY_INIT)

    if not isinstance(circuit, (bytes, bytearray)):
        abi.revert(E_CIRCUIT_LEN)
    if len(circuit) == 0 or len(circuit) > MAX_CIRCUIT_LEN:
        abi.revert(E_CIRCUIT_LEN)
    if shots < MIN_SHOTS or shots > MAX_SHOTS:
        abi.revert(E_SHOTS_RANGE)

    _set_bytes(K_CIRCUIT, bytes(circuit))
    _set_bytes(K_SHOTS, _u32_to_be(int(shots)))
    _set_bytes(K_INIT, b"\x01")

    events.emit(b"Configured", {b"circuit_len": len(circuit), b"shots": int(shots)})

def set_config(circuit: bytes, shots: int) -> None:
    """
    Update circuit and shots. Bounded by the same limits as init().
    """
    _require_configured()
    if not isinstance(circuit, (bytes, bytearray)):
        abi.revert(E_CIRCUIT_LEN)
    if len(circuit) == 0 or len(circuit) > MAX_CIRCUIT_LEN:
        abi.revert(E_CIRCUIT_LEN)
    if shots < MIN_SHOTS or shots > MAX_SHOTS:
        abi.revert(E_SHOTS_RANGE)

    _set_bytes(K_CIRCUIT, bytes(circuit))
    _set_bytes(K_SHOTS, _u32_to_be(int(shots)))
    events.emit(b"Configured", {b"circuit_len": len(circuit), b"shots": int(shots)})

def get_config() -> bytes:
    """
    Return the current circuit (bytes). Call get_shots() for the shot count.
    """
    _require_configured()
    return _get_bytes(K_CIRCUIT)

def get_shots() -> int:
    """
    Return the current shot count (u32).
    """
    _require_configured()
    return _be_to_u32(_get_bytes(K_SHOTS))

def enqueue() -> bytes:
    """
    Enqueue the current circuit to the quantum compute capability.

    Returns:
      task_id (bytes): Deterministic per (chainId|height|txHash|caller|payload).

    Emits:
      Enqueued(task_id, shots).
    """
    _require_configured()
    circuit = _get_bytes(K_CIRCUIT)
    shots = get_shots()
    # Template call: the VM's syscall surface provides quantum_enqueue(circuit, shots) -> task_id
    task_id = syscalls.quantum_enqueue(circuit, shots)
    if not isinstance(task_id, (bytes, bytearray)) or len(task_id) == 0:
        # Defensive guard: tasks must have non-empty ids
        abi.revert(b"TASK_ID")

    task_id_b = bytes(task_id)
    _set_bytes(K_LAST_TASK, task_id_b)
    events.emit(b"Enqueued", {b"task_id": task_id_b, b"shots": int(shots)})
    return task_id_b

def read_last_result() -> bytes:
    """
    Read (and cache) the result for the last enqueued task, and update the mixed RNG value.

    Deterministic mixing strategy (bias-resistant template):
      mix = sha3_256( random_bytes(32) || result_prefix )
    where:
      - random_bytes(32) is a host-provided deterministic beacon-backed source
        (pure stub on devnets; mixes the beacon when available).
      - result_prefix = result[0:MIX_PREFIX_MAX]

    Returns:
      cached_result (bytes): The bounded result payload we store (<= RESULT_CACHE_MAX).
    Emits:
      Completed(task_id, mix)
    """
    _require_configured()
    task_id = _get_bytes(K_LAST_TASK)
    if len(task_id) == 0:
        abi.revert(E_NO_TASK)

    res = syscalls.read_result(task_id)
    if not isinstance(res, (bytes, bytearray)) or len(res) == 0:
        abi.revert(E_NO_RESULT_YET)

    res_b = bytes(res)
    # Bound the stored result to keep state small
    cached = res_b[:RESULT_CACHE_MAX]
    _set_bytes(K_LAST_RESULT, cached)

    # Derive the mixed RNG output (bytes32)
    rng32 = syscalls.random(32)              # deterministic; beacon-mixed when available
    if not isinstance(rng32, (bytes, bytearray)) or len(rng32) != 32:
        # Fallback: ensure we still produce a 32-byte mix deterministically
        rng32 = b"\x00" * 32
    result_prefix = res_b[:MIX_PREFIX_MAX]
    mixed = hash.sha3_256(bytes(rng32) + result_prefix)
    _set_bytes(K_LAST_MIX, mixed)

    events.emit(b"Completed", {b"task_id": task_id, b"mix": mixed})
    return cached

def last_task() -> bytes:
    """
    Return the last task id (or empty bytes).
    """
    return _get_bytes(K_LAST_TASK)

def get_last_result() -> bytes:
    """
    Return the cached last result (may be empty if not yet read).
    """
    return _get_bytes(K_LAST_RESULT)

def get_mix() -> bytes:
    """
    Return the latest 32-byte mixed RNG value (or empty bytes).
    """
    return _get_bytes(K_LAST_MIX)
