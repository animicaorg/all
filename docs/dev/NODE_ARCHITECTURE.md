# Node Architecture — process model & component map

This document explains **how a full node is structured**, how processes/threads/tasks are organized, and how the major subsystems communicate: **Core**, **Consensus**, **Proofs**, **Execution**, **Mempool**, **P2P**, **RPC**, **Mining**, **Data Availability (DA)**, **Randomness**, **Capabilities/AICF**, and **ZK**.

> TL;DR: a single-process, modular Python node with async I/O (FastAPI/WS/WebSockets, P2P) and thread-pool assisted compute (native cryptography, storage), backed by SQLite (default) or RocksDB. Optional components (miners, DA retrieval service, studio-services) can run out-of-process.

---

## 1) Process & Concurrency Model

### Single process, many services
- **Monolith**: the default node runs as a single process that hosts:
  - JSON-RPC & WebSocket server (`rpc/`)
  - P2P networking stack (`p2p/`)
  - Core storage & chain state access (`core/`)
  - Consensus validation & scheduling (`consensus/`)
  - Proof verifiers registry (`proofs/`, `zk/`)
  - Mempool policy & indexing (`mempool/`)
  - Execution engine (state transition, receipts) (`execution/`)
  - Optional adapters (DA, Randomness, Capabilities, AICF bridges)

### Async + threads
- **Async**: network servers (RPC, WS, P2P), gossip, and timers use `asyncio`.
- **Thread pools**: CPU-bound tasks (CBOR/codec bursts, cryptography, KZG/Groth16/ZK checks via `zk/native` if enabled, SQLite/RocksDB I/O bursts) use `concurrent.futures.ThreadPoolExecutor`.
- **Graceful shutdown**: signal handlers trigger service stop-order and flush queues.

### Optional external processes
- **Mining service** (`mining/`): can run in-process (embedded) or out-of-process. Connects via local RPC/WS/Stratum.
- **DA retrieval API** (`da/retrieval/`) and **studio-services** can be run as separate FastAPI services when desired.
- **Wallets** (browser extension / Flutter) are always **out-of-process**; the node never holds user keys.

---

## 2) Component Map

