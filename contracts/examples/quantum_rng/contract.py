# -*- coding: utf-8 -*-
"""
Quantum RNG (mixed with chain beacon).

This contract demonstrates a two-step pattern:
1) request(): enqueue a quantum randomness job with traps (next-block resolution).
2) poll(task_id): when the result becomes available, read it and MIX with the
   chain beacon output to reduce potential bias/withholding.

Mixing rule (deterministic, cheap):
    mix = XOR( sha3_256(qbytes), sha3_256(beacon_bytes) )

Events:
- Requested(task_id, bits, shots, trap_rate)
- Fulfilled(task_id, out)

Storage keys:
- b"pending_task" : bytes32 task id (empty if none pending)
- b"last_raw"     : last raw provider bytes (post-verification, pre-mix)
- b"last_mix"     : last mixed output (bytes)
- b"stat_requests": u64 count
- b"stat_fulfill" : u64 count

Notes:
- All arguments are bounded to keep costs predictable.
- The task_id is deterministically derived by the capability layer from
  (chainId|height|txHash|caller|payload). The contract does not compute it.
- poll() never returns the provider output in the same block as request().
"""

# Contract-safe stdlib (imported by the VM sandbox)
from stdlib import storage, events, abi
from stdlib.hash import sha3_256

# --- syscall bindings ---------------------------------------------------------

# We keep imports flexible to accommodate slightly different host names across
# environments (devnet vs. future networks).
try:
    from stdlib.syscalls import quantum_enqueue, read_result, read_beacon
except Exception:  # pragma: no cover
    # Fallbacks (older runtimes may expose a single namespace)
    from stdlib import syscalls  # type: ignore

    quantum_enqueue = getattr(syscalls, "quantum_enqueue")
    read_result = getattr(syscalls, "read_result")
    # Beacon might not exist in very early dev builds; provide a zero beacon.
    _rb = getattr(syscalls, "read_beacon", None)
    read_beacon = _rb if _rb is not None else (lambda: b"\x00" * 32)  # type: ignore


# --- constants / bounds -------------------------------------------------------

_MAX_BITS = 4096          # hard cap for requested random bits
_MIN_BITS = 32            # ensure we get at least 32 bits (one SHA3-256 block)
_MAX_SHOTS = 4096         # limit repetitions (sampling passes)
_MIN_SHOTS = 16
_MIN_TRAP_RATE = 4        # at least 1 trap per 4 data qubits
_MAX_TRAP_RATE = 1024     # but bound so payload stays compact

# Storage keys
_K_PENDING = b"pending_task"
_K_LAST_RAW = b"last_raw"
_K_LAST_MIX = b"last_mix"
_K_CNT_REQ = b"stat_requests"
_K_CNT_FUL = b"stat_fulfill"

# Event names (bytes for deterministic ABI)
_EV_REQUESTED = b"Requested"
_EV_FULFILLED = b"Fulfilled"

__all__ = ("request", "poll", "last", "stats")


# --- utility helpers (deterministic only) ------------------------------------

def _u32(n: int) -> bytes:
    n &= 0xFFFFFFFF
    return bytes(((n >> 24) & 0xFF,
                  (n >> 16) & 0xFF,
                  (n >> 8) & 0xFF,
                  n & 0xFF))


def _u64(n: int) -> bytes:
    n &= 0xFFFFFFFFFFFFFFFF
    return bytes(((n >> 56) & 0xFF,
                  (n >> 48) & 0xFF,
                  (n >> 40) & 0xFF,
                  (n >> 32) & 0xFF,
                  (n >> 24) & 0xFF,
                  (n >> 16) & 0xFF,
                  (n >> 8) & 0xFF,
                  n & 0xFF))


def _inc(key: bytes) -> None:
    cur = storage.get(key)
    if cur is None or len(cur) == 0:
        storage.set(key, _u64(1))
    else:
        # big-endian u64
        v = (cur[0] << 56) | (cur[1] << 48) | (cur[2] << 40) | (cur[3] << 32) | \
            (cur[4] << 24) | (cur[5] << 16) | (cur[6] << 8) | cur[7]
        storage.set(key, _u64(v + 1))


def _encode_payload(bits: int, shots: int, trap_rate: int) -> bytes:
    """
    Deterministic, minimal payload for the quantum_enqueue syscall.
    Format (big-endian fields):
        b"Q_RNG_V1" | u32(bits) | u32(shots) | u32(trap_rate)
    The host/provider understands this compact header to synthesize an
    H-heavy circuit with interleaved trap checks.
    """
    return b"Q_RNG_V1" + _u32(bits) + _u32(shots) + _u32(trap_rate)


