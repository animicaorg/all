# Animica L2 — Architecture

Animica 10.0.0 adds an ANM-native Layer 2: a high-throughput payment rollup that
settles to Animica L1. It lives in the top-level `l2/` package. This document
maps each component to its module and shows how a transaction flows from wallet
to L1 finality.

## Design goals

- **ANM-native.** The only asset is ANM (integer nanos, `NANOS_PER_ANM = 10^9`,
  identical to L1). No new token, no VM — a fixed set of payment-shaped
  transaction types (`l2/constants.py::TxType`).
- **Post-quantum end to end.** Every user transaction is signed with ML-DSA-65
  (the L1-canonical `0x1003` scheme). Signature = 3309 bytes, pubkey = 1952
  bytes; the size dominates the wire format, and the DA layer exists largely to
  amortize it.
- **Money can never be minted on L2.** The bridge conservation invariant
  (`l2/bridge.py`) ties every L2 nano to ANM locked on L1.
- **Deterministic everywhere.** Two nodes replaying the same batch produce
  identical roots regardless of core count, insertion order, or scheduling.
- **Honest trust model.** 10.0.0 runs a designated sequencer; see
  [SECURITY_ASSUMPTIONS.md](SECURITY_ASSUMPTIONS.md) for exactly what is and is
  not trusted.

## Components

| Component | Module | Responsibility |
|---|---|---|
| Constants / enums | `l2/constants.py` | Chain ids (mainnet 1001 / devnet 421337), tx types, lifecycle states, `SettlementMode`, size limits, fork height `FORK_L2_ANCHOR_HEIGHT = 80_000`, `L2_TREASURY_ADDRESS` |
| Wire codec | `l2/codec.py` | Minimal varint/length-prefixed primitives shared by tx encoding and DA |
| Transactions | `l2/tx.py` | `L2Tx` dataclass, payload types, canonical `encode()`/`decode()`, `signing_hash()` (domain-separated `animica.l2.tx.v1`), `txid()`, `address_from_pubkey()` |
| Crypto | `l2/crypto.py` | Batched ML-DSA-65 verification (`get_verifier()`), worker pool |
| State | `l2/state.py` | Authenticated account state: depth-256 Sparse Merkle Tree keyed by the 32-byte address; membership **and** non-membership proofs; copy-on-write per batch |
| Executor | `l2/executor.py` | Deterministic parallel execution: account-conflict graph → connected components → components run in parallel, txs inside a component serially in sequencer order. Result equals a sequential run for any worker count |
| Fees | `l2/fees.py` | Deterministic fee schedule `base + da_per_byte·bytes + exec_per_unit·units`; fees accrue to the L2 treasury (see [FEES.md](FEES.md)) |
| Batch | `l2/batch.py` | `BatchHeader` (prev/new state roots, transactions/receipts/escrow/data roots, fee/deposit/withdraw aggregates), `ClosurePolicy` (close on tx count OR byte size OR age), `BatchBuilder` |
| Data availability | `l2/da.py` | Self-describing compressed batch blobs from which anyone reconstructs the exact tx list and re-derives the state root (see [DATA_AVAILABILITY.md](DATA_AVAILABILITY.md)) |
| Proof system | `l2/proof.py` | Pluggable backends behind one `ProofBackend` interface: `ReExecutionValidityBackend` (VALIDITY), `OptimisticBackend` (OPTIMISTIC), `DevBackend` (DEV). `ProofPublicInputs` is backend-independent |
| Bridge | `l2/bridge.py` | Deposits (reorg-safe confirmation tiers), withdrawals (nullifiers), forced inclusion/exit queue, and the conservation invariant `check_invariant()` |
| Sequencer | `l2/sequencer.py` | The pipeline driver: admission (decode → validate → dedupe → sig verify → nonce/balance), ordering, batch close, execute, DA encode, prove, commit, settle hooks. Synchronous core (`submit`/`tick`) |
| Store | `l2/store.py` | Durability: per-batch atomic commit (temp-file + fsync + `os.replace` + WAL commit marker), periodic authenticated state snapshots, crash recovery to the last consistent head. Never one fsync per tx |
| Node | `l2/node.py` | `L2Node`: all-in-one object graph (store → recover → sequencer). Process-wide singleton `get_l2_node()` shared by RPC/CLI/SDK |
| Config | `l2/config.py` | `L2Config.from_env()` — every `ANIMICA_L2_*` knob (see [RUNNING.md](RUNNING.md)) |
| Metrics | `l2/metrics.py` | `L2_METRICS` counters/gauges/histograms on a dedicated Prometheus `CollectorRegistry` |
| Bench | `l2/bench.py` | Deterministic load generator + harness measuring the real pipeline (see [PERFORMANCE.md](PERFORMANCE.md)) |
| RPC surface | `rpc/methods/l2.py` | All `l2_*` JSON-RPC methods (`l2_chainId`, `l2_status`, `l2_sendRawTransaction(s)`, `l2_getBatch`, `l2_getAccountProof`, `l2_verifyBatch`, …) served by the node's FastAPI RPC server |

