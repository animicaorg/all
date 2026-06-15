#!/usr/bin/env python3
"""Build the Animica knowledge instruction dataset (general + a full Animica
smart-contract curriculum) and register it as the ENA starter dataset.

    /root/animica/.venv/bin/python build_animica_dataset.py
"""
from __future__ import annotations
import json, os, sqlite3, urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Reusable contract code blocks (accurate to the vm_py stdlib + runtime API).
# ---------------------------------------------------------------------------

COUNTER = '''from stdlib import abi, events, storage  # provided by the Animica Python-VM

KEY = b"counter"
INT256_MIN, INT256_MAX = -(1 << 255), (1 << 255) - 1

def _get() -> int:
    b = storage.get(KEY)
    return int.from_bytes(b, "big", signed=True) if b else 0

def _set(v: int) -> None:
    abi.require(INT256_MIN <= v <= INT256_MAX, b"overflow")
    storage.set(KEY, int(v).to_bytes(32, "big", signed=True))

def get() -> int:            # stateMutability: view
    return _get()

def inc(by: int) -> int:     # stateMutability: nonpayable
    abi.require(by >= 1, b"by_must_be_positive")
    v = _get() + by
    _set(v)
    events.emit(b"Incremented", {"by": by, "value": v})
    return v

def reset(to: int) -> int:
    _set(to)
    events.emit(b"Reset", {"value": to})
    return to'''

TOKEN = '''from stdlib import abi, events, storage

KEY_SUPPLY = b"supply"
def _bal_key(addr: bytes) -> bytes: return b"bal:" + addr

def _get_int(key: bytes) -> int:
    b = storage.get(key)
    return int.from_bytes(b, "big") if b else 0

def _set_int(key: bytes, v: int) -> None:
    abi.require(0 <= v < (1 << 256), b"uint256_range")
    storage.set(key, int(v).to_bytes(32, "big"))

def setup(owner: bytes, supply: int) -> None:           # one-time constructor
    abi.require(_get_int(KEY_SUPPLY) == 0, b"already_initialized")
    abi.require(supply > 0, b"bad_supply")
    _set_int(KEY_SUPPLY, supply)
    _set_int(_bal_key(owner), supply)
    events.emit(b"Mint", {"to": owner, "amount": supply})

def totalSupply() -> int:                               # view
    return _get_int(KEY_SUPPLY)

def balanceOf(addr: bytes) -> int:                      # view
    return _get_int(_bal_key(addr))

def transfer(to: bytes, amount: int) -> bool:           # nonpayable
    frm = abi.caller()
    abi.require(amount > 0, b"bad_amount")
    bal = _get_int(_bal_key(frm))
    abi.require(bal >= amount, b"insufficient_balance")
    _set_int(_bal_key(frm), bal - amount)
    _set_int(_bal_key(to), _get_int(_bal_key(to)) + amount)
    events.emit(b"Transfer", {"from": frm, "to": to, "amount": amount})
    return True'''

ESCROW = '''from stdlib import abi, events, storage, treasury

S_INIT, S_RELEASED = b"INIT", b"RELEASED"
K_STATE, K_AMOUNT, K_DEP, K_BEN = b"state", b"amount", b"dep", b"ben"

def setup(depositor: bytes, beneficiary: bytes, amount: int) -> None:
    abi.require(storage.get(K_STATE) is None, b"already_initialized")
    abi.require(amount > 0, b"bad_amount")
    storage.set(K_DEP, depositor)
    storage.set(K_BEN, beneficiary)
    storage.set(K_AMOUNT, int(amount).to_bytes(32, "big"))
    storage.set(K_STATE, S_INIT)

def release() -> None:                       # only the beneficiary, once funded
    abi.require(abi.caller() == storage.get(K_BEN), b"only_beneficiary")
    abi.require(storage.get(K_STATE) == S_INIT, b"not_releasable")
    need = int.from_bytes(storage.get(K_AMOUNT), "big")
    me = abi.self_address()
    abi.require(treasury.balance(me) >= need, b"underfunded")
    treasury.transfer(me, storage.get(K_BEN), need)
    storage.set(K_STATE, S_RELEASED)
    events.emit(b"Released", {"to": storage.get(K_BEN), "amount": need})'''

