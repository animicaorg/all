# Full Node Overview — End-to-End

This document explains what an **Animica full node** does from boot to block production/validation, how subsystems fit together, and where each piece lives in the repo. It’s written to be *systems-engineer friendly*—you should be able to follow a transaction or a block through every layer.

> TL;DR
> - A node boots from **genesis**, loads **params/policy**, and brings up **storage + RPC + P2P**.
> - It **syncs** headers/blocks, **verifies proofs** (PoIES inputs), **executes** transactions, and **serves** APIs.
> - Miners optionally run the **HashShare search** plus *useful-work* (AI/Quantum/Storage/VDF) flows and produce blocks.
> - DA blobs are committed and later verified by **light clients** via DAS.
> - **Randomness** advances via commit→reveal→VDF; **ZK** verification is pluggable via the `zk/` module.

---

## 0) Key Objects (spec terminology)

- **Header**: roots (state, txs, receipts, DA), Θ (difficulty target), nonce/mixSeed, chainId.  
  Schema: `spec/header_format.cddl` → `core/types/header.py`
- **Block**: header + txs + proof receipts.  
  Schema: `core/types/block.py`
- **Tx**: CBOR, domain-separated **PQ signature** (Dilithium3/SPHINCS+).  
  Schema: `spec/tx_format.cddl` → `core/types/tx.py`
- **Proofs** (PoIES): HashShare, AIProof, QuantumProof, StorageHeartbeat, VDFProof.  
  Envelopes: `proofs/types.py`, schemas under `proofs/schemas/*`
- **PoIES acceptance**: accept iff \( S = H(u) + \sum \psi \ge \Theta \), with per-type caps and total Γ cap.  
  Policy: `spec/poies_policy.yaml`, math: `spec/poies_math.md`, logic: `consensus/scorer.py`, `consensus/caps.py`

---

## 1) Boot & Genesis

**Goal:** Initialize databases, compute the genesis header/roots, and expose services.

- **Load genesis:** `core/genesis/loader.py` reads `core/genesis/genesis.json`, materializes accounts, computes `stateRoot` via `core/chain/state_root.py`.
- **Persist:** Headers/blocks in `core/db/block_db.py`; state KV in `core/db/state_db.py` (SQLite by default; RocksDB optional).
- **Params & policy:** Chain params from `spec/params.yaml` → `core/types/params.py`. PoIES policy from `spec/poies_policy.yaml` → `consensus/policy.py`.
- **Bring up:** RPC (`rpc/server.py`), P2P (`p2p/node/service.py`), DA service (optional, `da/retrieval/api.py`), metrics endpoints.

