# Animica Quantum Work Blueprint

This document defines a practical, incremental approach to integrating "useful quantum work" into Animica, leveraging the existing PoIES consensus, DA/NMT for large data, and the deterministic Python VM for on-chain logic.

Summary:
- Quantum work happens off-chain, classical chain stores job specs, commitments, results and attestations.
- Validation on-chain must be deterministic, cheap (bounded gas), and auditable.
- Start with "trusted providers" (registered, staked workers) then iterate to committee verification and eventually cryptographic proofs.

## 0. Ground rules / constraints

- Quantum work is off-chain. The chain is classical; it stores only compact job specs and compact verified receipts.
- The on-chain system accepts a job spec (with input commitment) and a signed result/receipt.
- Inputs and large blobs live in DA/NMT; the job references the DA commitment.
- Verification must be deterministic and gas-bounded. Do not attempt to re-run quantum circuits on-chain.
- Security model is explicit: Stage 1 = trusted, staked workers; Stage 2 = committee consensus; Stage 3 = cryptographic proofs.

## 1. Canonical job & receipt formats

We define canonical JSON/CBOR schemas for job specs and receipts. These are the inputs to contracts and RPC methods.

High-level fields (see schema files in `schemas/`):

Job spec (quantum_job):
- job_id: hex (SHA256 or NMT root)
- owner: address
- program_id: token/string (from approved list)
- input_commitment: hex (NMT root pointing to DA)
- backend_type: enum (ion_trap, superconducting, simulator, neutral_atom, etc.)
- shots: integer
- tolerance: object (e.g. {"energy_tolerance": 1e-3})
- payment_max: uint (in ANM wei)
- deadline_block: uint
- committee_size: optional uint (for committee verification)
- min_agreement_ratio: optional float
- proof_scheme: optional string (hooks for future zk/mahadev)
- metadata: optional map

Receipt / result (quantum_result):
- job_id: hex
- worker_id: address
- result_data: map (application-specific; e.g., best_bitstring, energy, distribution_commitment)
- result_commitment: hex (NMT root if result is large in DA)
- metadata: device, error_rates, runtime, shots
- worker_signature: bytes (PQ signature over canonical bytes)
- proof: optional (snark/proof transcript)
- timestamp_block: block number of submission

These schemas live as JSON Schema files (`schemas/quantum_job.schema.json`, `schemas/quantum_result.schema.json`) and must be used by off-chain clients, explorer UI, and contracts for canonical validation.

## 2. Use DA for heavy data

- Inputs, circuit manifests, and any large results are uploaded to the DA (Data Availability) layer and referenced by NMT root / commitment in the on-chain job spec.
- Contracts verify only that the commitment is well-formed and within size or TTL constraints; the full blob is fetched by workers / verifiers off-chain.
- Standardize a `program_id` registry: small manifests (DA or static docs) describe allowed programs and canonical canonicalization rules.

## 3. Python contracts (on-chain)

We propose 2 principal contracts plus optional aggregator:

### 3.1 `QuantumWorkers` (registry)
- Methods:
  - register_worker(pubkey, metadata) -> worker_id
  - stake(worker_id, amount)
  - slash(worker_id, reason)
  - update_reputation(worker_id, delta)
  - get_worker(worker_id) -> struct
- Storage:
  - workers: mapping(worker_id -> {pubkey, stake, status, reputation, metadata})
- Notes:
  - py-VM should store PQ public keys for signature verification (use canonical byte formats)
  - slashing requires governance / admin or committee trigger

### 3.2 `QuantumJobs` (manager)
- Methods:
  - submit_job(job_spec) -> job_id
  - submit_result(job_id, result_blob) -> receipt event
  - dispute_result(job_id, evidence)
  - finalize_job(job_id)  # internal path when accepted
  - view getters: getJob, getReceipt, listJobs
- Storage:
  - jobs[job_id] = {owner, spec, status, escrowed_amount, submissions[]}
  - submissions: list of {worker_id, result_commitment, signature, status}
- Behavior:
  - submit_job checks fee escrow and allowed program_id; emits JobSubmitted
  - submit_result verifies worker registration, signature, deadlines, basic sanity checks; then either accepts or places under dispute / committee
  - on accept: status=completed, pay worker from escrow and emit JobCompleted

### 3.3 `QuantumCommittee` (optional)
- Manage multi-worker consensus for high-assurance jobs. Tracks votes/submissions and finalizes when quorum is reached.

