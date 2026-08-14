# Animica L2 — Running a Node

The L2 is configured entirely through `ANIMICA_L2_*` environment variables
(`l2/config.py::L2Config.from_env()`). Every knob has a safe default, so

```bash
ANIMICA_L2_ENABLE=1 animica node up
```

brings up a working all-in-one dev L2 alongside the L1 node.

## Environment configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANIMICA_L2_ENABLE` | `0` | Master switch. `1/true/yes/on` enables the L2 node inside the process |
| `ANIMICA_L2_MODE` | `all` | Duties: `sequencer` \| `node` (follower/verifier) \| `prover` \| `all` |
| `ANIMICA_L2_CHAIN_ID` | `421337` devnet / `1001` mainnet | L2 chain id. Default follows `ANIMICA_NETWORK` (`mainnet` → 1001, anything else → 421337) |
| `ANIMICA_L2_SETTLEMENT_MODE` | `VALIDITY` | `VALIDITY` \| `OPTIMISTIC` \| `DEV` (see [SECURITY_ASSUMPTIONS.md](SECURITY_ASSUMPTIONS.md)); an unknown value falls back to `VALIDITY` |
| `ANIMICA_L2_DATA_DIR` | `$ANIMICA_DATA_DIR/l2` (i.e. `/data/l2`) | Store directory: batches, DA blobs, WAL, state snapshots |
| `ANIMICA_L2_RPC_PORT` | `8551` | Reserved port for a standalone L2 RPC deployment. In the all-in-one node the `l2_*` methods are served by the node's main JSON-RPC server (default `:8545`) |
| `ANIMICA_L2_P2P_PORT` | `8552` | Reserved L2 p2p port (batch/DA gossip for follower nodes) |
| `ANIMICA_L2_SETTLEMENT_ENABLED` | `0` | Actually submit anchoring txs to L1. Leave off for local dev so nothing is written to L1 |
| `ANIMICA_L2_EXEC_WORKERS` | `0` (auto) | Parallel execution workers; `0` = auto (the node uses 4 when unset) |
| `ANIMICA_L2_SIG_WORKERS` | `0` (auto) | ML-DSA-65 batch-verification workers |
| `ANIMICA_L2_PROOF_WORKERS` | `1` | Proof-generation workers |
| `ANIMICA_L2_BATCH_MAX_TXS` | `50000` | Close the open batch at this many txs |
| `ANIMICA_L2_BATCH_MAX_MS` | `250` | …or when the batch is this old (soft-latency bound) |
| `ANIMICA_L2_BATCH_MAX_BYTES` | `8388608` (8 MiB) | …or at this encoded size (whichever comes first) |
| `ANIMICA_L2_TICK_MS` | `25` | Sequencer tick interval for the driving loop |
| `ANIMICA_L2_MAX_PENDING` | `500000` | Admission queue bound; beyond it `submit` rejects (`QueueFull`) instead of growing without bound |
| `ANIMICA_L2_BRIDGE_ADDRESS` | (network param) | Canonical L1 bridge account (locks deposits, receives anchoring txs) |
| `ANIMICA_L2_L1_RPC_URL` | `http://127.0.0.1:8545` | L1 JSON-RPC the bridge watcher / settlement submitter talks to |

Integer variables accept `0x…` hex too (parsed with base auto-detection).

## Modes

`L2Node` (`l2/node.py`) is one object graph for every role; `ANIMICA_L2_MODE`
selects duties without changing the code path:

- **`all`** (default) — sequencer + prover + RPC in one process. What
  `ANIMICA_L2_ENABLE=1` gives you; right for dev and for the initial 10.0.0
  deployment.
- **`sequencer`** — accepts transactions, orders, executes, commits batches.
- **`node`** — a follower/verifier: syncs batches + DA blobs, re-executes them
  (`l2_verifyBatch` locally), serves read RPC. Runs no sequencing. This is the
  role anyone can run to hold the sequencer honest.