```mermaid
flowchart LR
  A[Genesis JSON] --> B[State DB init]
  B --> C[Compute Roots]
  C --> D[Genesis Header]
  D --> E[Persist DBs]
  E --> F[Start RPC/P2P/DA]


⸻

2) Networking & Sync (P2P)

Handshake: PQ P2P using Kyber768 KEM + HKDF-SHA3-256 → AEAD channel, identity signed by Dilithium3/SPHINCS+. (p2p/crypto/*, p2p/protocol/hello.py)

Sync flows:
	•	Headers sync: p2p/sync/headers.py (locators, fork handling). Lightweight checks via p2p/adapters/consensus_view.py.
	•	Blocks sync: p2p/sync/blocks.py fetches bodies; p2p/adapters/core_chain.py decodes CBOR, validates against spec.
	•	Gossip: txs, headers, blocks, and shares (useful-work) via topics in p2p/gossip/*.

Fork choice: consensus/fork_choice.py (longest/weight-aware, deterministic tie-breakers). The effective weight is governed by PoIES acceptance and Θ scheduling.

⸻

3) Proofs & PoIES (Useful-Work + HashWork)

Verification (per proof kind):
proofs/ module parses evidence, validates attestations (SGX/SEV/CCA, quantum providers, storage PoSt, VDF), and emits metrics used by PoIES scoring.
Adapters map metrics → ψ inputs (proofs/policy_adapter.py).

Acceptance score:
consensus/scorer.py applies mapping ψ(p), caps (consensus/caps.py), totals Γ, and checks ( H(u) + \sum \psi \ge \Theta ). Difficulty retarget uses EMA (consensus/difficulty.py).

Nullifiers: prevent reuse and replay (consensus/nullifiers.py, proofs/nullifiers.py).

⸻

4) Mempool & Admission

Incoming txs arrive via RPC or P2P:
	•	Stateless checks: mempool/validate.py (sizes, chainId, gas limits, PQ sig precheck).
	•	Account checks: mempool/accounting.py (balances, intrinsic gas).
	•	Queues & replacement: nonce sequencing, RBF policy in mempool/sequence.py, mempool/policy.py.
	•	Fee market: dynamic floor + surge in mempool/fee_market.py.
	•	Selection for block: mempool/drain.py chooses ready txs under gas/byte budgets.

Events: pending, dropped, replaced (mempool/notify.py).

⸻

5) Block Production (Mining/Proving)

For nodes acting as producers (CPU miner + optional useful-work workers):
	•	Templates: mining/templates.py assembles a candidate header (roots TBD), selected txs, and attached proofs decided by mining/proof_selector.py under caps/Γ/fairness.
	•	HashShare search: mining/hash_search.py runs the nonce/mixSeed u-draw; mining/share_target.py computes ratio vs Θ.
	•	Useful-work workers: mining/ai_worker.py, quantum_worker.py, storage_worker.py, vdf_worker.py.
	•	Submit: mining/share_submitter.py → RPC or Stratum proxy (mining/stratum_*).

If acceptance holds, seal header and broadcast via P2P. The winning block includes proof receipts and DA commitments (if any).

⸻

6) Data Availability (DA)

Large payloads (blobs) are committed via Namespaced Merkle Trees + erasure coding.
	•	Blob → shares → NMT root: da/erasure/*, da/nmt/*, commitment in da/blob/commitment.py. Schema in spec/blob_format.cddl.
	•	Retrieval API: da/retrieval/api.py for POST/GET/proof; proofs verified by da/sampling/verifier.py.
	•	Light clients: use header’s DA root + random samples to determine availability (da/sampling/light_client.py).

⸻

7) Execution (State Machine) & Receipts

Upon block import (validator path) or when producing:
	•	Tx execution: execution/runtime/* (transfer/deploy/call). Deterministic gas accounting (execution/gas/*).
	•	State writes: journaled writes, revert/commit (execution/state/*), receipts/bloom (execution/state/receipts.py).
	•	Access lists & scheduler: optional optimistic scheduler prototype (execution/scheduler/*).
	•	VM(Py) bridge: contracts compiled/run via the Python VM in vm_py/ (deterministic interpreter & stdlib). When enabled, the execution/adapters/vm_entry.py routes calls.

Root calculations must match spec/* schemas; receipts & logs are indexed and exposed via RPC.

⸻

8) Randomness Beacon

Rounds advance through:
	1.	Commit: domain-separated commitment of salt/payload (randomness/commit_reveal/commit.py)
	2.	Reveal: verified against prior commitment (verify.py)
	3.	VDF: Wesolowski verification (randomness/vdf/verifier.py)
	4.	Finalize: mix to produce BeaconOut (randomness/beacon/finalize.py)

Gossip commitments/reveals (randomness/adapters/p2p_gossip.py) and expose RPC (randomness/rpc/methods.py).

⸻

9) ZK Verification (optional plug-in)

The node offers an internal zk.verify capability for contracts or services that need to verify succinct proofs off-chain/on-chain:
	•	Schemes: Groth16 (BN254), PLONK-KZG (BN254), toy STARK over FRI.
	•	Verifiers: zk/verifiers/* with optional Rust native fast paths under zk/native/.
	•	Adapters: normalize snarkjs/plonkjs/STARK JSON (zk/adapters/*) into a canonical ProofEnvelope (zk/integration/types.py).
	•	Policy: zk/integration/policy.py (allowlist, size caps, gas costs).
	•	Hooks: zk/integration/omni_hooks.py exposes a clean function surface used by capabilities/host/zk.py.

⸻

10) RPC & WebSockets

JSON-RPC 2.0 (rpc/jsonrpc.py) with models in rpc/models.py. Key methods include:
	•	Chain: chain.getParams, chain.getChainId, chain.getHead
	•	Blocks/Tx: chain.getBlockByNumber/Hash, tx.sendRawTransaction, tx.getTransactionByHash/Receipt
	•	State: state.getBalance, state.getNonce
	•	DA: mounted endpoints for blobs/proofs
	•	AICF/Capabilities: read-only job/result queries
	•	WS pub/sub: rpc/ws.py (newHeads, pendingTxs)

Middleware for logging, rate-limits, CORS in rpc/middleware/*. Prometheus metrics in rpc/metrics.py.

⸻

11) Wallets, SDKs, and Studio
	•	Wallet extension (MV3) and Flutter wallet sign with PQ keys and submit CBOR txs over RPC.
	•	SDKs (Python/TS/Rust) provide typed clients for RPC, contracts, DA, AICF, randomness, and light-client verify.
	•	Studio Web + Studio Services enable compile/simulate/deploy/verify flows without server-side signing.

⸻

12) Observability & Ops
	•	Metrics across subsystems: */metrics.py (Prometheus).
	•	Structured logs: core/logging.py, RPC/P2P middleware.
	•	CLI tools:
	•	Core/genesis/chain ops: core/cli_demo.py, core/boot.py
	•	Proof tools: proofs/cli/*
	•	Consensus bench: consensus/cli/bench_poies.py
	•	Miner control: mining/cli/*
	•	DA tooling: da/cli/*
	•	Randomness: randomness/cli/*

⸻

13) Security Invariants (high level)
	•	PQ signatures for txs and P2P identities (Dilithium3/SPHINCS+). Addresses: bech32m of alg_id || sha3(pubkey).
	•	PoIES caps prevent any single proof type from dominating; nullifiers prevent replay.
	•	DA ensures availability proofs verify from headers alone.
	•	ZK policy pins VKs and sizes; trusted setup caveats documented.
	•	Determinism: VM(Py) & execution avoid non-deterministic sources; gas metering is deterministic.
	•	CORS/rate-limit: conservative defaults in RPC/services.

⸻

14) End-to-End Trace (happy path)
	1.	Wallet builds a tx → sign (PQ) → tx.sendRawTransaction.
	2.	RPC validates → forwards to mempool → gossip to peers.
	3.	Miner builds template (tx set + selected proofs, DA commitments).
	4.	HashShare u-draw hits acceptance with ( H(u) + \sum \psi \ge \Theta ).
	5.	Producer seals block → broadcast.
	6.	Validators verify header (policy roots, nullifiers, proofs) → execute txs → receipts/roots match.
	7.	Fork choice updates head.
	8.	Light clients verify DA availability; Explorer shows the new head.

⸻

15) Failure Modes & Recovery
	•	Insufficient fees / gas: mempool rejects; clear error over RPC.
	•	Policy mismatch (Θ/Γ/roots): block rejected; bad peer scored down by P2P.
	•	DA proof failure: light client marks unavailable; nodes can refuse finalize until sufficient sampling.
	•	Reorgs: mempool/reorg.py re-injects txs; fork choice consistent.
	•	Attestation parse failures: proof rejected with specific error codes; metrics incremented.

⸻

16) Where to Look (by directory)
	•	spec/: canonical schemas, params, formats, opcodes, JSON-RPC surface.
	•	core/: types, encoding, DBs, block import, head tracking.
	•	consensus/: policy, caps, scoring, difficulty, fork choice.
	•	proofs/: proof schemas, verifiers, attestations, metrics mapping.
	•	mempool/: admission, fee market, eviction, drain.
	•	mining/: templates, search loops, workers, stratum/ws.
	•	da/: NMT/erasure, sampling, retrieval API.
	•	execution/ & vm_py/: state machine + deterministic Python VM.
	•	p2p/: handshake, transports, gossip, sync.
	•	rpc/: FastAPI app, methods, WS, metrics.
	•	randomness/: beacon pipeline.
	•	zk/: verifiers, adapters, registry, native fast paths.
	•	wallet-extension/, sdk/, studio-*: user & developer tooling.

⸻

Appendix: Notation
	•	( H(u) = -\ln(u) ), safe draw from uniform ( u\in(0,1] ) for HashShare.
	•	( \Theta ): current difficulty threshold; retarget via EMA with clamps.
	•	( \psi ): mapped contribution from a verified proof’s metrics; subject to per-type caps and total Γ.