AI_CONTRACT = '''from stdlib import abi, events, storage, ena

K_LAST = b"last_request"

def classify(text: bytes) -> bytes:          # nonpayable: enqueue an ENA job
    rid = ena.request(model_version=b"ena-active", task_type=b"classify",
                      input_bytes=text, fee_limit=10000)
    storage.set(K_LAST, rid)
    events.emit(b"Requested", {"request_id": rid})
    return rid

def result() -> bytes:                       # view: read the settled result hash
    rid = storage.get(K_LAST)
    abi.require(rid is not None, b"no_request")
    if ena.get_status(rid) != b"completed":
        abi.revert(b"not_ready")
    return ena.get_result_hash(rid)'''

PAIRS: list[tuple[str, str]] = []
def add(p, r): PAIRS.append((p, r))

# ===========================================================================
# SECTION 1 — Animica overview & chain
# ===========================================================================
add("What is Animica?",
    "Animica is a post-quantum Layer-1 blockchain combined with an open AI compute network. It pairs a Proof-of-Work chain secured by post-quantum signatures with ENA and AICF, which let people contribute compute to run AI inference and training and earn the native token, ANM. It also runs deterministic Python smart contracts on its built-in VM.")
add("What is the native token of Animica?",
    "ANM. It has 9 decimals, so 1 ANM equals 1,000,000,000 base (nano) units. ANM pays transaction fees and gas, rewards miners and AI contributors, and funds AI jobs and contracts.")
add("What makes Animica post-quantum?",
    "Account signatures use post-quantum schemes instead of ECDSA: ML-DSA-65 (FIPS 204), Dilithium3, and SPHINCS+ (SHAKE-128s). Contracts can also verify these signatures on-chain via the pq_verify stdlib.")
add("What is Animica's chain ID?", "The Animica mainnet chain ID is 1.")
add("What consensus does Animica use?",
    "Proof-of-Work. Blocks are secured by SHA3-based hashing; block times depend on the current network hashrate.")
add("What hash function does Animica use?",
    "SHA3-256 (the NIST standard, not Keccak) for addresses, transaction hashing, and receipts. Contracts can also use keccak256 via the hash stdlib.")
add("How are Animica blocks and messages encoded?",
    "With canonical CBOR. Floating-point values are rejected at the wire and consensus layers so every node encodes state deterministically.")
add("Why does Animica reject floats in consensus encoding?",
    "Floating-point is not byte-for-byte deterministic across platforms. To keep every node's canonical CBOR identical, Animica rounds or rejects floats. This is also why smart contracts must never use floats.")
add("Does Animica have data availability?",
    "Yes. Animica has a data-availability (DA) layer. From the CLI use `animica da submit` and `animica da retrieve`; contracts can pin blobs with the syscalls.blob_pin stdlib.")

# ===========================================================================
# SECTION 2 — addresses, transactions, RPC
# ===========================================================================
add("What does an Animica address look like?",
    "Animica addresses are bech32m strings beginning with `anim1` (for example anim1zqp...). They encode a 2-byte algorithm id plus the 32-byte SHA3-256 digest of the public key.")
add("How is an Animica address derived?",
    "address = bech32m(\"anim\", u16be(alg_id) || sha3_256(public_key)). The 34-byte payload is a 2-byte big-endian algorithm id followed by the 32-byte SHA3-256 digest of the raw public key.")
add("What are the Animica signature algorithm ids?",
    "ml_dsa_65 = 0x1003 (4099), dilithium3 = 0x1001 (4097), sphincs_shake_128s = 0x1002 (4098). Contract addresses use alg_id 0x0000.")
add("Why must Animica addresses use bech32m and not bech32?",
    "bech32m (BIP-350, checksum constant 0x2bc830a3) and plain bech32 differ only in the 6-character checksum. Plain bech32 yields a valid-looking address with the wrong tail, so cross-client validation silently fails. Animica addresses must use bech32m.")
add("What fields does an Animica transaction have?",
    "chain_id, nonce, from_addr (32 bytes), to_addr (32 bytes), value (base units), fee, data (calldata), and an optional memo (up to 256 bytes).")