def _xor(a: bytes, b: bytes) -> bytes:
    ln = len(a) if len(a) < len(b) else len(b)
    out = bytearray(ln)
    i = 0
    while i < ln:
        out[i] = a[i] ^ b[i]
        i += 1
    return bytes(out)


def _mix_with_beacon(qbytes: bytes, beacon: bytes) -> bytes:
    """
    Extract-then-XOR:
      mq = sha3_256(qbytes)
      mb = sha3_256(beacon)
      return XOR(mq, mb)
    Produces 32 bytes regardless of input sizes (as long as inputs are non-empty).
    """
    mq = sha3_256(qbytes)
    mb = sha3_256(beacon)
    return _xor(mq, mb)


# --- contract entrypoints -----------------------------------------------------

def request(bits: int = 256, shots: int = 256, trap_rate: int = 16) -> bytes:
    """
    Enqueue a quantum job for ~`bits` random bits with `shots` sampling passes
    and trap density ~1 per `trap_rate`. Returns a deterministic task_id (bytes32).
    """
    # sanitize/clamp
    if bits < _MIN_BITS:
        bits = _MIN_BITS
    if bits > _MAX_BITS:
        bits = _MAX_BITS
    if shots < _MIN_SHOTS:
        shots = _MIN_SHOTS
    if shots > _MAX_SHOTS:
        shots = _MAX_SHOTS
    if trap_rate < _MIN_TRAP_RATE or trap_rate > _MAX_TRAP_RATE:
        abi.revert(b"trap_rate out of bounds")

    # Optionally enforce single pending request to keep UX simple.
    pend = storage.get(_K_PENDING)
    if pend is not None and len(pend) == 32:
        # There's already a pending request that hasn't been consumed yet.
        # To preserve determinism and avoid accidental replacement, revert.
        abi.revert(b"request already pending")

    payload = _encode_payload(bits, shots, trap_rate)
    task_id = quantum_enqueue(payload)  # bytes32

    storage.set(_K_PENDING, task_id)
    _inc(_K_CNT_REQ)

    events.emit(_EV_REQUESTED, {
        b"task_id": task_id,
        b"bits": bits,
        b"shots": shots,
        b"trap_rate": trap_rate,
    })
    return task_id


def poll(task_id: bytes) -> tuple:
    """
    Attempt to read the job result and produce the mixed output.

    Returns:
        (ready: bool, out: bytes)

    Behavior:
    - If the result is not yet available, returns (False, b"").
    - If available:
        * mix = XOR(sha3(qbytes), sha3(beacon))
        * persist last_raw and last_mix
        * clear pending_task (if matches)
        * emit Fulfilled(task_id, mix)
        * return (True, mix)
    """
    if task_id is None or len(task_id) == 0:
        abi.revert(b"bad task_id")

    qbytes = read_result(task_id)  # returns b"" if not ready
    if qbytes is None or len(qbytes) == 0:
        return (False, b"")

    # Read the beacon (adapter exposes the current/previous as per host policy;
    # we simply mix whatever is exposed deterministically).
    beacon = read_beacon()
    if beacon is None or len(beacon) == 0:
        # Even in the (unlikely) absence of a beacon, keep behavior well-defined.
        beacon = b"\x00" * 32

    mixed = _mix_with_beacon(qbytes, beacon)

    # Book-keeping
    storage.set(_K_LAST_RAW, qbytes)
    storage.set(_K_LAST_MIX, mixed)

    pend = storage.get(_K_PENDING)
    if pend is not None and pend == task_id:
        # Consume the pending slot only if it matches the polled task.
        storage.set(_K_PENDING, b"")

    _inc(_K_CNT_FUL)

    events.emit(_EV_FULFILLED, {b"task_id": task_id, b"out": mixed})
    return (True, mixed)


def last() -> bytes:
    """
    Return the last mixed output (or b"" if none).
    """
    v = storage.get(_K_LAST_MIX)
    return v if v is not None else b""


def stats() -> dict:
    """
    Return a small status snapshot:
        {
          b"pending": bytes32 | b"",
          b"requests": u64,
          b"fulfilled": u64
        }
    """
    pending = storage.get(_K_PENDING) or b""
    req = storage.get(_K_CNT_REQ) or _u64(0)
    ful = storage.get(_K_CNT_FUL) or _u64(0)
    return {b"pending": pending, b"requests": req, b"fulfilled": ful}
