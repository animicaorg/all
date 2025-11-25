# DA Oracle — Post a Blob, then Verify It On-Chain

This tutorial shows a practical pattern to:
1) **Post a blob** to the Data Availability (DA) layer and obtain its **commitment** (NMT root).
2) **Verify the same content on-chain** by recomputing the commitment inside a contract using the deterministic `blob_pin(ns, data)` syscall, then **storing and checking** it later.

> The DA layer handles chunking, erasure coding, NMT commitments, and availability proofs. On-chain we **store only the commitment** (not the blob), and we verify content by **recomputing** the commitment from bytes. Availability proofs (DAS) are meant for **light/off-chain** verification.

---

## 0) Prereqs

- Devnet node + DA retrieval service running (see `da/retrieval/` and **Dev Quickstart**).
- Python SDK installed (`sdk/python`), plus VM CLI (`vm_py/cli`).
- A sample file to post (e.g. `da/fixtures/blob_small.bin`).

---

## 1) Post a Blob to DA (off-chain)

You can use the Python SDK DA client or the DA CLI. Here we’ll show the SDK way.

```python
# post_blob.py — post bytes to the DA layer; print commitment (NMT root)
from omni_sdk.config import Config
from omni_sdk.da.client import DAClient

RPC_URL = "http://127.0.0.1:8545"  # or DA service URL if separate
CHAIN_ID = 1
cfg = Config(rpc_url=RPC_URL, chain_id=CHAIN_ID)

da = DAClient(cfg)

# Choose a namespace (uint32/uint64 domain per your network policy)
NAMESPACE = 24
data = open("da/fixtures/blob_small.bin", "rb").read()

# Post returns a commitment (NMT root) and a receipt
resp = da.post_blob(namespace=NAMESPACE, data=data)
print("namespace:", NAMESPACE)
print("commitment:", resp["commitment"])      # 0x… NMT root
print("size_bytes:", resp["size"])
print("receipt_id:", resp["receipt"]["id"])

Alternative (CLI):

python -m da.cli.put_blob --ns 24 da/fixtures/blob_small.bin
# prints commitment & receipt JSON

Keep the commitment handy; the on-chain contract will recompute the same root from the exact same bytes to verify authenticity.

⸻

2) Contract: da_oracle.py

The contract exposes:
	•	register(ns: u64, data: bytes) -> bytes
Computes the DA commitment deterministically via syscalls.blob_pin(ns, data), stores it, and emits an event.
	•	verify(ns: u64, data: bytes) -> bool
Recomputes and checks equality against the stored commitment.
	•	get_commitment() -> bytes
Returns the last stored commitment.

# da_oracle.py — minimal on-chain DA oracle using deterministic blob commitment
# Public API:
#   - register(ns: u64, data: bytes) -> bytes
#   - verify(ns: u64, data: bytes) -> bool
#   - get_commitment() -> bytes

from stdlib import storage, events, abi, syscalls

def _k_commit() -> bytes: return b"da:commit"
def _k_ns() -> bytes:      return b"da:ns"
def _k_size() -> bytes:    return b"da:size"

def register(ns: int, data: bytes) -> bytes:
    """
    Deterministically compute the blob commitment (NMT root) from (ns, data)
    using the host-provided syscall. Store it for future verification.
    """
    abi.require(ns >= 0 and ns <= (1<<32)-1, b"ns out of range")
    abi.require(len(data) > 0 and len(data) <= 2_000_000, b"data size out of bounds")

    # blob_pin returns a deterministic commitment for (ns, data).
    # The host may enqueue/persist off-chain work; the return value is consensus-safe.
    commit = syscalls.blob_pin(ns, data)  # bytes (NMT root)
    abi.require(len(commit) > 0, b"invalid commitment")

    storage.set(_k_commit(), commit)
    storage.set(_k_ns(), ns.to_bytes(8, "big"))
    storage.set(_k_size(), len(data).to_bytes(8, "big"))

    events.emit(b"DARegistered", {b"ns": ns, b"size": len(data), b"commit": commit})
    return commit

def verify(ns: int, data: bytes) -> bool:
    """
    Recompute the commitment from (ns, data) and compare to the stored one.
    """
    stored = storage.get(_k_commit()) or b""
    if len(stored) == 0:
        return False
    current = syscalls.blob_pin(ns, data)
    return current == stored

def get_commitment() -> bytes:
    return storage.get(_k_commit()) or b""

Why blob_pin?
	•	It is a deterministic syscall that returns the canonical NMT root
for (namespace, data) under chain rules.
	•	It avoids re-implementing NMT/erasure logic in the VM and prevents gas abuse.
	•	The host can bridge to da/ to persist the blob if policy permits (devnet),
while the return value remains deterministic for consensus.

⸻

3) Compile & Deploy

python -m vm_py.cli.compile da_oracle.py --out /tmp/da_oracle.ir
python -m vm_py.cli.inspect_ir /tmp/da_oracle.ir

# deploy_and_register.py — deploy contract, then register & verify
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

ir = open("/tmp/da_oracle.ir","rb").read()
abi = {
  "functions":[
    {"name":"register","inputs":[{"name":"ns","type":"u64"},{"name":"data","type":"bytes"}],"returns":"bytes","mutates":True},
    {"name":"verify","inputs":[{"name":"ns","type":"u64"},{"name":"data","type":"bytes"}],"returns":"bool"},
    {"name":"get_commitment","inputs":[],"returns":"bytes"}
  ],
  "events":[{"name":"DARegistered","inputs":[
    {"name":"ns","type":"u64"},{"name":"size","type":"u64"},{"name":"commit","type":"bytes"}
  ]}]
}

# 1) Deploy
tx0 = build_deploy_tx(from_address=addr, manifest={"abi": abi}, code=ir,
                      gas_price=1, gas_limit=900_000, nonce=0)
rcpt0 = send_and_await_receipt(rpc, signer.sign_tx(tx0, CHAIN_ID), 30)
assert rcpt0["status"] == "SUCCESS", rcpt0
contract = rcpt0["contractAddress"]
print("DA Oracle deployed at:", contract)

client = ContractClient(rpc=rpc, address=contract, abi=abi)

# 2) Use the same bytes you posted to DA in step (1)
NAMESPACE = 24
data = open("da/fixtures/blob_small.bin","rb").read()

# Register (compute & store commitment on-chain)
tx1 = build_call_tx(from_address=addr, to_address=contract, abi=abi,
                    function="register", args=[NAMESPACE, data],
                    gas_price=1, gas_limit=500_000, nonce=1)
rcpt1 = send_and_await_receipt(rpc, signer.sign_tx(tx1, CHAIN_ID), 30)
print("register status:", rcpt1["status"])

# 3) Read commitment + verify equality by recomputing
commit = client.call("get_commitment", [])
print("stored commitment:", commit.hex())

ok = client.call("verify", [NAMESPACE, data])
print("verify:", ok)

Expected:
	•	register succeeds and emits DARegistered(ns, size, commit)
	•	get_commitment() returns the same commitment that DA printed in step (1)
	•	verify(ns, data) returns true

⸻

4) Best Practices
	•	Store only commitments on-chain. Put blob metadata (MIME, filename) and
retrieval hints off-chain (e.g. studio-services/artifacts or your app DB).
	•	Namespace hygiene. Define per-app namespaces to avoid collisions and to
enable policy routing/quotas on DA.
	•	Large blobs. Respect size caps from network policy; the blob_pin syscall
will enforce limits and charge gas for commitment work.
	•	Prove availability off-chain. Use the DA light client verifier to
check sampling proofs (da/sampling/light_client.py). On-chain full DAS is
intentionally not supported (cost prohibitive).

⸻

5) Troubleshooting
	•	Mismatched commitment: Ensure the exact same data bytes are used both
off-chain (DA post) and on-chain (register/verify). Any difference (line endings,
encoding) changes the root.
	•	Syscall not enabled: If blob_pin is feature-gated on your network, the
contract call will revert. Enable capabilities per capabilities/config.py.
	•	Blob too large: The syscall rejects over-limit inputs; chunk and pin
multiple blobs, then store their merkle of commitments on-chain.

⸻

6) What about “availability proofs” on-chain?

The chain header already binds the DA root for the block. Availability
sampling and full proofs are designed for light clients and off-chain
agents, not for contracts. Contracts should trust the header binding but
can require submitters to present the exact bytes and recompute the
commitment as shown here.

See also:
	•	docs/da/OVERVIEW.md
	•	docs/da/RETRIEVAL.md
	•	da/sampling/light_client.py
	•	capabilities/host/blob.py & capabilities/adapters/da.py