- **`prover`** — generates/checks proofs for committed batches
  (`ANIMICA_L2_PROOF_WORKERS`).

On start the node recovers the canonical head from the store (WAL commit
markers + verified state snapshot, `l2/store.py::recover`); an empty
`data_dir` is genesis.

## Ports and endpoints

| Port | What |
|---|---|
| `8545` | L1 node JSON-RPC — serves all `l2_*` methods when L2 is enabled (`rpc/methods/l2.py`) |
| `8551` | Dedicated L2 RPC (standalone deployments) |
| `8552` | L2 p2p |
| `/metrics` | Prometheus, includes the L2 registry (`l2/metrics.py`) — batches, tx counters, proof timings, queue gauges |

## Quick starts

All-in-one dev node (no L1 writes, DEV-friendly):

```bash
export ANIMICA_L2_ENABLE=1
animica node up
animica l2 status
```

Sequencer that actually settles to L1:

```bash
export ANIMICA_L2_ENABLE=1
export ANIMICA_L2_MODE=sequencer
export ANIMICA_L2_SETTLEMENT_MODE=VALIDITY
export ANIMICA_L2_SETTLEMENT_ENABLED=1
export ANIMICA_L2_BRIDGE_ADDRESS=anim1...        # canonical bridge account
export ANIMICA_L2_L1_RPC_URL=http://127.0.0.1:8545
export ANIMICA_L2_DATA_DIR=/data/l2
animica node up
```

Independent verifier (the trust-minimizing role):

```bash
ANIMICA_L2_ENABLE=1 ANIMICA_L2_MODE=node animica node up
animica l2 verify-batch 42        # re-executes batch 42 from its DA blob
```

## The `animica l2` CLI

The `l2` subcommand group (Typer, `python/animica/cli/`) talks JSON-RPC to a
running node; `--rpc-url` / the usual `ANIMICA_RPC_URL` resolution applies.
`animica l2 --help` is authoritative; the core commands map 1:1 onto the
`l2_*` RPC methods:

| Command | Backing RPC | What it does |
|---|---|---|
| `animica l2 status` | `l2_status`, `l2_getSequencerStatus`, `l2_getSyncStatus` | Node/sequencer health, head batch, pending queue, settlement mode |
| `animica l2 balance <address>` | `l2_getBalance`, `l2_getNonce` | Balance (nanos + ANM) and nonce for a `0x…`/`anim1…` account |
| `animica l2 send …` | `l2_estimateFee`, `l2_sendRawTransaction` | Sign (ML-DSA-65) and submit a transfer/payment; prints txid and tracks status |
| `animica l2 tx <txid>` | `l2_getTransaction`, `l2_getReceipt` | Lifecycle status + receipt |
| `animica l2 withdraw …` | `l2_sendRawTransaction`, `l2_getWithdrawalProof` | Burn on L2 and follow the withdrawal to CLAIMABLE |
| `animica l2 batch <n>` | `l2_getBatch`, `l2_getBatchData` | Batch header / DA blob |
| `animica l2 verify-batch <n>` | `l2_verifyBatch` | Independent re-execution check of a committed batch |
| `animica l2 proof <n>` | `l2_getProofStatus` | Proof backend + status for a batch |
| `animica l2 bench …` | in-process (`l2/bench.py`) | Deterministic benchmark harness on an ephemeral devnet node — never touches real state; see [PERFORMANCE.md](PERFORMANCE.md) |

Errors print to stderr and exit non-zero, matching the rest of the CLI.

## Data directory layout

Under `ANIMICA_L2_DATA_DIR` the store (`l2/store.py`) keeps per-batch headers +
DA blobs, periodic authenticated state snapshots, and the WAL of commit
markers. Commits are atomic (temp file → fsync → `os.replace` → WAL marker
append+fsync): a crash at any point leaves the previous consistent head
intact. Old snapshots are pruned (`prune_snapshots`, default keep 16); batch
headers + blobs are the replayable history — treat the whole directory as the
unit for backup.