add("How do I send ANM from the CLI?",
    "animica tx send --from <addr-or-index> --to anim1... --value 1.5 . The node signs with the sender's post-quantum key and broadcasts the transaction.")
add("How can I look up a transaction over RPC?",
    "Call tx.getTransaction with the transaction hash (passed positionally), or tx.getTransactionStatus for confirmation status. The explorer also shows transactions.")
add("What is the Animica public RPC endpoint?",
    "rpc.animica.org/rpc serves JSON-RPC 2.0 over HTTPS via POST. Chain id is 1. Common methods: chain.getHead, tx.send, tx.getTransaction, state.getBalance.")
add("How do I check an account balance over RPC?",
    "Call state.getBalance (or account.getBalance) with the address, or look the address up on explorer.animica.org.")

# ===========================================================================
# SECTION 3 — CLI, node, wallet, mining
# ===========================================================================
add("How do I install the Animica CLI?",
    "Run: pip install --upgrade animica . For AI training extras (PyTorch, transformers, PEFT, TRL, bitsandbytes), install: pip install --upgrade \"animica[gpu]\".")
add("What command groups does the animica CLI have?",
    "node, wallet, key, tx, chain, rpc, mempool, miner, da, network, peer, sync, contract, ena, aicf, and bittensor. Run `animica --help` to list them.")
add("How do I start an Animica node?",
    "animica node up (mainnet by default). Check it with `animica node status` and `animica sync status`.")
add("How do I create a wallet from the CLI?",
    "animica wallet new (or use the `animica key` commands). List accounts with `animica wallet list`.")
add("What wallets can I use with Animica?",
    "A browser extension (injects window.animica), a Qt desktop wallet (Windows/macOS/Linux), an Android wallet, and the web wallet at wallet.animica.org. Get them from animica.org/wallet.")
add("How does a website connect to the Animica browser wallet?",
    "The extension injects a provider at window.animica and fires `animica#initialized` when ready. Connect with window.animica.request({ method: 'animica_requestAccounts' }) and send with 'animica_sendTransaction'.")
add("How do I mine on Animica?",
    "Install the CLI and run: animica miner dual-mine <your-anim1-address> . Pool details and downloads are at pool.animica.org; the stratum endpoint is on port 3333.")
add("What is dual mining on Animica?",
    "It runs the Animica ANM miner and a Monero (XMR) miner together, splitting effort so you earn ANM while also mining XMR. The CLI auto-resolves the miner binaries.")

# ===========================================================================
# SECTION 4 — ENA & AICF
# ===========================================================================
add("What is ENA?",
    "ENA is Animica's open AI training and inference network. Contributors run real AI jobs — data curation, evaluation, retrieval, and fine-tuning — and earn ANM. Every verified job mints an on-chain receipt. The site is ena.animica.org.")
add("How do I contribute compute to ENA?",
    "Install the CLI and run: animica ena worker start --worker-id <id> --endpoint https://ena.animica.org/api . Your machine claims jobs, runs them, and earns ANM per verified job.")
add("What training methods does ENA support?",
    "SFT, LoRA, QLoRA (4-bit), DPO, and CPU distillation via the python_transformers backend, plus a `command` backend to launch an external trainer.")
add("How do I request an AI job on ENA?",
    "On ena.animica.org connect your Animica wallet, choose a job type, and pay ANM to fund it. The payment is bound to the job on-chain (job_hash in the memo); the contributor fleet runs it.")
add("How do I get the ENA starter dataset?",
    "Download it and load it into ENA: `curl -O https://ena.animica.org/animica-knowledge.jsonl` then `animica ena datasets contribute animica-knowledge.jsonl`. To fine-tune on it: `animica ena train prepare --dataset animica-knowledge.jsonl --out manifests/run.json --base-model Qwen/Qwen2.5-1.5B --backend python_transformers --method lora --auto-split`.")
add("What is AICF?",
    "AICF, the AI Compute Fund, is Animica's off-chain compute coordination layer. It matches AI and quantum jobs to providers, ties completed work to on-chain proofs, and prices and settles rewards under transparent policy and SLA rules.")
add("What is the difference between ENA and AICF?",
    "AICF is the accounting and coordination layer — queue, economics, proofs, settlement. ENA is the execution and orchestration layer — the agent, retrieval, useful-work, and training that runs the jobs and produces the receipts AICF settles.")

