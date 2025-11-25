# Animica-20 (A20) — Fungible Token with Permits

This tutorial walks you through implementing and using a minimal **Animica-20** token
(“A20”) on the Python-VM, including classic ERC-20-style flows and a forward-compatible
**permit** mechanism.

> **Heads-up on "permit":**  
> True off-chain *permit-by-signature* (spender submits a signature created by owner)
> requires a generic signature-verify syscall. In this milestone we ship a **safe,
> on-chain `permit(...)`** (owner calls it directly, ensures nonce/deadline semantics,
> events, and compatibility). When the `verify_sig` syscall lands, the same ABI can
> expose `permit_by_sig(...)` for gasless approvals without breaking clients.

---

## 0) Prereqs

- Devnet node & miner running (see **Dev Quickstart**).
- `vm_py` and SDKs installed (Python and/or TypeScript).
- You can simulate locally with **studio-wasm** before deploying.

---

## 1) Contract — Minimal A20 with Permits

Save as `a20.py`:

```python
# Animica-20 (A20) reference implementation (Python-VM)
# - balances, allowances, classic transfer/approve/transferFrom
# - on-chain permit(owner, spender, value, deadline) with nonces & events
# - forward-compatible stub for permit_by_sig(...) (disabled until verify_sig is available)

from stdlib import storage, events, abi, hash

# ---- Storage keys helpers ----------------------------------------------------

def _k(suffix: bytes) -> bytes: return b"a20:" + suffix
def _kb(addr: bytes) -> bytes:   return b"a20:bal:" + addr
def _ka(owner: bytes, spender: bytes) -> bytes: return b"a20:alw:" + owner + b":" + spender
def _kn(owner: bytes) -> bytes:  return b"a20:non:" + owner

# ---- Metadata (configurable at init) ----------------------------------------

def name() -> bytes:    return storage.get(b"a20:name") or b"Animica Token"
def symbol() -> bytes:  return storage.get(b"a20:symbol") or b"ANM"
def decimals() -> int:  return int(storage.get(b"a20:dec") or 18)

# ---- Views ------------------------------------------------------------------

def total_supply() -> int:
    v = storage.get(b"a20:tot")
    return int(v or 0)

def balance_of(owner: bytes) -> int:
    return int(storage.get(_kb(owner)) or 0)

def allowance(owner: bytes, spender: bytes) -> int:
    return int(storage.get(_ka(owner, spender)) or 0)

def nonces(owner: bytes) -> int:
    return int(storage.get(_kn(owner)) or 0)

# ---- Internal ---------------------------------------------------------------

def _emit_transfer(frm: bytes, to: bytes, amt: int) -> None:
    events.emit(b"Transfer", {b"from": frm, b"to": to, b"value": amt})

def _emit_approval(owner: bytes, spender: bytes, amt: int) -> None:
    events.emit(b"Approval", {b"owner": owner, b"spender": spender, b"value": amt})

def _emit_permit(owner: bytes, spender: bytes, amt: int, deadline: int, nonce: int) -> None:
    events.emit(b"Permit", {
        b"owner": owner, b"spender": spender, b"value": amt,
        b"deadline": deadline, b"nonce": nonce
    })

def _set_balance(addr: bytes, new_bal: int) -> None:
    abi.require(new_bal >= 0, b"negative balance")
    storage.set(_kb(addr), new_bal)

def _set_allowance(owner: bytes, spender: bytes, amt: int) -> None:
    abi.require(amt >= 0, b"negative allowance")
    storage.set(_ka(owner, spender), amt)

# ---- Init / Mint ------------------------------------------------------------

def init(meta_name: bytes, meta_symbol: bytes, meta_decimals: int, initial_to: bytes, initial_supply: int) -> None:
    """
    One-time initializer. Call immediately after deploy.
    """
    abi.require(storage.get(b"a20:inited") is None, b"already initialized")
    abi.require(meta_decimals >= 0 and meta_decimals <= 36, b"bad decimals")

    storage.set(b"a20:name", meta_name)
    storage.set(b"a20:symbol", meta_symbol)
    storage.set(b"a20:dec", meta_decimals)

    storage.set(b"a20:tot", initial_supply)
    _set_balance(initial_to, initial_supply)
    storage.set(b"a20:inited", b"1")
    _emit_transfer(b"\x00"*32, initial_to, initial_supply)

# ---- Core transfers ---------------------------------------------------------

def transfer(to: bytes, amt: int) -> bool:
    sender = abi.caller()  # provided by VM
    _transfer(sender, to, amt)
    return True

def transfer_from(frm: bytes, to: bytes, amt: int) -> bool:
    sender = abi.caller()
    allowed = allowance(frm, sender)
    abi.require(allowed >= amt, b"allowance too low")
    _transfer(frm, to, amt)
    _set_allowance(frm, sender, allowed - amt)
    _emit_approval(frm, sender, allowed - amt)
    return True

def _transfer(frm: bytes, to: bytes, amt: int) -> None:
    abi.require(amt >= 0, b"bad amount")
    bfrm = balance_of(frm)
    abi.require(bfrm >= amt, b"insufficient")
    bto = balance_of(to)
    _set_balance(frm, bfrm - amt)
    _set_balance(to, bto + amt)
    _emit_transfer(frm, to, amt)

# ---- Approvals & Permits ----------------------------------------------------

def approve(spender: bytes, amt: int) -> bool:
    owner = abi.caller()
    _set_allowance(owner, spender, amt)
    _emit_approval(owner, spender, amt)
    return True

def permit(owner: bytes, spender: bytes, value: int, deadline: int) -> bool:
    """
    On-chain permit with anti-replay (nonce) and deadline semantics.
    Caller must be the owner in this milestone (gas-paying).
    """
    abi.require(abi.caller() == owner, b"caller != owner (use permit_by_sig in future)")
    abi.require(abi.block_timestamp() <= deadline, b"permit expired")

    nonce = nonces(owner)
    storage.set(_kn(owner), nonce + 1)

    _set_allowance(owner, spender, value)
    _emit_permit(owner, spender, value, deadline, nonce)
    _emit_approval(owner, spender, value)
    return True

def permit_by_sig(owner: bytes, spender: bytes, value: int, deadline: int, signature: bytes) -> bool:
    """
    Forward-compatible stub. Once 'verify_sig' syscall is available, this method will:
      - reconstruct SignBytes(domain="A20-PERMIT", owner, spender, value, deadline, nonce, chainId, contract)
      - verify owner's PQ signature over those bytes
      - on success: increment nonce, set allowance, emit events
    """
    abi.revert(b"permit_by_sig not enabled on this network yet")

ABI (manifest excerpt) — events & functions (illustrative):

{
  "abi": {
    "events": [
      {"name":"Transfer","inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"value","type":"u128"}]},
      {"name":"Approval","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"},{"name":"value","type":"u128"}]},
      {"name":"Permit","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"},{"name":"value","type":"u128"},{"name":"deadline","type":"u64"},{"name":"nonce","type":"u64"}]}
    ],
    "functions": [
      {"name":"init","inputs":[{"name":"name","type":"bytes"},{"name":"symbol","type":"bytes"},{"name":"decimals","type":"u32"},{"name":"initial_to","type":"address"},{"name":"initial_supply","type":"u128"}],"mutates":true},
      {"name":"name","inputs":[],"returns":"bytes"},
      {"name":"symbol","inputs":[],"returns":"bytes"},
      {"name":"decimals","inputs":[],"returns":"u32"},
      {"name":"total_supply","inputs":[],"returns":"u128"},
      {"name":"balance_of","inputs":[{"name":"owner","type":"address"}],"returns":"u128"},
      {"name":"allowance","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"returns":"u128"},
      {"name":"nonces","inputs":[{"name":"owner","type":"address"}],"returns":"u64"},
      {"name":"transfer","inputs":[{"name":"to","type":"address"},{"name":"amount","type":"u128"}],"returns":"bool","mutates":true},
      {"name":"approve","inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"u128"}],"returns":"bool","mutates":true},
      {"name":"transfer_from","inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"amount","type":"u128"}],"returns":"bool","mutates":true},
      {"name":"permit","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"},{"name":"value","type":"u128"},{"name":"deadline","type":"u64"}],"returns":"bool","mutates":true},
      {"name":"permit_by_sig","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"},{"name":"value","type":"u128"},{"name":"deadline","type":"u64"},{"name":"signature","type":"bytes"}],"returns":"bool","mutates":true}
    ]
  }
}


⸻

2) Compile

python -m vm_py.cli.compile a20.py --out /tmp/a20.ir

(Optional) quick static gas estimate:

python -m vm_py.cli.inspect_ir /tmp/a20.ir


⸻

3) Deploy & Init (Python SDK)

# deploy_a20.py
from pathlib import Path
import json
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

mn_owner = new_mnemonic()
owner = Dilithium3Signer.from_mnemonic(mn_owner)
addr_owner = address_from_pubkey("dilithium3", owner.public_key())

ir = Path("/tmp/a20.ir").read_bytes()
manifest = {
  "abi": json.loads(Path("a20.manifest.json").read_text())["abi"] if Path("a20.manifest.json").exists()
         else json.loads("""{"functions":[],"events":[]}""")  # replace with your real manifest
}

# deploy
tx0 = build_deploy_tx(from_address=addr_owner, manifest=manifest, code=ir,
                      gas_price=1, gas_limit=600_000, nonce=0)
rcpt0 = send_and_await_receipt(rpc, owner.sign_tx(tx0, CHAIN_ID), 30)
assert rcpt0["status"] == "SUCCESS", rcpt0
token_addr = rcpt0["contractAddress"]
print("A20 at:", token_addr)

# init (name, symbol, decimals, initial_to, initial_supply)
abi = manifest["abi"]
client = ContractClient(rpc=rpc, address=token_addr, abi=abi)

tx1 = build_call_tx(from_address=addr_owner, to_address=token_addr, abi=abi,
                    function="init",
                    args=[b"Animica Token", b"ANM", 18, addr_owner, 1_000_000_000_000],
                    gas_price=1, gas_limit=300_000, nonce=1)
rcpt1 = send_and_await_receipt(rpc, owner.sign_tx(tx1, CHAIN_ID), 30)
print("init:", rcpt1["status"])

Replace the manifest loading with your generated manifest (from your build pipeline or Studio).

⸻

4) Transfer & Approvals

Approve/transferFrom (TypeScript)

import { Config, HttpRpc, Wallet, Signer, Tx, Contracts } from "@animica/sdk";

const cfg = new Config({ rpcUrl: "http://127.0.0.1:8545", chainId: 1 });
const rpc = new HttpRpc(cfg);

const owner = await Signer.dilithium3FromMnemonic(Wallet.newMnemonic());
const spender = await Signer.dilithium3FromMnemonic(Wallet.newMnemonic());
const addrOwner = Wallet.addressFromPubkey("dilithium3", await owner.publicKey());
const addrSpender = Wallet.addressFromPubkey("dilithium3", await spender.publicKey());

const token = "<DEPLOYED_TOKEN_ADDRESS>";
const abi = /* your A20 ABI */;
const client = new Contracts.Client(rpc, token, abi);

// approve
const txA = Tx.buildCall({
  from: addrOwner, to: token, abi,
  function: "approve", args: [addrSpender, 123_000n],
  gasPrice: 1n, gasLimit: 100_000n, nonce: 2n
});
await Tx.sendAndAwaitReceipt(rpc, await owner.signTx(txA, 1));

// transferFrom (spender pulls)
const to = addrSpender; // demo: pull to self
const txB = Tx.buildCall({
  from: addrSpender, to: token, abi,
  function: "transfer_from", args: [addrOwner, to, 10_000n],
  gasPrice: 1n, gasLimit: 120_000n, nonce: 0n
});
const rcptB = await Tx.sendAndAwaitReceipt(rpc, await spender.signTx(txB, 1));
console.log("transfer_from:", rcptB.status);


⸻

5) Permit (Milestone-ready, on-chain)

Owner sets an allowance for a spender with nonce/deadline semantics:

# owner calls on-chain (gas-paying), keeps nonce discipline
txp = build_call_tx(
    from_address=addr_owner,
    to_address=token_addr,
    abi=abi,
    function="permit",
    args=[addr_owner, addr_spender, 50_000, 2_000_000_000],  # deadline: unix ts
    gas_price=1, gas_limit=120_000, nonce=2
)
rcptp = send_and_await_receipt(rpc, owner.sign_tx(txp, CHAIN_ID), 30)
print("permit:", rcptp["status"])

	•	Contract increments nonces(owner) and emits both Permit and Approval.
	•	Spender can then transfer_from(owner, to, value) until allowance is spent/changed.

⸻

6) Permit-by-Signature (coming soon)

When the VM exposes verify_sig, you will:
	1.	Construct deterministic SignBytes with domain "A20-PERMIT":

H = SignBytes(
  domain="A20-PERMIT",
  owner, spender, value, deadline,
  nonce=nonces(owner),
  chainId, contractAddress
)


	2.	Owner signs H with their PQ key (Dilithium3/SPHINCS+).
	3.	Spender submits permit_by_sig(owner, spender, value, deadline, signature).
	4.	Contract verifies signature, increments nonce, sets allowance, emits events.

This preserves client compatibility with today’s on-chain permit(...).

⸻

7) Events & Indexing
	•	Transfer(from, to, value)
	•	Approval(owner, spender, value)
	•	Permit(owner, spender, value, deadline, nonce)

Use SDK ContractClient.decode_event(log) to parse receipts, or your indexer to power UI.

⸻

8) Testing Checklist
	•	Init only once; subsequent init must revert.
	•	transfer reverts on insufficient balance; balances never negative.
	•	approve overwrites allowance (or implement increase/decrease variants).
	•	transfer_from reduces allowance and emits Approval.
	•	permit enforces deadline and increments nonces(owner).
	•	(Future) permit_by_sig rejects bad/expired signatures and wrong nonces.

⸻

9) Security Notes
	•	Avoid uint under/over-flows (VM ints are big, but keep explicit checks).
	•	Emit events consistently to ease auditing and wallets’ UX.
	•	Consider mint/burn roles (omitted here) and access controls.
	•	Reentrancy is minimal in this model, but keep state-before-effects discipline.

⸻

10) Next Steps
	•	Add mint/burn with role checks.
	•	Gas-optimized batch transfers.
	•	Richer metadata (EIP-1046-style token URIs) pinned via DA blobs.
	•	Wire a UI in studio-web to exercise approvals & transfers.

Happy building! 🪙