```mermaid
flowchart LR
  subgraph Client
    Dapp[<b>Dapp / SDK</b>\n(@animica/sdk)]
    Wallet[<b>Wallet</b>\n(Extension/Flutter)]
  end

  subgraph Node[Animica Node (single process)]
    RPC[RPC FastAPI\nHTTP + JSON-RPC + WS]
    MEM[Mempool\nadmission/priority/index]
    EXEC[Execution\nstate machine + receipts]
    CONS[Consensus\nPoIES score + Θ retarget]
    CORE[Core DBs\nstate/blocks/txidx]
    P2P[P2P stack\nhandshake+gossip+sync]
    PROOFS[Proofs & ZK\nverifiers registry]
    DA[DA adapters\nNMT roots, proofs]
    RAND[Randomness\ncommit→reveal→VDF]
    CAP[Capabilities host\nAI/Quantum/zk/random/blob]
    AICF[AICF bridge\nregistry/settlement]
  end

  subgraph Miner
    MIN[Mining orchestrator\ntemplates + search + submit]
  end

  Dapp <--> RPC
  Wallet <--> RPC
  RPC <---> MEM
  RPC <---> CORE
  RPC <---> EXEC
  RPC <---> DA
  RPC <---> CAP
  MEM <--> P2P
  MEM --> EXEC
  EXEC --> CORE
  CONS <--> CORE
  PROOFS <--> CONS
  DA <--> CONS
  RAND <--> CONS
  CAP <--> EXEC
  AICF <--> CAP
  P2P <--> CORE
  MIN <--> RPC
  MIN <--> PROOFS
  MIN <--> CONS


⸻

3) Storage & Indices
	•	State DB: account/state key–value (core/db/state_db.py) on top of SQLite (default) or RocksDB (optional).
	•	Block/Headers DB: canonical chain storage (core/db/block_db.py), head pointers, receipts/logs.
	•	Tx Index: hash → (height, idx) for lookup (core/db/tx_index.py).
	•	DA Store (optional): blob store + NMT indices (da/blob/*) when DA endpoints are enabled.
	•	AICF/Capabilities: queue/result stores (capabilities/jobs/*, aicf/queue/*) use SQLite by default.

⸻

4) Lifecycle & Service Order

Startup (simplified)
	1.	Load config/env (core/config.py, rpc/config.py).
	2.	Open DBs (core/db/sqlite.py or RocksDB).
	3.	Load/validate genesis (compute roots & initialize head if first run).
	4.	Mount RPC (HTTP/WS) and start P2P transports.
	5.	Warm verifiers/registries (proofs, zk, policy).
	6.	Start background tasks: head tracker, fee market EMA, nullifier TTL, randomness rounds, DA samplers (if enabled).

Shutdown
	•	Stop acceptors (RPC/P2P) → drain queues → flush DB → stop timers → final logs/metrics.

⸻

5) Transaction Flow

sequenceDiagram
  participant C as Client/Wallet
  participant RPC as RPC server
  participant MEM as Mempool
  participant EXEC as Execution
  participant CORE as Core DB
  participant P2P as P2P

  C->>RPC: tx.sendRawTransaction(CBOR)
  RPC->>MEM: stateless validate (sizes, chainId, PQ sig precheck)
  MEM-->>RPC: accepted (pending hash)
  MEM->>P2P: gossip INV(Tx)
  Note over MEM: priority = fee, size, age, replace-by-fee rules
  MIN loop->>MEM: fetch ready txs under gas/byte budget
  MIN->>RPC: submit candidate block
  RPC->>EXEC: apply block (serial scheduler)
  EXEC->>CORE: write state/receipts/logs
  RPC-->>C: tx receipt (status/gasUsed/logs)
  P2P->>Peers: relay block/tx confirmations

Admission stages:
	•	Stateless checks: sizes, schema, PQ sig precheck, chainId, intrinsic gas.
	•	Account checks: balance/nonce (fast reads).
	•	Priority/Replace: RBF thresholds and per-sender fairness caps.
	•	Indices: hash, sender queues, ready/held sets.

⸻

6) Block Import & Consensus
	1.	Header parse + cheap checks: roots present, policy roots (alg-policy, DA root optional).
	2.	Proofs validation (if included): HashShare/AI/Quantum/Storage/VDF envelopes verified via proofs/ and zk/ as applicable.
	3.	PoIES scoring: compute Σψ, draw S = −ln(u) + Σψ, compare with Θ; apply caps and Γ rules (consensus/scorer.py, consensus/caps.py).
	4.	Difficulty/Θ retarget: EMA clamps, schedule update (consensus/difficulty.py).
	5.	Finalize import: persist header/block, receipts; update head; fork choice (longest/weight-aware).
	6.	Reorg handling: bounded reorgs; re-inject mempool where needed.

⸻

7) Execution & Receipts
	•	Runtime: execution/runtime/* applies transfers/calls/deploys deterministically.
	•	Gas model: intrinsic + metered op costs (execution/gas/*), refunds bounded.
	•	Receipts: status, gasUsed, logs/bloom roots, CBOR encoding stable.
	•	Access lists: built/merged for scheduling & future parallelization.

⸻

8) Mining / Block Production

Two modes:
	•	Local miner (dev): mining/orchestrator.py builds header templates, selects proofs, runs CPU hash search (mining/hash_search.py), and submits shares/blocks via RPC.
	•	External miners: Stratum (mining/stratum_server.py) and WS getwork (mining/ws_getwork.py) for remote devices. Proof selector ensures Γ/caps/fairness before pack.

⸻

9) P2P: Handshake, Gossip, Sync
	•	Handshake: PQ-first (Kyber KEM + HKDF → AEAD), identity via Dilithium3/SPHINCS+, peer-id = sha3(alg_id||pubkey).
	•	Transports: TCP, QUIC (optional), WS. Encrypted binary frames.
	•	Gossip: topics for headers, blocks, txs, shares (useful-work), DA messages.
	•	Sync: header sync with locators, then block bodies; mempool & share relay with rate limits.

⸻

10) Data Availability (DA)
	•	Commitment: NMT root included in block header when blobs present.
	•	Retrieval API: optional FastAPI service (da/retrieval/*) to POST/GET blobs and proofs.
	•	Light client: verify availability with sampling proofs against DA root.

⸻

11) Randomness Beacon
	•	Rounds: commit → reveal → VDF verify → optional QRNG mix.
	•	Integration: beacon recorded in chain DB and exposed to contracts via adapters; light proofs available.

⸻

12) ZK & Proofs
	•	Verifiers: Groth16 (BN254), PLONK(KZG), toy STARK(FRI) with Poseidon, plus generic Merkle.
	•	Native paths: optional zk/native Rust crate for BN254 pairing/KZG acceleration.
	•	VK registry: pinned verification keys with hashes and metadata; policy gates which circuits are allowed.

⸻

13) Capabilities & AICF
	•	Contract syscalls: blob_pin, ai_enqueue, quantum_enqueue, zk_verify, random, treasury (host-side providers enforce determinism & size caps).
	•	AICF: provider registry, staking, SLA evaluation, pricing/splits, settlement; maps on-chain proofs to payouts/slashing.

⸻

14) Observability & Ops
	•	Metrics: Prometheus counters/histograms across subsystems (*/metrics.py), /metrics endpoint.
	•	Logs: structured JSON logs with request/trace IDs; per-subsystem levels.
	•	Health: /healthz, /readyz, version endpoints.

⸻

15) Configuration & Security
	•	Config: env + config modules; sane defaults for devnet (chainId 1337).
	•	Key isolation: node holds no user signing keys (wallets manage seeds; PQ addresses).
	•	CORS & rate limits: strict RPC & DA middleware; per-IP/per-route token buckets.
	•	Policy roots: PQ alg-policy, ZK VK pinning, DA namespaces—all hashed & enforced.

⸻

16) Failure Modes & Recovery
	•	Crash safety: SQLite transactions & write-ahead ensure atomicity; on restart, head & indices recover from last committed block.
	•	Reorgs: bounded by fork-choice rules; mempool re-inject logic restores dropped/replaced txs.
	•	Partial services: DA/Randomness/AICF are optional; node continues with reduced features if disabled.

⸻

17) Developer Notes
	•	Hot reload: run RPC with uvicorn --reload for fast iteration.
	•	E2E: see sdk/test-harness for cross-language deploy+call tests.
	•	Bench: zk/bench, da/bench, mining/bench provide micro-bench entrypoints.

⸻

This architecture favors clarity and determinism first, with clear seams for native acceleration and out-of-process scaling where needed.