# ===========================================================================
# SECTION 5 — SMART CONTRACTS: model & determinism
# ===========================================================================
add("Does Animica support smart contracts?",
    "Yes. Animica runs deterministic Python smart contracts on a built-in Python VM (vm_py). You write a contract as a Python module, compile it to an artifact, deploy it, then call or send transactions to its methods with the `animica contract` CLI.")
add("What language are Animica smart contracts written in?",
    "Python. A contract is an ordinary Python module whose public (non-underscore) top-level functions become the callable contract methods. It runs on Animica's deterministic Python-VM, not the EVM.")
add("How does an Animica contract differ from a normal Python script?",
    "It must be fully deterministic: no wall-clock time, no system randomness, no network or filesystem, no floats. State lives only in VM storage, and side effects happen only through the VM stdlib (storage, events, treasury, etc.). Use abi.block_timestamp() instead of time, and syscalls.get_beacon()/random_bytes() instead of random.")
add("What modules can an Animica contract import?",
    "Only the VM stdlib: `from stdlib import abi, events, storage, treasury, hash, hash_work, pq_verify, syscalls, ena`. These are the deterministic syscall surface; arbitrary imports (os, time, random, requests) are not available.")
add("What are the rules for deterministic Animica contracts?",
    "No floats; no wall-clock (use abi.block_timestamp()); no system randomness (use syscalls.get_beacon() or random_bytes()); no I/O; bounded loops only; integers behave as exact big integers but you should keep on-chain values within documented bounds (e.g. signed/unsigned 256-bit) and encode them deterministically.")
add("How are public methods defined in an Animica contract?",
    "Top-level functions whose names do not start with an underscore are the contract's public methods. Underscore-prefixed functions (e.g. _helper) are private and not callable from outside. Method arguments and return values are encoded by the VM ABI.")

# ===========================================================================
# SECTION 6 — stdlib surface
# ===========================================================================
add("What is in the Animica contract storage stdlib?",
    "`storage` is a key/value store of bytes: storage.get(key, default=None) -> bytes, storage.set(key, value), storage.delete(key). Keys and values are bytes. There are no native maps — model a mapping by prefixing keys, e.g. b'bal:' + address.")
add("How do I store an integer in Animica contract storage?",
    "Storage holds bytes, so encode integers deterministically as fixed-width big-endian. For an unsigned value: storage.set(key, int(v).to_bytes(32, 'big')); read back with int.from_bytes(storage.get(key) or b'', 'big'). Use signed=True for signed values.")
add("How do I emit an event from an Animica contract?",
    "events.emit(name: bytes, args: dict). For example: events.emit(b'Transfer', {'from': frm, 'to': to, 'amount': amount}). Events are recorded in the transaction receipt as canonical logs.")
add("How do I revert or assert in an Animica contract?",
    "Use abi.require(condition, b'error_name') to assert, or abi.revert(b'error_name') to fail unconditionally. A revert aborts the call and rolls back all state changes made in it.")
add("How does an Animica contract read the caller and value?",
    "From the abi context: abi.caller() is the immediate caller's 32-byte address, abi.sender()/abi.tx_origin() is the original transaction sender, and abi.value() is the ANM (base units) sent with the call. abi.self_address()/abi.contract_address() give the contract's own address.")
add("What block and transaction context can an Animica contract read?",
    "abi.block_height(), abi.block_timestamp(), abi.chain_id(), abi.tx_hash(), abi.call_depth(), and abi.is_read_only() (True inside a view/call, False inside a send). Use block_timestamp() for time-based logic — never wall-clock time.")
add("How do contracts move native ANM?",
    "Via the treasury stdlib: treasury.balance(address) -> int returns an account's ANM balance, and treasury.transfer(from_addr, to_addr, amount) moves ANM. A contract transfers from its own address with from_addr = abi.self_address().")
add("Can an Animica contract hash data?",
    "Yes: hash.sha3_256(data), hash.keccak256(data), hash.sha3_512(data). For mining-style hashing the hash_work stdlib adds sha256, sha256d, blake2b_256, and helpers like compute_commitment and verify_hash_result.")
