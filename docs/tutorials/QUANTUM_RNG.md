# Quantum RNG — Consume the Beacon mixed with QRNG

This tutorial shows how to **consume the chain randomness beacon** that’s
constructed each round as:

**commit–reveal → VDF → (optional) QRNG mix → BeaconOut**

Contracts can **safely** derive deterministic randomness from this beacon and
use it for lotteries, leader election, or fair sampling—*without relying on any
off-chain oracle*. The optional QRNG component (if enabled by the network) is
already mixed **inside** the beacon, so your contract just reads the final,
canonical bytes.

> Determinism rule: contracts **never** read external entropy directly.
> All mixing happens in the beacon finalize step (see `randomness/beacon/finalize.py`).

---

## 0) Prereqs

- Devnet node + miner running (see **Dev Quickstart**).
- Randomness subsystem enabled (defaults are fine).
- Python SDK installed (`sdk/python`).
- VM CLI available (`vm_py/cli`).

---

## 1) Contract: `quantum_rng_consumer.py`

This small contract:
- Reads the **current beacon output**.
- Exposes a helper to draw N random bytes **derived from the beacon** with
  domain separation (context bytes).
- Stores the last draw so you can inspect it later.

The VM’s syscall surface provides:
- `syscalls.random(n: int) -> bytes`: deterministically derived bytes that
  include the beacon (when available) and a per-call PRF (see
  `capabilities/host/random.py` and `randomness/adapters/execution.py`).
- `syscalls.rand_beacon() -> bytes` (optional/feature-gated): returns the raw
  current beacon output (or empty if not yet finalized for the round).