## Transaction pipeline

```
 wallet / SDK
      │  signed L2Tx (ML-DSA-65 over signing_hash, domain animica.l2.tx.v1)
      ▼
┌─────────────────────────────  SEQUENCER (l2/sequencer.py)  ─────────────────────────────┐
│                                                                                          │
│  submit ─► decode ─► cheap validation ─► dedupe ─► signature verify ─► nonce/balance     │
│  (l2/tx.py)          (limits, chain id)            (l2/crypto.py,      admission         │
│                                                     batched)                             │
│        ····· tx is now SOFT_CONFIRMED; ordered into the open batch ·····                 │
│                                                                                          │
│  tick ──► ClosurePolicy: close on max_txs | max_bytes | max_age_ms (l2/batch.py)         │
│                │                                                                         │
│                ▼                                                                         │
│        EXECUTE (l2/executor.py)          conflict components in parallel,                │
│                │                         deterministic == sequential order               │
│                ▼                                                                         │
│        STATE (l2/state.py)               SMT update → new_state_root                     │
│                │                                                                         │
│                ▼                                                                         │
│        DA ENCODE (l2/da.py)              address dictionary + zlib → blob, data_root     │
│                │                                                                         │
│                ▼                                                                         │
│        PROVE (l2/proof.py)               backend by SettlementMode → Proof               │
│                │                                                                         │
│                ▼                                                                         │
│        COMMIT (l2/store.py)              atomic: blob + header + snapshot + WAL marker   │
└────────────────┬─────────────────────────────────────────────────────────────────────────┘
                 │  batch header (roots + aggregates) + DA blob
                 ▼
        SETTLE to Animica L1 (l2/bridge.py conventions)
        state-root/DA commitments as L1 txs to the bridge address (memo ANML2C1);
        deposits arrive as L1 transfers to the bridge address (memo ANML2D1)
                 │
                 ▼
        L1 confirmation tiers: OBSERVED → CONFIRMED (12) → FINALIZED (64)
        tx status: BATCHED → PROVEN → L1_SUBMITTED → L1_FINALIZED
```

## The L1 ↔ L2 relationship

L2 does not change L1 consensus in 10.0.0 — anchoring is **additive**:

- **Deposits** are ordinary L1 transfers to the canonical bridge account
  carrying an `ANML2D1` magic memo. The L1 executor treats the memo as opaque,
  so no L1 change is needed to make deposits observable. The bridge credits a
  deposit on L2 only once it is **FINALIZED** on L1 (64 blocks deep,
  `L1_FINALITY_DEPTH`), so an L1 reorg can never mint unbacked L2 ANM.
- **Commitments** (batch state roots + DA blobs) ride the same rail with memo
  magic `ANML2C1`.
- **Fork gate.** At L1 height `FORK_L2_ANCHOR_HEIGHT = 80_000`
  (`FORK_L2_ANCHOR`), anchoring transactions become consensus-interpreted:
  full nodes advertising 10.0.0 validate them. Below that height they are
  opaque memos indexed off-consensus.
- **Withdrawals** burn on L2 and produce a unique nullifier
  (`sha3_256("animica.l2.withdraw.nullifier.v1" || l2_txid)`); the L1 claim
  spends the nullifier exactly once after the containing batch is
  L1-finalized. Double claims are rejected, and a claim that would exceed the
  locked balance is refused as an invariant violation.
- **Forced inclusion / exit** requests are enqueued via L1 so a censoring
  sequencer cannot trap funds — see [FORCED_EXITS.md](FORCED_EXITS.md).

Replay is structurally impossible in both directions: the L2 chain id
(mainnet 1001, devnet 421337) is distinct from L1's, and it is part of every
signing preimage together with the `animica.l2.tx.v1` domain tag.

## Determinism guarantees (why re-execution works)

Three properties make any third party able to reproduce a batch bit-for-bit:

1. **Canonical encoding** — one byte representation per tx (`l2/codec.py`,
   `l2/tx.py`); DA reconstruction is strict and rejects trailing bytes.
2. **Deterministic execution** — the executor's component-parallel schedule is
   provably equivalent to a sequential run of the sequencer's total order; the
   fee schedule is protocol-fixed; every quantity is a Python int (floats are
   banned).
3. **Deterministic commitments** — the SMT root is independent of insertion
   order; the batch header hashes only consensus data (the sequencer timestamp
   is deliberately *excluded* from `batch_id()`).

This is what the VALIDITY settlement mode's verifier
(`ReExecutionValidityBackend.verify`) relies on: DA blob + previous state in,
identical roots out — or the batch is invalid.

## Deployment shapes

`L2Node` (`l2/node.py`) is one object graph used for every role; `mode` in
`l2/config.py` selects duties (`sequencer` / `node` / `prover` / `all`) without
rewriting anything. The synchronous sequencer core is wrapped by the node
service integration (RPC server startup hook) which drives `tick()` on
`tick_interval_ms` and owns settlement submission. See
[RUNNING.md](RUNNING.md).
