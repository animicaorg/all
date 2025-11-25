# Animica Glossary — Canonical Definitions

Authoritative terms used across **specs**, **code**, **SDKs**, and **docs**. If a term here conflicts with another doc, **this page wins** unless the spec explicitly states otherwise.

> Conventions: math symbols appear as code (e.g., `Θ`, `ψ`, `Σψ ≥ Θ`). ASCII fallbacks: `Theta`, `psi`, `Gamma`.

---

## Core consensus (PoIES)

- **PoIES** — *Proof-of-Integrated External Signals*; consensus model that aggregates heterogeneous *useful-work* and classical proofs per block.
- **`Θ` (Theta)** — acceptance threshold. A candidate block is accepted if the aggregated score meets/exceeds `Θ`.
- **`ψ` (psi)** — non-negative contribution from a single proof after caps and mapping rules (units: micro-nats in math notes).
- **`Γ` (Gamma)** — aggregate cap limiter. Enforces global/per-type ceilings on the sum of `ψ` to ensure fairness and anti-whale behavior.
- **Acceptance predicate** — canonical inequality `Σψ ≥ Θ`.
- **Retarget (difficulty)** — fractional EMA that updates `Θ` toward a target block interval; bounded by stability clamps.
- **Escort / diversity rules** — policy knobs that prevent any single proof type/provider from monopolizing block selection.
- **α tuner** — slow-moving fairness corrector that nudges weights across proof types to maintain long-run equity.
- **Micro-target share** — micro-difficulty used to classify a proof’s *share* quality vs. current `Θ` (used for receipts/metrics).

---

## Proof types & envelopes

- **Proof envelope** — uniform container `{type_id, body, nullifier}` plus typed metrics. Hash-stable, CBOR/JSON-schema’d.
- **HashShare** — classical hash-based PoW-like share tied to header fields (`u`-draw, target ratio, mix/nonce domain).
- **AIProof** — attested useful-work proof for AI jobs (TEE evidence + trap receipts + QoS inputs).
- **QuantumProof** — attested quantum compute proof (provider cert, trap-circuit stats, QoS).
- **StorageHeartbeat** — storage availability proof (PoSt heartbeat; optional retrieval tickets).
- **VDFProof** — Wesolowski VDF proof with seconds-equivalent estimate (can serve as beacon input).
- **Nullifier** — domain-separated digest preventing double-use of the same proof body over a TTL window.

---

## Attestations & metrics

- **TEE** — Trusted Execution Environment (e.g., Intel SGX/TDX, AMD SEV-SNP, Arm CCA).
- **TEE quote/report** — vendor-signed measurement evidence bound to a job/input; parsed and verified per vendor roots.
- **Provider cert (QPU)** — identity & capability document for quantum providers (hybrid X.509 / EdDSA / PQ).
- **Trap circuits** — randomly selected circuits/run subsets used to detect cheating; yields *traps ratio*.
- **QoS metrics** — availability/latency/success rates incorporated into `ψ` mapping (pre-cap).

---

## Data availability (DA)

- **Blob** — arbitrary payload posted to the DA service; chunked/erasure-encoded and namespaced.
- **NMT (Namespaced Merkle Tree)** — Merkle construction that preserves namespace ranges for efficient inclusion/range proofs.
- **Commitment** — NMT root of the erasure-extended matrix; placed in the block’s DA root.
- **Erasure coding** — RS(k,n) expansion enabling recovery from any `k` shares; positions prove via NMT.
- **DAS (Data Availability Sampling)** — probabilistic sampling of shares with verification of NMT branches to assert availability.
- **DA root** — header field committing to all accepted blobs’ NMT roots.

---

## Zero-knowledge (ZK) primitives

- **BN254** — pairing-friendly curve used by Groth16/PLONK/KZG in this repo (alt name: alt_bn128).
- **Pairing** — bilinear map `e: G1×G2→GT` (Ate pairing in BN254).
- **KZG** — polynomial commitment scheme using pairings; supports single-opening in our minimal verifier.
- **Groth16** — pairing-based zkSNARK with succinct constant-size proofs and circuit-dependent vk.
- **PLONK (KZG)** — polynomial-I/O zkSNARK with universal setup; demo verifier supports single-opening flow.
- **Poseidon** — algebraic hash over prime fields; used inside circuits and transcripts.
- **Transcript (Fiat–Shamir)** — deterministic transcript producing challenges by hashing prior messages (domain-separated).
- **Verification key (vk)** — circuit-specific metadata for verification (curve points, domain params); pinned via registry.
- **Circuit ID** — stable identifier for a circuit/vk pair (hashed metadata; used for policy allowlists).

---

## Execution & VM(Py)

- **Tx (transaction)** — CBOR-encoded action with *kind* (transfer/deploy/call), access list, PQ signature, gas fields.
- **Receipt** — result record of execution (status, gas used, logs, bloom/hash).
- **Intrinsic gas** — upfront gas charged based on tx kind and payload size before execution.
- **ABI (Python-VM)** — JSON schema describing functions/events/errors for VM(Py) contracts.
- **Manifest** — deploy bundle descriptor (code/ABI/caps/resources) for deterministic packaging.
- **Gas meter/refund** — deterministic accounting for gas debit/refund; OOG halts with receipt.
- **Access list** — hints about storage/key touches to aid scheduling and fee policy.