## 4. Verification strategy (incremental)

Design contracts to accept different verification branches by `proof_scheme`.

### Stage 1 (Trusted, staked workers)
- Acceptance checks performed on-chain:
  - job exists and open
  - worker registered and not slashed
  - PQ signature valid (verify bytes) — deterministic
  - deadline not passed
  - result_commitment present and well-formed
- If above holds, contract accepts and pays worker. Disputes handled off-chain or via governance.
- Cheap, deterministic verification; good for early adoption.

### Stage 2 (Committee)
- submit_result records multiple submissions.
- Contract aggregates submissions using canonical compression (e.g., result_digest = hash(result_summary)).
- When k submissions reach min_agreement_ratio, finalize via consensus.
- Slash outliers and distribute rewards.

### Stage 3 (Cryptographic proofs)
- Add `proof_scheme` field and verifier hooks in the contract.
- If `proof` included, run VM-based verifier (deterministic, gas-bounded) or use precompiled verifier logic.
- Examples: SNARK verification, Mahadev transcript checks, or verifying a signature from a committee aggregator.

## 5. Miner incentives & PoIES

Two integration options:

### 5.1 Fee-only (simple)
- Worker gets paid from job escrow on acceptance. Miners earn normal fees and include txs as usual.
- No changes to PoIES.

### 5.2 Useful-work score (PoIES integration)
- Define S_quantum(block) = Σ weight(job) for jobs completed in the block.
- RewardPolicy contract computes a bonus share for miner based on S_quantum and α parameter.
- Consensus rule: block producer must set coinbase according to RewardPolicy output for that block. Validator nodes verify applicability by reading included JobCompleted events and recomputing S_quantum.
- This ties useful quantum work to block reward and gives miners incentive to include quantum receipts.

## 6. Concrete UX flows

### 6.1 Job submitter
1. User uploads inputs to DA; gets `input_commitment`.
2. Build `job_spec` (program_id, shots, tolerance, payment_max, deadline_block, optional committee parameters).
3. Call `QuantumJobs.submit_job(job_spec)` with escrowed payment.
4. Listen for `JobSubmitted(job_id)` and then `JobCompleted(job_id, worker_id, summary)`.
5. If dispute, call `dispute_result(job_id, evidence)`.

### 6.2 Worker
1. Register via `QuantumWorkers.register_worker(pubkey, metadata)` and stake.
2. Monitor chain for `JobSubmitted` events.
3. Fetch DA blob data for job.
4. Run quantum algorithm off-chain.
5. Produce `result_data` and `result_commitment` (if large, store to DA).
6. Sign canonical bytes with PQ key.
7. Submit via `QuantumJobs.submit_result(job_id, result_blob)`.
8. Receive payment when finalized.

### 6.3 Miner
- Include job-related txs in blocks. If using RewardPolicy, ensure coinbase matches required reward split.

## 7. Data model & canonical bytes

- Define canonical CBOR ordering for bytes that are signed by PQ keys.
- Signature target: `canonical_bytes = CBOR(job_id || result_commitment || metadata)`
- Workers sign canonical_bytes with PQ key. Contract stores worker_pubkey and verifies signature using deterministic VM verifier (requires PQ verify implemented in VM or via on-chain helper).

## 8. Security considerations

- Slashing must be carefully designed to avoid griefing. Use multi-step slashing or governance arbitration.
- Use deposit/escrow to align incentives.
- For committee-based verification, require stake from committee members and time windows for submission.
- Use time-locks or challenge windows for disputes.

## 9. Implementation plan & next steps

See `TODO` in the repository (root-level task manager). Short roadmap:
1. Add JSON/CBOR schemas to `schemas/`.
2. Add contract skeletons to `contracts/examples/quantum`.
3. Implement PQ signature verification library in VM (or via precompile) — research required.
4. Add RPC indexing for quantum events and explorer UI pages.
5. Add wallet flows (submit job, view jobs, submit result helper).
6. Integrate RewardPolicy and miner verification if desired.

## 10. Testing and monitoring

- Unit tests for contract logic (deadlines, escrow, slashing).
- Integration tests with mock workers and PQ keys.
- End-to-end tests: user submits job, worker submits result, contract finalizes and pays.
- Monitoring: Job queue length, average turnaround time, dispute rate, slashing events.

---

Appendix: Schemas and example canonical bytes in `schemas/` and contract skeletons in `contracts/examples/quantum/`.
