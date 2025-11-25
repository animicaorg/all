# Animica Mining Module

The `mining/` module lets a node (or an external miner) produce *useful* blocks by combining:
- classical **HashShare** work (u-draw, header-bound) and
- verifiable **AI/Quantum/Storage/VDF** proofs,

into a single PoIES score S = H(u) + Σψ that must meet the network difficulty Θ. This document
covers architecture, flows, configs, message formats, and how AI/Quantum proofs are attached.

> **Gate for this milestone:** a single node mines dev blocks locally (CPU miner) and can also
> serve/consume external work via Stratum and WebSocket getwork, **with** attached AI/Quantum
> proofs selected under policy caps and fairness.

---

## 1) Architecture at a glance

       ┌─────────────────────────────────────────────────┐
       │                     rpc/                         │
       │   /rpc       /ws         /metrics    /openrpc    │
       └─────▲─────────▲────────────▲──────────▲──────────┘
             │         │            │          │
             │         │            │          │

┌────────────────┴─┐  ┌────┴──────────┐│     ┌────┴────────────┐
│ mining/orchestr. │  │ mining/ws_*   ││     │ mining/stratum_*│
│  rotate templates│  │  getWork/WS   ││     │  JSON-RPC/TCP   │
│  run device loop │  │  submitShare  ││     │  submit_share   │
│  attach proofs   │  │               ││     │                 │
└───────┬──────────┘  └──────┬────────┘│     └──────┬──────────┘
│                    │         │            │
│                    │         │            │
┌────▼─────┐         ┌────▼─────┐   │       ┌────▼─────┐
│ hash_…   │         │ templates│   │       │ share_…  │
│ CPU scan │         │ + packer │   │       │ submitter│
└────┬─────┘         └────┬─────┘   │       └────┬─────┘
│     HashShare         │ Candidate Block        │
│  (nonce, mix, D)      │ (txs + proofs)         │
│                       │                        │
┌────▼────────┐         ┌────▼────────────────────────▼──────────┐
│ consensus/  │         │     proofs/ & aicf/ adapters           │
│  Θ, Γ, caps │         │  verify useful proofs, map → ψ inputs  │
└────┬────────┘         └────┬────────────────────────────────────┘
│                       │
┌────▼────────┐         ┌────▼──────────────┐
│ core/chain  │         │ core/db block_db  │
│  head, link │         │  persist, update  │
└─────────────┘         └───────────────────┘

**Key responsibilities**
- **Template builder** (`templates.py`): builds header candidates from current head, Θ/Γ, mempool snapshot, DA root (optional), policy roots, and randomness seed.
- **Hash scanning** (`hash_search.py`): CPU loop over nonce/mixSeed domain to produce **HashShare** proofs; computes **D_ratio** vs the micro-target.
- **Proof selector** (`proof_selector.py`): chooses a set of AI/Quantum/Storage/VDF proofs respecting per-type caps, total Γ, and fairness (no double counting via nullifiers).
- **Header packer** (`header_packer.py`): combines header, txs, and selected proofs into a **candidate block**. Re-checks score ≥ Θ.
- **Submitter** (`share_submitter.py`): submits shares or full candidate blocks via local node RPC (with backoff/retry).
- **Server interfaces**: 
  - **WebSocket getwork** (`ws_getwork.py`) for browser/miner clients.
  - **Stratum server** (`stratum_server.py`) for pool-style miners.

---

## 2) Data & message shapes

### 2.1 Header template (JSON)
Served to miners via WS/Stratum and periodically refreshed:

```json
{
  "chainId": 1,
  "height": 1024,
  "parentHash": "0x…",
  "stateRoot": "0x…",
  "txsRoot": "0x…",
  "proofsRoot": "0x…",
  "daRoot": "0x…",
  "params": { "thetaMicro": 1130000, "gammaCap": 2.5, "escortQ": 0.15 },
  "policyRoots": { "poiesPolicy": "0x…", "algPolicy": "0x…" },
  "nonceDomain": { "mixSeed": "0x…", "uDomainTag": "animica:u-draw:v1" },
  "time": 1730000000,
  "coinbase": "anim1…",
  "extra": { "mempoolSnapshotId": "…", "templateId": "…"}
}

2.2 HashShare proof (envelope snippet)

HashShare includes:
	•	binding to header template (parent hash, Θ schedule window id, mixSeed),
	•	the u-draw sample,
	•	the micro-target ratio D_ratio (for scoring/selection).

{
  "type_id": "hashshare:v1",
  "body": {
    "templateId": "…",
    "nonce": "0x0000…",
    "mixSeed": "0x…",
    "u": "0x…",                  // 256-bit uniform sample
    "keccak": "0x…",             // header-nonce hash binding
    "d_ratio_ppm": 712345        // share difficulty ratio (ppm of target)
  },
  "nullifier": "0x…"
}


⸻

3) Flows

3.1 getWork → scan → submitShare (WS)
	1.	Client subscribes to /ws → miner.newWork stream.
	2.	Server pushes a template when head or Θ changes.
	3.	Client scans nonces producing HashShares where H(u) is promising and d_ratio_ppm meets the current share micro-target.
	4.	Client sends miner.submitShare with the proof envelope.
	5.	Node verifies (cheap path): schema → header-linking → u-draw domain → target ratio → nullifier freshness.
	6.	Shares accumulate; if Σψ with other proofs ≥ Θ and linkage is valid, a block candidate is sealed and broadcast.

WS examples
	•	Subscribe:

{"jsonrpc":"2.0","id":1,"method":"miner_subscribe","params":["newWork"]}


	•	New work notification:

{"jsonrpc":"2.0","method":"miner.newWork","params":[{ /* template JSON */ }]}


	•	Submit share:

{"jsonrpc":"2.0","id":2,"method":"miner_submitShare","params":[{ /* HashShare envelope */ }]}



3.2 Stratum (JSON-RPC over TCP)
	•	Methods: mining.subscribe, mining.authorize, mining.set_difficulty, mining.notify, mining.submit.
	•	Animica extends notify payload to include:
	•	policyRoots, Θ (thetaMicro), and mixSeed.
	•	Optional required proof hints (e.g., “attach ≥1 AIProof with traps_ratio ≥ t”).

3.3 Candidate assembly with useful proofs

When a HashShare (or set of them) appears promising, the orchestrator asks the proof selector to pull locally verified AI/Quantum/Storage/VDF proofs (from worker queues or local caches) and checks:
	•	per-proof validity (proofs/ verifiers),
	•	caps (per-proof kind, per-type, total Γ),
	•	fairness (escort q constraints),
	•	nullifier freshness.

Then S = H(u) + Σψ is recomputed; if S ≥ Θ, the header_packer seals a block.

3.4 AI/Quantum/Storage/VDF workers
	•	AI worker uses aicf/ to enqueue small, deterministic jobs (e.g., model inference with TEE attestation + trap receipts).
	•	Quantum worker enqueues trap-circuit runs to a QPU provider; collects cert+trap outcomes; builds a QuantumProof.
	•	Storage worker sends a heartbeat PoSt and optional retrieval tickets for bonus ψ.
	•	VDF worker (devnet) may generate Wesolowski proofs for optional bonus ψ or for the beacon.

All proofs are verified locally (via proofs/) before being considered by the selector.

⸻

4) Hash scanning details (CPU miner)
	•	Nonce domain: a 64-bit (or 128-bit) nonce plus a per-template mixSeed. The scanning function derives:

digest = Keccak512( canonical_header_without_nonce || nonce || mixSeed )
u      = digest[0:32] interpreted as 256-bit uniform
H(u)   = -ln(u / 2^256)  // safe log; see consensus/math.py


	•	Micro-target: we track a moving share target to keep share submissions smooth. The D_ratio encodes how “deep” the share is relative to current Θ; the selector prefers deeper shares.
	•	SIMD: the CPU backend optionally uses numpy/numba (feature-gated) for tighter loops; pure-Python fallback remains.

⸻

5) Configuration (env + file)

mining/config.py accepts:
	•	MINER_THREADS (default: #logical cores)
	•	MINER_DEVICE = cpu|cuda|rocm|opencl|metal (only cpu enabled by default)
	•	MINER_TARGET_SHARES_PER_SEC (adaptive micro-target tuning)
	•	RPC_URL, WS_URL, CHAIN_ID
	•	Stratum: STRATUM_LISTEN=0.0.0.0:11333
	•	Selection policy overlays (local caps tighter than network)
	•	AICF endpoints for AI/Quantum job queues (devnet-ready)

⸻

6) Node interactions
	•	core/: reads current head, persists candidate blocks via the block DB adapter.
	•	consensus/: reads Θ, Γ, caps & fairness from policy; uses scorer to check acceptance.
	•	proofs/: verifies all proof kinds (HashShare, AI, Quantum, Storage, VDF) and produces metrics.
	•	aicf/: enqueues compute jobs, receives completed outputs/proofs, accounts payouts.
	•	da/: computes DA root for candidate blocks when blobs are included.
	•	randomness/: may consume or produce VDF proofs for beacon rounds.
	•	rpc/: mounts getwork WS and Stratum bridge endpoints.

⸻

7) Running locally (devnet)

Quick start (built-in CPU miner)

# 1) Start node RPC (from repo root; example)
python -m rpc.server --config rpc/config.toml

# 2) Start miner (CPU)
python -m mining.cli.miner --threads 4 --device cpu --rpc http://127.0.0.1:8545 --ws ws://127.0.0.1:8546

Stratum proxy (optional)

python -m mining.cli.stratum_proxy --listen 0.0.0.0:11333 --rpc http://127.0.0.1:8545

One-shot getwork (debug)

python -m mining.cli.getwork

You should see periodic newWork messages, shares being found and submitted, and eventually blocks being sealed when Σψ pushes S ≥ Θ.

⸻

8) Security & DoS considerations
	•	Admission: All shares/proofs are schema-checked, cryptographically bound to the current header template, and nullifier-deduped.
	•	Rate limits: WS & Stratum endpoints enforce token-buckets per peer and global.
	•	Fairness: Proof selection respects escort q and caps to prevent a single proof type from starving others.
	•	Attestation: AI/Quantum proofs must include verifiable attestations and trap outcomes; policy roots pin acceptable providers/algorithms.

⸻

9) Metrics

Exposed via /metrics (Prometheus):
	•	animica_miner_hashrate_shares_per_sec
	•	animica_miner_submit_latency_seconds
	•	animica_miner_shares_rejected_total{reason=…}
	•	animica_miner_selected_proofs_total{type=hash|ai|quantum|storage|vdf}
	•	animica_miner_blocks_sealed_total

⸻

10) Extensibility
	•	New devices: add a backend under gpu_* and implement device.py interface.
	•	New proof kinds: implement a verifier in proofs/, wire a metrics → ψ mapping, then teach proof_selector cap logic (caps enforced in consensus/).
	•	Pools: extend Stratum messages to carry provider hints and per-miner quotas.

⸻

11) FAQs

Q: Can I mine with only HashShares?
A: Yes, but you’ll often need deeper shares. Attaching valid AI/Quantum/Storage/VDF proofs greatly increases Σψ, reducing the required H(u).

Q: Are AI/Quantum jobs deterministic?
A: The verification is deterministic. Providers attach attested evidence and trap outcomes; scoring uses only verifiable metrics.

Q: Does the miner keep secrets?
A: No private chain secrets. Device auth (for pools) and provider credentials (for AICF) are kept locally by the operator.

⸻

12) File map (where to look next)
	•	nonce_domain.py, hash_search.py — inner loop and u-draw binding
	•	templates.py, header_packer.py — build and seal candidates
	•	proof_selector.py — policy-aware proof picking
	•	orchestrator.py — runs the whole pipeline
	•	ws_getwork.py, stratum_server.py — external miner APIs
	•	adapters/* — glue to core/consensus/proofs/aicf

