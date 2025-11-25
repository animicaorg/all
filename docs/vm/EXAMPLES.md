# VM(Py) Contract Examples
Counter • Escrow • Token • AI Agent • Quantum RNG  
All examples use the deterministic Python subset and the provided stdlib (no I/O, no nondeterminism).

> Import surface you can rely on:
> ```python
> from stdlib import storage, events, hash, abi, treasury, syscalls, random
> ```
> - **storage**: deterministic key/value (bytes→bytes)
> - **events**: structured logs (name: bytes, args: dict[bytes→scalar/bytes])
> - **hash**: `sha3_256`, `sha3_512`, `keccak256`
> - **abi**: helpers like `require(cond, b"REASON")`
> - **treasury**: local-run safe balance accounting helpers
> - **syscalls**: capability shims (blob_pin, ai_enqueue, quantum_enqueue, read_result, zk_verify, random)
> - **random**: deterministic PRNG (local-only stub, seeded from tx hash); production mixes beacon via `syscalls.random(n)`

---

## 1) Counter (minimal, deterministic)

**ABI**:
- `inc(caller: bytes) -> int`
- `get() -> int`

**Code**
```python
from stdlib import storage, events

KEY = b"state:v1:counter"

def inc(caller: bytes) -> int:
    raw = storage.get(KEY) or (0).to_bytes(8, "big")
    val = int.from_bytes(raw, "big") + 1
    storage.set(KEY, val.to_bytes(8, "big"))
    events.emit(b"Inc", {b"by": caller, b"new": val})
    return val

def get() -> int:
    raw = storage.get(KEY) or (0).to_bytes(8, "big")
    return int.from_bytes(raw, "big")

Notes
	•	Use fixed-size integers encoded to bytes for stability.
	•	Emit compact events for observability (see docs/vm/DEBUGGING.md).

⸻

2) Escrow (payer deposits, payee withdraws on approval)

ABI (simplified):
	•	init(payer: bytes, payee: bytes, amount: int) -> None
	•	deposit(from_addr: bytes, amount: int) -> None
	•	approve(arbiter: bytes) -> None
	•	withdraw(to: bytes) -> int
	•	refund(to: bytes) -> int
	•	status() -> dict

This variant omits block-time deadlines to avoid time sources; add them only if your env exposes deterministic block context through the stdlib.

Code

from stdlib import storage, events, abi, treasury

PAYER   = b"esc:payer"
PAYEE   = b"esc:payee"
AMOUNT  = b"esc:amount"
DEPOSIT = b"esc:deposited"
APPROVED= b"esc:approved"

def _u64(i: int) -> bytes: return i.to_bytes(8, "big")
def _i(raw: bytes|None) -> int: return int.from_bytes(raw or b"\x00"*8, "big")

def init(payer: bytes, payee: bytes, amount: int) -> None:
    abi.require(storage.get(PAYER) is None, b"ALREADY_INIT")
    abi.require(amount > 0, b"BAD_AMOUNT")
    storage.set(PAYER, payer)
    storage.set(PAYEE, payee)
    storage.set(AMOUNT, _u64(amount))
    storage.set(DEPOSIT, _u64(0))
    storage.set(APPROVED, b"\x00")
    events.emit(b"Init", {b"payer": payer, b"payee": payee, b"amt": amount})

def deposit(from_addr: bytes, amount: int) -> None:
    abi.require(storage.get(PAYER) == from_addr, b"NOT_PAYER")
    needed = _i(storage.get(AMOUNT)) - _i(storage.get(DEPOSIT))
    abi.require(amount > 0 and amount <= needed, b"BAD_DEPOSIT")
    # debit payer, credit contract (local-only treasury; real credit handled by execution)
    treasury.transfer(from_addr, b":contract", amount)
    storage.set(DEPOSIT, _u64(_i(storage.get(DEPOSIT)) + amount))
    events.emit(b"Deposit", {b"from": from_addr, b"amt": amount})

def approve(arbiter: bytes) -> None:
    # in a real app, restrict arbiter; here we accept any caller (demo)
    storage.set(APPROVED, b"\x01")
    events.emit(b"Approved", {b"by": arbiter})

def withdraw(to: bytes) -> int:
    abi.require(storage.get(APPROVED) == b"\x01", b"NOT_APPROVED")
    abi.require(storage.get(PAYEE) == to, b"NOT_PAYEE")
    amt = _i(storage.get(DEPOSIT))
    abi.require(amt > 0, b"EMPTY")
    storage.set(DEPOSIT, _u64(0))
    treasury.transfer(b":contract", to, amt)
    events.emit(b"Withdraw", {b"to": to, b"amt": amt})
    return amt

def refund(to: bytes) -> int:
    abi.require(storage.get(APPROVED) != b"\x01", b"ALREADY_APPROVED")
    abi.require(storage.get(PAYER) == to, b"NOT_PAYER")
    amt = _i(storage.get(DEPOSIT))
    abi.require(amt > 0, b"EMPTY")
    storage.set(DEPOSIT, _u64(0))
    treasury.transfer(b":contract", to, amt)
    events.emit(b"Refund", {b"to": to, b"amt": amt})
    return amt

def status() -> dict:
    return {
        b"payer": storage.get(PAYER) or b"",
        b"payee": storage.get(PAYEE) or b"",
        b"amount": _i(storage.get(AMOUNT)),
        b"deposited": _i(storage.get(DEPOSIT)),
        b"approved": 1 if (storage.get(APPROVED) == b"\x01") else 0,
    }


⸻

3) Token (minimal, owner-minted)

ABI
	•	name() -> bytes, symbol() -> bytes, decimals() -> int
	•	balance_of(owner: bytes) -> int
	•	transfer(sender: bytes, to: bytes, amt: int) -> None
	•	mint(owner: bytes, to: bytes, amt: int) -> None (owner-only)

Code

from stdlib import storage, events, abi

NAME    = b"tok:name"
SYMBOL  = b"tok:sym"
DEC     = b"tok:dec"
OWNER   = b"tok:owner"
SUPPLY  = b"tok:supply"
BALPRE  = b"tok:bal:"  # prefix

def _k(addr: bytes) -> bytes: return BALPRE + addr
def _u(i: int) -> bytes: return i.to_bytes(16, "big")
def _i(b: bytes|None) -> int: return int.from_bytes(b or b"\x00"*16, "big")

def _init_once() -> None:
    if storage.get(NAME) is None:
        storage.set(NAME, b"Animica Token")
        storage.set(SYMBOL, b"ANM")
        storage.set(DEC, (18).to_bytes(1, "big"))
        storage.set(OWNER, b"admin:owner")   # set via deploy-time args in real usage
        storage.set(SUPPLY, _u(0))

def name() -> bytes:
    _init_once(); return storage.get(NAME)

def symbol() -> bytes:
    _init_once(); return storage.get(SYMBOL)

def decimals() -> int:
    _init_once(); return int.from_bytes(storage.get(DEC), "big")

def balance_of(owner: bytes) -> int:
    return _i(storage.get(_k(owner)))

def transfer(sender: bytes, to: bytes, amt: int) -> None:
    abi.require(amt > 0, b"BAD_AMT")
    sb = _i(storage.get(_k(sender)))
    abi.require(sb >= amt, b"NO_FUNDS")
    tb = _i(storage.get(_k(to)))
    storage.set(_k(sender), _u(sb - amt))
    storage.set(_k(to), _u(tb + amt))
    events.emit(b"Transfer", {b"from": sender, b"to": to, b"amt": amt})

def mint(owner: bytes, to: bytes, amt: int) -> None:
    _init_once()
    abi.require(storage.get(OWNER) == owner, b"NOT_OWNER")
    abi.require(amt > 0, b"BAD_AMT")
    tb = _i(storage.get(_k(to)))
    storage.set(_k(to), _u(tb + amt))
    supply = _i(storage.get(SUPPLY)) + amt
    storage.set(SUPPLY, _u(supply))
    events.emit(b"Mint", {b"to": to, b"amt": amt})

Notes
	•	Keep keys short (tok:bal:<addr>) for low overhead.
	•	Use owner-only mint for simplicity; add burn/approve/transfer_from as needed.

⸻

4) AI Agent (enqueue inference, then consume next block)

Pattern
	1.	request_infer(model: bytes, prompt: bytes, max_units: int) -> bytes
Returns task_id (deterministic hash).
	2.	consume(task_id: bytes) -> dict|None
Returns result record once the proof appears on-chain (next block); otherwise None.

Code

from stdlib import syscalls, storage, events, abi, hash

# Store minimal metadata for UX
META = b"ai:meta:"   # task_id -> {caller, model}

def request_infer(caller: bytes, model: bytes, prompt: bytes, max_units: int) -> bytes:
    abi.require(0 < len(prompt) <= 4096, b"PROMPT_SIZE")
    abi.require(max_units > 0, b"UNITS")

    task_id = syscalls.ai_enqueue(model=model, prompt=prompt, max_units=max_units)
    storage.set(META + task_id, caller)  # optional: track owner
    events.emit(b"AIQueued", {b"id": task_id, b"model": model, b"len": len(prompt)})
    return task_id

def consume(task_id: bytes) -> dict|None:
    rec = syscalls.read_result(task_id)
    if rec is None:
        return None  # still pending; user should call later
    # rec is a deterministic dictionary with fields (digest, units, ok, proof_ref, etc.)
    events.emit(b"AIResult", {b"id": task_id, b"ok": 1 if rec.get(b"ok") else 0})
    return rec

Notes
	•	Calls are deterministic: the result isn’t visible until a later block includes the proof.
	•	Cap input sizes and units to prevent abuse; charge fees via your app’s policy if needed.

⸻

5) Quantum RNG (beacon-mixed randomness for fair draws)

Use the capabilities-backed randomness function if available:
	•	syscalls.random(n: int) -> bytes returns n bytes mixed with the on-chain beacon
(in dev/local mode it falls back to a deterministic PRNG).

ABI
	•	draw(seed: bytes, n_participants: int) -> int → returns a winner index [0, n-1].

Code

from stdlib import syscalls, hash, abi

def _to_u256(b: bytes) -> int:
    # Interpret first 32 bytes big-endian
    bl = b[:32] if len(b) >= 32 else (b + b"\x00"*32)[:32]
    return int.from_bytes(bl, "big")

def draw(seed: bytes, n_participants: int) -> int:
    abi.require(n_participants > 0, b"BAD_N")
    # Mix caller-provided seed with beacon-backed randomness
    r = syscalls.random(32)  # beacon-mixed if available; deterministic stub in local mode
    h = hash.sha3_256(seed + r)
    winner = _to_u256(h) % n_participants
    return winner

Notes
	•	The caller-supplied seed makes the draw auditable (include in events if desired).
	•	For multi-winner draws, run a Fisher–Yates shuffle using successive syscalls.random(32) calls.

⸻

6) Manifests (sketch)

Each example can be packaged with a JSON manifest containing ABI & metadata (see vm_py/examples/*/manifest.json). The RPC/SDKs use these manifests for deploy/call.

Example manifest snippet (Counter)

{
  "name": "Counter",
  "version": "1.0.0",
  "abi": {
    "functions": [
      {"name": "inc", "inputs":[{"name":"caller","type":"address"}], "returns":"uint"},
      {"name": "get", "inputs": [], "returns": "uint"}
    ],
    "events": [
      {"name":"Inc","args":{"by":"address","new":"uint"}}
    ]
  },
  "resources": {"storage_keys_max": 8, "event_args_max": 4}
}


⸻

7) Testing & Simulation
	•	Local run: python -m vm_py.cli.run --manifest manifest.json --call inc --arg caller=anim1...
	•	Static gas: python -m vm_py.cli.inspect_ir --in out.ir
	•	Studio (browser): use studio-wasm simulateCall/compileSource APIs.

⸻

8) Security & Production Tips
	•	Enforce length caps on all bytes inputs; reject oversize payloads.
	•	Use abi.require with short ASCII reasons (≤ 16 bytes recommended).
	•	Emit minimal, structured events for state transitions.
	•	Prefer uint encodings via fixed-width big-endian for balances/counters.
	•	For AI/Quantum flows: store task metadata, verify consumption is idempotent.

Further reading
	•	docs/vm/SANDBOX.md, docs/vm/GAS_MODEL.md, docs/vm/DEBUGGING.md
	•	docs/spec/RECEIPTS_EVENTS.md
	•	capabilities/specs/SYSCALLS.md, capabilities/specs/COMPUTE.md
	•	randomness/specs/BEACON.md
