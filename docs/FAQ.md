# Animica FAQ

Short, practical answers to the questions we get most about **miners**, **contracts**, **post-quantum (PQ) keys**, **quantum compute proofs**, and more. For terminology, see the [Glossary](./GLOSSARY.md). For writing conventions, see the [Style Guide](./STYLE_GUIDE.md).

---

## Basics

**What is Animica?**  
A PoIES chain where blocks are accepted when `Σψ ≥ Θ`: we aggregate contributions `ψ` from heterogeneous proofs (hash, AI, quantum, storage, VDF) under policy caps `Γ`, and retarget `Θ` for stable block times.

**Which networks exist?**  
- `animica:1` — mainnet (reserved)  
- `animica:2` — public testnet  
- `animica:1337` — local/devnet  
See site chain files in `website/chains/*.json`.

---

## Mining

**How do I start the built-in CPU miner?**  
```bash
python -m mining.cli.miner --threads 4 --device cpu
# Optional flags: --rpc http://127.0.0.1:8545 --share-target 8e-3

What does the miner actually submit?
	•	It scans nonces to produce HashShare proofs (u-draw; ratio vs Θ).
	•	Optionally attaches AI/Quantum/Storage/VDF proofs selected under caps Γ.
	•	A candidate block is packed then submitted via RPC (or Stratum).

CPU vs GPU?
CPU works everywhere. Optional GPU backends (CUDA/OpenCL/Metal) are available if your drivers/toolkits are installed; miner auto-detects but gracefully falls back to CPU.

I see rejects — what now?
	•	WorkExpired: template rolled; fetch new work.
	•	SubmitRejected: policy or header link mismatch; update node/miner.
	•	Tighten share target (submit higher-quality shares) or wait for Θ retarget.

See: mining/README.md, mining/specs/*, consensus/README.md.

⸻

Contracts (VM(Py))

Language & determinism?
Contracts are written in a safe subset of Python, compiled to a deterministic IR. No I/O, no network, bounded runtime and memory. See vm_py/specs/*.

How do I deploy a contract?
Using the Python SDK:

from omni_sdk.contracts.deployer import deploy
addr = deploy(manifest_path="examples/counter/manifest.json", rpc_url="http://127.0.0.1:8545")
print(addr)  # anim1...

How do I call it?

from omni_sdk.contracts.client import Contract
c = Contract(address=addr, abi_path="examples/counter/manifest.json", rpc_url="http://127.0.0.1:8545")
c.write("inc")          # state-changing
print(c.read("get"))    # view call

Gas & receipts?
	•	Intrinsic gas depends on tx kind & payload.
	•	Execution debits/credits gas and returns a Receipt with logs/bloom.
	•	Deterministic event order; see execution/specs/*.

⸻

Post-Quantum (PQ) Keys & Addresses

Which algorithms are supported?
	•	Dilithium3 (default signer)
	•	SPHINCS+ (SHAKE-128s) (alternative)
	•	Kyber-768 for KEM in P2P handshakes

What do addresses look like?
Bech32m with HRP anim (e.g., anim1...). Payload = alg_id || sha3_256(pubkey). See pq/py/address.py.

Can I import/export keys?
Yes — wallet (extension/Flutter) supports mnemonic → PQ keys. SDKs provide keystore helpers. Never upload private keys; no server-side signing anywhere.

⸻

Quantum & AI Proofs

What is an AI/Quantum proof?
A provider executes a job and returns attested evidence: TEE or QPU certificates + trap results + QoS metrics. The verifier maps metrics → ψ inputs (then policy caps apply).

Who runs providers? (AICF)
Registered providers with stake; assignments, SLAs, and settlements are managed by AICF. See aicf/README.md and specs.

Latency expectations?
Typically next block: contracts enqueue this block and consume results the next via deterministic result_read. See capabilities/*.

⸻

Data Availability (DA)

How do blobs relate to blocks?
Blobs are erasure-encoded into namespaced leaves → NMT root. The block header’s DA root commits to all blob commitments.

Light client check?
DAS sampling verifies inclusion/range proofs against the DA root. See da/specs/*.

⸻

Randomness

How is beacon randomness produced?
Commit–reveal aggregation → Wesolowski VDF proof → optional QRNG mix. Verifiers check the VDF; outputs are persisted and exposed to contracts. See randomness/specs/*.

⸻

Zero-Knowledge (ZK) Verification

Which schemes are supported?
	•	Groth16 (BN254) — snarkjs JSON compatible
	•	PLONK (KZG on BN254) — single-opening demo flow
	•	STARK (FRI) — tiny educational verifier for Merkle-membership AIR

How do I verify a proof?
	•	Off-chain: zk/integration/omni_hooks.py exposes a python API.
	•	On-chain (future hook): contracts call zk.verify(...) via capabilities; policy meters cost/size.

VKs (verification keys)?
Pinned in zk/registry/vk_cache.json with metadata in zk/registry/registry.yaml. Update via zk/registry/update_vk.py (hash/sign checks).

Performance tips
Enable native Rust fast paths (zk/native crate) for pairing/KZG; Python falls back to py_ecc. See zk/docs/PERFORMANCE.md.

⸻

Fees, Mempool, Replacement

Why was my tx rejected?
	•	FeeTooLow: raise base/tip; check dynamic floor.
	•	NonceGap: submit missing nonces in order.
	•	ChainIdMismatch: wrong network.
	•	AdmissionError: size/gas/signature precheck failed.

Replacement policy (RBF-style): same sender+nonce requires ≥ X% higher effective fee. See mempool/policy.py.

⸻

RPC, P2P, SDKs

JSON-RPC methods?
Minimal, OpenRPC-documented surface: chain params/head, block/tx lookup, send raw tx, state getters, DA endpoints, AICF, randomness (varies by build). See spec/openrpc.json and rpc/methods/*.

WebSocket subscriptions?
/ws hub publishes newHeads, pendingTxs, miner getwork, AICF/job events.

SDKs?
	•	Python: sdk/python (omni_sdk)
	•	TypeScript: sdk/typescript (@animica/sdk)
	•	Rust: sdk/rust (animica-sdk)

⸻

Wallets

Browser extension vs Flutter?
	•	Extension (MV3): in-page provider window.animica, connect/sign/submit.
	•	Flutter: desktop/mobile app; installers for macOS/Windows/Linux.
No server-side signing; CORS and permissions are strict.

⸻

Testnets & Local Dev

Spin up a local node from genesis

python -m core.boot --genesis core/genesis/genesis.json --db sqlite:///animica.db
python -m rpc.server --host 127.0.0.1 --port 8545

Then mine locally or use the SDK examples.

⸻

Security

Trusted setup caveats?
Groth16/PLONK require SRS; we pin VKs and document SRS provenance. Prefer circuits with well-audited setups; see zk/docs/SECURITY.md.

Key safety
Keep mnemonics offline; use keystores with strong passphrases; never paste secrets into web pages.

⸻

Troubleshooting

Common errors & fixes
	•	InvalidTx: check CBOR format, signature domain, chainId.
	•	PolicyError: wrong policy root or out-of-bounds ψ inputs.
	•	ProofError / AttestationError: malformed proof or vendor chain invalid; re-export with proper fixtures.
	•	RateLimitError: slow down RPC requests or raise limits in config.

Where are logs?
	•	Node/RPC: structured logs in stdout (configure level via rpc/config.py).
	•	Miner: mining/logs (if configured) and console.

⸻

Where to read more
	•	Consensus & math: spec/poies_math.md, consensus/README.md
	•	ZK architecture & formats: zk/docs/*
	•	DA & sampling: da/specs/*
	•	VM(Py) determinism: vm_py/specs/*
	•	Randomness beacon: randomness/specs/*
	•	AICF (providers/slashing): aicf/specs/*

If your question isn’t covered, open an issue with a minimal repro and the versions (core, rpc, sdk, miner) you’re running.