```python
# quantum_rng_consumer.py — deterministic beacon/QRNG consumer for the Python-VM
# Exposes:
#   - beacon() -> bytes                  # current beacon bytes (may be empty early in round)
#   - draw(n: u64, ctx: bytes) -> bytes  # derive n bytes with domain-separated context
#   - last_draw() -> bytes               # persisted last random draw
#   - last_ctx() -> bytes                # persisted context of last draw

from stdlib import storage, events, abi, hash, syscalls

def _k_last_draw() -> bytes: return b"rng:last_draw"
def _k_last_ctx()  -> bytes: return b"rng:last_ctx"

def beacon() -> bytes:
    """
    Read the canonical beacon bytes for the *current* round, if the finalize
    step has occurred. If called too early it may return b"".
    """
    try:
        b = syscalls.rand_beacon()  # may not exist on all networks; returns bytes or b""
        return b or b""
    except Exception:
        # Fallback: not all networks expose raw-beacon; return empty to signal "not ready".
        return b""

def draw(n: int, ctx: bytes) -> bytes:
    """
    Derive n random bytes for the given context. Uses syscalls.random(n) which
    is a deterministic PRF seeded from the beacon (if available) + chain state.
    We additionally domain-separate with caller-provided context.
    """
    abi.require(n > 0 and n <= 4096, b"n out of bounds")
    abi.require(len(ctx) > 0 and len(ctx) <= 64, b"bad context length")

    base = syscalls.random(n)           # deterministic, beacon-seeded when available
    bcn  = beacon()                     # may be empty if round not finalized yet
    # Domain separation: H("DRW" | ctx | base | beacon)
    out  = hash.keccak256(b"DRW" + ctx + base + bcn)

    storage.set(_k_last_draw(), out)
    storage.set(_k_last_ctx(), ctx)
    events.emit(b"RandomDraw", {b"ctx": ctx, b"n": n, b"hasBeacon": 1 if len(bcn) > 0 else 0})
    return out

def last_draw() -> bytes:
    return storage.get(_k_last_draw()) or b""

def last_ctx() -> bytes:
    return storage.get(_k_last_ctx()) or b""

Notes
	•	syscalls.random(n) is the portable way to get randomness in contracts.
It is deterministic and includes the beacon (and QRNG mix, if enabled) once
the round has finalized.
	•	syscalls.rand_beacon() is optional and may be feature-gated. The
contract safely handles its absence by returning b"".

⸻

2) Compile the Contract

python -m vm_py.cli.compile quantum_rng_consumer.py --out /tmp/quantum_rng_consumer.ir
python -m vm_py.cli.inspect_ir /tmp/quantum_rng_consumer.ir


⸻

3) Deploy & Try It (Python SDK)

The script below deploys the contract, then:
	1.	Calls beacon() to inspect whether the current round has finalized.
	2.	Calls draw(32, b"lottery-epoch-1") to get 32 bytes for a lottery context.

# deploy_and_draw_rng.py
import time
from omni_sdk.config import Config
from omni_sdk.rpc.http import HttpRpc
from omni_sdk.wallet.mnemonic import new_mnemonic
from omni_sdk.wallet.signer import Dilithium3Signer
from omni_sdk.address import address_from_pubkey
from omni_sdk.tx.build import build_deploy_tx, build_call_tx
from omni_sdk.tx.send import send_and_await_receipt
from omni_sdk.contracts.client import ContractClient

RPC_URL = "http://127.0.0.1:8545"
CHAIN_ID = 1

cfg = Config(rpc_url=RPC_URL, chain_id=CHAIN_ID)
rpc = HttpRpc(cfg)

mn = new_mnemonic()
signer = Dilithium3Signer.from_mnemonic(mn)
addr = address_from_pubkey("dilithium3", signer.public_key())

ir = open("/tmp/quantum_rng_consumer.ir","rb").read()
abi = {
  "functions": [
    {"name":"beacon","inputs":[],"returns":"bytes"},
    {"name":"draw","inputs":[{"name":"n","type":"u64"},{"name":"ctx","type":"bytes"}],"returns":"bytes","mutates":True},
    {"name":"last_draw","inputs":[],"returns":"bytes"},
    {"name":"last_ctx","inputs":[],"returns":"bytes"}
  ],
  "events":[
    {"name":"RandomDraw","inputs":[{"name":"ctx","type":"bytes"},{"name":"n","type":"u64"},{"name":"hasBeacon","type":"u64"}]}
  ]
}

# 1) Deploy
tx0 = build_deploy_tx(from_address=addr, manifest={"abi": abi}, code=ir,
                      gas_price=1, gas_limit=700_000, nonce=0)
rcpt0 = send_and_await_receipt(rpc, signer.sign_tx(tx0, CHAIN_ID), 30)
assert rcpt0["status"] == "SUCCESS", rcpt0
contract = rcpt0["contractAddress"]
print("Quantum RNG Consumer deployed at:", contract)

client = ContractClient(rpc=rpc, address=contract, abi=abi)

# 2) Check beacon bytes (may be empty early in a round)
b = client.call("beacon", [])
print("beacon len:", len(b) if isinstance(b, (bytes, bytearray)) else 0)

# 3) Draw 32 bytes for your lottery epoch
n = 32
ctx = b"lottery-epoch-1"
tx1 = build_call_tx(from_address=addr, to_address=contract, abi=abi,
                    function="draw", args=[n, ctx],
                    gas_price=1, gas_limit=220_000, nonce=1)
rcpt1 = send_and_await_receipt(rpc, signer.sign_tx(tx1, CHAIN_ID), 30)
print("draw status:", rcpt1["status"])

draw = client.call("last_draw", [])
print("random bytes (hex):", draw.hex() if isinstance(draw, (bytes, bytearray)) else draw)

Tip: If you want draws tied to round boundaries, call draw only after
you observe a new round (or new block after beacon finalize). Derive contexts
with the round number in them (e.g., b"lottery-epoch-%d" % round_id).

⸻

4) Best Practices
	•	Context domain separation: Always include a unique context (e.g., contract
address, purpose, round/epoch) so outputs are unlinkable across use-cases.
	•	Round awareness: If your logic requires the next round’s randomness,
trigger consumption after the finalize event height. (Your app/SDK can watch
rand.getRound via RPC or listen to events if you publish them on finalize.)
	•	No external entropy: Never feed external random bytes into a contract.
All entropy must come from the beacon (already mixed with QRNG if enabled).
	•	Auditability: For user-facing draws/lotteries, emit events containing the
context and output hash so results are easy to verify off-chain.

⸻

5) Troubleshooting
	•	Empty beacon bytes: You’re calling beacon() before the round finalized.
Wait a block or two (depending on your randomness/config.py windows).
	•	Non-reproducible draws in tests: Ensure tests mine at deterministic
intervals and pin chain parameters; use the same context and block height.

⸻

6) Next Steps
	•	Build a verifiable on-chain raffle using draw + merkle claims.
	•	Use beacon-derived randomness to shuffle committee membership.
	•	In a contract suite, derive per-game seeds (ctx = game_id || round_id)
to make simulated games fair and reproducible.

See also:
	•	docs/randomness/OVERVIEW.md
	•	docs/randomness/BEACON_API.md
	•	randomness/specs/LIGHT_CLIENT.md

Happy mixing! 🎲