---

## P2P, RPC, mempool

- **Peer ID** — hash of node identity pubkey (PQ) + alg_id.
- **Handshake (P2P)** — Kyber768 KEM + HKDF-SHA3-256 → AEAD keys; transcript hash for binding.
- **AEAD** — authenticated encryption (ChaCha20-Poly1305 / AES-GCM in transports).
- **Gossip topics** — typed pubsub channels (headers, blocks, txs, shares, blobs).
- **Pending pool / mempool** — admission, sequencing, replacement policy with dynamic min-fee watermark.
- **JSON-RPC / OpenRPC** — node API surface and its machine-readable spec.

---

## Post-quantum (PQ) crypto

- **Dilithium3** — PQ signature scheme (default signer).
- **SPHINCS+ SHAKE-128s** — stateless hash-based signature alternative.
- **Kyber-768** — KEM used in handshakes (P2P and demos).
- **HKDF-SHA3-256** — key derivation for sessions and P2P.
- **bech32m** — address encoding with HRP `anim`; payload = `alg_id || sha3_256(pubkey)`.

---

## Randomness beacon

- **Commit–reveal** — two-phase scheme: commit hash, reveal preimage; aggregated reveals feed VDF input.
- **Wesolowski VDF** — time-delay function with succinct proof; verified by consensus.
- **Beacon output** — finalized randomness for the next epoch/round, optionally mixed with QRNG bytes.

---

## AICF (AI Compute Fund)

- **Provider** — registered compute entity (AI/Quantum) with stake, capabilities, and endpoints.
- **Stake / lockup** — provider-posted security deposit enabling assignments; subject to slashing.
- **Job** — queued request (AI/Quantum). **Lease** — assignment of a job to a provider with renewal/expiry.
- **SLA** — metrics and thresholds (traps, QoS, latency) for payouts/slashing.
- **Settlement** — batched payouts credited to provider balances; governed by `Γ_fund` caps.
- **Slash event** — penalty on failing SLA or invalid proofs/attestations.

---

## Headers, blocks, state

- **Header** — canonical block header structure (roots, `Θ`, nonce domain, mix seed, chainId).
- **Block** — header + txs + proofs (+ optional receipts).
- **State root** — Merkleized commitment to account/storage state after execution.
- **Fork choice** — weight-aware longest-chain rule with deterministic tie-breakers.
- **Head** — current best block per fork choice.

---

## Schemas & formats

- **CDDL** — Concise Data Definition Language for CBOR schemas (tx/header/blob/proof bodies).
- **JSON-Schema** — validation for ABI, vk, and various API payloads.
- **OpenRPC** — machine-readable description of RPC methods.
- **Canonical JSON/CBOR** — deterministic field order and number/string rules for hash-stability.

---

## Policy & registry

- **PoIES policy** — mapping rules from verified metrics to `ψ`, per-type caps, escort/diversity parameters, `Γ` total cap.
- **PQ alg-policy** — allowlist and deprecation weights for signature/KEM algorithms; hashed into a root.
- **VK registry** — signed index of `(circuit_id → vk record)` with integrity hash and optional signatures.

---

## Fees & market

- **Base fee / tip** — split model for tx pricing; dynamic floor via EMA on recent blocks.
- **Surge multiplier** — ceiling applied under congestion.
- **Watermark** — rolling min-fee threshold guiding eviction and admission.

---

## Domains & hashing

- **Domain separation** — unique tags/input layouts per operation (signing, nullifiers, transcripts) to prevent cross-protocol confusion.
- **SignBytes** — canonical bytes of an object used for signatures (deterministic encoder).

---

## Chain & addressing

- **Chain ID** — CAIP-2 styled identifier (e.g., `animica:1` mainnet, `:2` testnet, `:1337` devnet).
- **Address** — bech32m (`anim1…`) derived from pubkey and `alg_id`.

---

## Error classes (selected)

- **ConsensusError** — invalid header/block under policy/`Θ` rules.
- **PolicyError** — policy root mismatch or illegal parameterization.
- **ProofError / AttestationError** — malformed/invalid proof or attestation chain.
- **AdmissionError / FeeTooLow / NonceGap** — mempool rejections.
- **InvalidTx / ChainIdMismatch** — RPC-level structured errors.

---

## Notation cheatsheet

- `Σψ` — sum of all accepted proof contributions in a candidate block.
- `ψ_i` — contribution from the *i-th* proof after mapping and caps.
- `Θ_t` — threshold at time/window `t` determined by retarget.
- `Γ_total` / `Γ_type` — global vs per-type caps.
- `H(u) = −ln(u)` — *u-draw* mapping used in HashShare probability model.

---

## See also

- **Specs:** `spec/*.cddl`, `spec/*.json`, `spec/*.yaml`  
- **ZK docs:** `zk/docs/*` (architecture, formats, security)  
- **DA specs:** `da/specs/*`  
- **VM(Py) specs:** `vm_py/specs/*`

> Proposing a new term? Open a PR to add it here, include the formal definition, and reference affected modules.