add("Can an Animica contract verify a post-quantum signature?",
    "Yes. pq_verify.verify(...) verifies an ML-DSA/Dilithium/SPHINCS+ signature inside the contract, so contracts can authenticate messages signed by post-quantum keys (e.g. multisig or meta-transactions).")
add("How does an Animica contract get randomness?",
    "Deterministically, from the chain: syscalls.get_beacon() returns the current randomness beacon and syscalls.random_bytes(n) returns n bytes derived from it. Never use Python's random module — it is non-deterministic and unavailable.")
add("Can an Animica contract call AI or quantum jobs?",
    "Yes — this is a core feature. syscalls.ai_enqueue(model, prompt) and the higher-level ena.request(...) submit AI inference jobs; syscalls.quantum_enqueue(circuit, shots) submits quantum jobs; syscalls.zk_verify(circuit, proof, public_input) verifies a zero-knowledge proof; results are read back once settled with ena.get_status / ena.get_result_hash / syscalls.read_result.")
add("Can one Animica contract call another?",
    "Yes. abi.call_contract(address, method, args) performs a cross-contract call and returns the decoded result; abi.try_call_contract(...) returns a success flag instead of reverting. Watch abi.call_depth() to bound recursion.")
add("How does an Animica contract pin a blob to data availability?",
    "syscalls.blob_pin(namespace, data) publishes data to the DA layer and returns a commitment the contract can store or emit, letting large payloads live off the main state while staying verifiable.")

# ===========================================================================
# SECTION 7 — full contract examples
# ===========================================================================
add("Show a complete minimal Animica smart contract.",
    "A storage-backed counter with get/inc/reset, events, and bounds checks:\n\n```python\n" + COUNTER + "\n```")
add("Write a simple fungible token contract for Animica.",
    "A minimal token with a one-time constructor, balances, and transfer:\n\n```python\n" + TOKEN + "\n```\nBalances are stored under b'bal:'+address keys (there are no native maps), amounts are 256-bit big-endian, and transfer authorizes the sender with abi.caller() and guards with abi.require.")
add("Write an escrow contract for Animica that holds and releases ANM.",
    "An escrow with a constructor, access control, and a native ANM payout:\n\n```python\n" + ESCROW + "\n```\nIt funds via ANM sent to the contract address, only the beneficiary can release, and the payout uses treasury.transfer from abi.self_address().")
add("Show an Animica contract that uses on-chain AI (ENA).",
    "A contract that enqueues an ENA classification job and reads the settled result:\n\n```python\n" + AI_CONTRACT + "\n```\nThe contract submits the job with ena.request, stores the request id, and later reads the result hash once ena.get_status reports it completed.")
add("How do I implement a mapping in an Animica contract?",
    "There are no native maps; compose a key from a prefix plus the key bytes and store the value, e.g. storage.set(b'bal:' + addr, amount_bytes) and read with storage.get(b'bal:' + addr). Use distinct prefixes (b'bal:', b'allow:') to namespace different mappings.")
add("How do I add a constructor / initializer to an Animica contract?",
    "Expose a one-time setup method (e.g. setup(owner, supply)) that guards against re-initialization, for example: abi.require(storage.get(b'init') is None, b'already_initialized'); storage.set(b'init', b'1'). The deploy step can pass constructor arguments to it.")
add("How do I do access control in an Animica contract?",
    "Compare abi.caller() against an owner stored at deploy time: store the owner in setup (storage.set(b'owner', owner)) and gate privileged methods with abi.require(abi.caller() == storage.get(b'owner'), b'only_owner').")

# ===========================================================================
# SECTION 8 — ABI, compile/deploy/call, gas, testing, security
# ===========================================================================
add("What is the Animica contract ABI format?",
    "A JSON document with encoding 'animica.abi.v1' describing name, version, namespace, types, and functions. Each function has name, stateMutability (view | nonpayable | payable), payable, inputs (name/type), outputs, and docs. Address types are bech32m anim1 strings.")
add("What are the state mutabilities for Animica contract methods?",
    "`view` (read-only, no state change — used by `call`), `nonpayable` (changes state but rejects ANM value), and `payable` (changes state and accepts ANM value via abi.value()). Reads use `animica contract call`; state changes use `animica contract send`.")
