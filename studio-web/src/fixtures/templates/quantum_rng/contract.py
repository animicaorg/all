# Quantum RNG demo contract (simulation-friendly).
# - If runtime exposes syscalls.quantum_enqueue/read_result, we use them.
# - Otherwise, we fall back to a deterministic KDF using keccak256 over a local seed.
#
# Storage layout (keys → bytes):
#   b"seed"       : current seed (bytes)
#   b"ctr"        : little-endian u64 counter (bytes, length 8)
#   b"last_task"  : last enqueued task_id (bytes, empty if none)
#   b"last_rand"  : last produced randomness bytes (opaque)

from stdlib import storage, events, hash, abi

# Optional capability surface (not available in in-browser simulator)
try:
    # In full node/VM this is provided; in studio-wasm it's absent.
    from stdlib import syscalls  # type: ignore[attr-defined]
    _HAS_SYSCALLS = True
except Exception:
    _HAS_SYSCALLS = False

    class _SysNoop:
        def quantum_enqueue(self, circuit: bytes, shots: int) -> bytes:
            # No-op in local simulation: pretend no task is created.
            return b""
        def read_result(self, task_id: bytes) -> bytes:
            return b""

    syscalls = _SysNoop()  # type: ignore[assignment]

K_SEED = b"seed"
K_CTR = b"ctr"
K_LAST_TASK = b"last_task"
K_LAST_RAND = b"last_rand"

def _u64_to_le(n: int) -> bytes:
    if n < 0:
        n = 0
    out = bytearray(8)
    for i in range(8):
        out[i] = (n >> (8 * i)) & 0xFF
    return bytes(out)

def _le_to_u64(b: bytes) -> int:
    if not b:
        return 0
    x = 0
    lim = 8 if len(b) >= 8 else len(b)
    for i in range(lim):
        x |= (b[i] & 0xFF) << (8 * i)
    return x

def _load_seed() -> bytes:
    s = storage.get(K_SEED)
    if s is None:
        s = b"\x00" * 32
        storage.set(K_SEED, s)
    return s

def _load_ctr() -> int:
    c = storage.get(K_CTR)
    return _le_to_u64(c if c is not None else b"")

def _bump_ctr() -> int:
    n = _load_ctr() + 1
    storage.set(K_CTR, _u64_to_le(n))
    return n

def get_seed() -> bytes:
    """
    Return the current local RNG seed (bytes).
    """
    return _load_seed()

def set_seed(seed: bytes) -> None:
    """
    Set the local RNG seed. Emits SeedSet event with seed_hash.
    """
    if not seed:
        abi.revert(b"SEED_EMPTY")
    storage.set(K_SEED, seed)
    storage.set(K_CTR, _u64_to_le(0))
    events.emit(b"SeedSet", {
        b"seed_hash": hash.keccak256(seed),
    })

def last_task() -> bytes:
    """
    Return the last enqueued task_id (bytes) or empty if none.
    """
    t = storage.get(K_LAST_TASK)
    return t if t is not None else b""

def last() -> bytes:
    """
    Return the last produced randomness bytes (opaque) or empty.
    """
    r = storage.get(K_LAST_RAND)
    return r if r is not None else b""

def status() -> tuple[bytes, bytes, int]:
    """
    Return (last_task_id, last_rand, ctr).
    """
    return (last_task(), last(), _load_ctr())

def request(bits: int) -> bytes:
    """
    Request quantum randomness.
    - If syscalls.quantum_enqueue is available, we enqueue a tiny "Bell" circuit payload.
    - Otherwise (e.g., browser sim), we record a pseudo task_id = keccak('QRNG'|seed|ctr).
    Always emits Requested event.
    Returns task_id (may be empty in pure simulation).
    """
    if bits <= 0:
        abi.revert(b"BITS_TOO_SMALL")
    # Build a tiny circuit payload (opaque to the VM). Keep it byte-only.
    # Format: b"bell|" + u64_le(bits) + b"|" + u64_le(shots)
    shots = 1
    payload = b"bell|" + _u64_to_le(bits) + b"|" + _u64_to_le(shots)

    if _HAS_SYSCALLS:
        task_id = syscalls.quantum_enqueue(payload, shots)  # type: ignore[attr-defined]
    else:
        # Deterministic pseudo task id for local sim:
        seed = _load_seed()
        ctr = _load_ctr()
        task_id = hash.keccak256(b"QRNG|task|" + seed + _u64_to_le(ctr) + _u64_to_le(bits))

    storage.set(K_LAST_TASK, task_id)
    events.emit(b"Requested", {
        b"bits": bits,
        b"task_id": task_id,
        b"payload_hash": hash.keccak256(payload),
    })
    return task_id

def read(task_id: bytes) -> bytes:
    """
    Read a result for a specific task_id, without consuming it.
    If unavailable, returns empty bytes.
    """
    if not task_id:
        abi.revert(b"TASK_ID_EMPTY")
    if not _HAS_SYSCALLS:
        return b""
    data = syscalls.read_result(task_id)  # type: ignore[attr-defined]
    return data if data is not None else b""

def consume_last() -> bytes:
    """
    Consume the latest randomness, preferring a finished quantum job result.
    Fallback: derive bytes via keccak256('QRNG'|seed|ctr), bump ctr, and store.
    Emits Result event on success.
    Returns the randomness bytes (possibly empty if nothing was available).
    """
    task = last_task()
    out: bytes = b""

    if _HAS_SYSCALLS and task:
        data = syscalls.read_result(task)  # type: ignore[attr-defined]
        if data:
            out = data

    if not out:
        # Fallback deterministic path
        seed = _load_seed()
        ctr = _bump_ctr()
        # 32 bytes of output; callers can expand by hashing again if desired.
        out = hash.keccak256(b"QRNG|out|" + seed + _u64_to_le(ctr))
        storage.set(K_LAST_RAND, out)
    else:
        # On real result, also update last_rand
        storage.set(K_LAST_RAND, out)

    events.emit(b"Result", {
        b"task_id": task,
        b"result_hash": hash.keccak256(out),
        b"size": len(out),
    })
    return out

def clear() -> None:
    """
    Clear the cached last result and task pointer. Emits CacheCleared.
    """
    prev = storage.get(K_LAST_RAND)
    storage.set(K_LAST_RAND, b"")
    storage.set(K_LAST_TASK, b"")
    events.emit(b"CacheCleared", {
        b"prev_hash": hash.keccak256(prev if prev else b""),
        b"prev_size": len(prev) if prev else 0,
    })