add("How do I compile an Animica contract?",
    "animica contract compile path/to/contract.py --out build/contract.avm --abi-out build/contract.abi.json . This produces the deployable artifact (.avm) and the ABI JSON.")
add("How do I deploy an Animica contract?",
    "animica contract deploy build/contract.avm --from main --abi build/contract.abi.json --constructor-args '[\"anim1owner...\", 1000000]' . You can deploy a source file directly too; pass constructor arguments as JSON.")
add("How do I call a read-only Animica contract method?",
    "animica contract call <contract> <method> --args '[...]' . For example: animica contract call counter get . Reads are free (no transaction) and run against current state.")
add("How do I send a state-changing transaction to an Animica contract?",
    "animica contract send <contract> <method> --from main --args '[...]' --wait . For example: animica contract send counter inc --args '[1]' --from main --wait . `--wait` blocks until the transaction is confirmed.")
add("What other animica contract subcommands exist?",
    "inspect (deployment/ABI metadata), address (resolve a saved alias), estimate-gas, encode-calldata and decode-result (ABI codec helpers), receipt (fetch a tx receipt), save-deployment, and list-artifacts.")
add("How is gas handled for Animica contracts?",
    "Each VM operation and stdlib syscall has a fixed gas cost (see vm_py/gas_table.json). The transaction's fee must cover the gas used; estimate it with `animica contract estimate-gas <contract> <method> --args '[...]'`. Deterministic, bounded execution keeps gas predictable.")
add("How do I test an Animica contract before deploying?",
    "Write local pytest tests that load the contract through the VM test harness and invoke methods with a simulated call context (sender, value), then assert on returned values, storage, and emitted events — no running node required. The contracts/tests directory has examples (token, escrow, multisig, oracle).")
add("What security practices apply to Animica contracts?",
    "Use abi.require for every precondition; follow checks-effects-interactions (update storage before external calls/transfers); gate privileged methods with abi.caller(); guard re-initialization; bound all loops; keep integers in documented ranges; and watch abi.call_depth() and treasury.transfer ordering to avoid reentrancy. Never trust off-chain input without validation.")
add("Where do Animica contract artifacts and deployments live?",
    "By default under artifacts/ (compiled .avm artifacts plus an index) and deployments/<network-key>/ (deployment records by network/chain). Records include the contract address, abi, tx_hash, block_height, and deployer; saved aliases let you call by name.")
add("How do I reference a deployed contract by name in the CLI?",
    "Deployments can be saved with an alias so you can run `animica contract call <alias> <method>`; resolve the address with `animica contract address <alias>` and inspect metadata with `animica contract inspect <alias>`.")
add("Can an Animica contract read another account's ANM balance?",
    "Yes — treasury.balance(address) returns any address's ANM balance in base units. A contract reads its own balance with treasury.balance(abi.self_address()).")
add("How should an Animica contract handle time-based logic?",
    "Use abi.block_timestamp() (the block's timestamp) and/or abi.block_height() for deadlines and schedules. Never use Python's time/datetime — they are non-deterministic and unavailable in the VM.")

# ---------------------------------------------------------------------------

def main() -> None:
    base = os.environ.get("ENA_API", "http://127.0.0.1:8791")
    home = os.environ.get("ANIMICA_ENA_HOME", "/var/lib/animica-ena")
    rows = [{"prompt": p, "response": r} for p, r in PAIRS]

    out = Path(__file__).resolve().parent / "animica-knowledge.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} pairs -> {out}")

    db = Path(home) / "ena.db"
    if db.is_file():
        con = sqlite3.connect(str(db), timeout=15)
        con.execute("PRAGMA busy_timeout=15000")
        con.execute("DELETE FROM datasets")
        con.commit(); con.close()
        print("cleared prior datasets")

    body = json.dumps({"name": "animica-knowledge", "kind": "knowledge",
                       "rows": rows, "contributor": "animica", "curate": True}).encode()
    req = urllib.request.Request(base + "/datasets/contribute", data=body, method="POST",
                                 headers={"content-type": "application/json"})
    res = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print("registered:", {k: res.get(k) for k in ("name", "kind", "row_count", "sha256")})


if __name__ == "__main__":
    main()
