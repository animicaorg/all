# core/

Core provides the *deterministic substrate* for Animica nodes:
types, canonical encodings, genesis loading, persistent stores, and the minimal
block–import/head plumbing that every higher-level service (RPC, P2P,
Consensus/PoIES, DA, VM, AICF, …) builds upon.

It is deliberately small and *boring*: if it parses, hashes, persists, or
computes a Merkle root, it probably lives here.

---

## What lives here

- **Types** — canonical dataclasses for `Tx`, `Receipt`, `Header`, `Block`,
  and proof envelopes (opaque here).
- **Encodings** — deterministic CBOR (matching `spec/*.cddl`) and a
  canonical **SignBytes** encoder used for hashing/signing.
- **Genesis** — load/validate the genesis file, initialize state DB, compute
  `stateRoot`, build the genesis header, and persist it atomically.
- **Databases** — a backend-agnostic KV interface with:
  - Embedded **SQLite** (default) tuned for WAL + integrity.
  - Optional **RocksDB** (feature-gated, graceful if missing).
- **Chain plumbing** — block import skeleton, canonical head pointer, minimal
  fork-choice placeholder (height, hash tie-break) for early bring-up.
- **Utilities** — hashing (SHA3/Keccak/Blake3), Merkle helpers, canonical JSON.

> Higher-level logic (PoIES scoring, difficulty/Θ, nullifiers, DA, VM, etc.)
> is out-of-scope and integrated via adapters.

---

## Dependencies

- **Python** ≥ 3.11 (deterministic `int` semantics, `typing` niceties).
- **System**: SQLite (stdlib), optional `python-rocksdb` if you enable RocksDB.
- **Python packages** (pinned in repo root):
  - `msgspec` (fast structured (de)serialization)
  - `cbor2` (test vectors + tooling; runtime uses our canonical codec)
  - `blake3` (optional, accelerated hashing)
  - `pyyaml`, `pydantic` (config/validation)
  - `click`/`typer` (CLIs), `rich` (pretty output, optional)

---

## Design invariants

1. **Deterministic encoding**: CBOR canonical form, sorted map keys, no
   float/NaN, bounded lengths, explicit big-int representation.
2. **Domain separation**: All hash/sign bytes include a domain tag from
   `spec/domains.yaml`.
3. **Stable roots**: `stateRoot`, `txsRoot`, `proofsRoot`, `receiptsRoot`
   are computed via canonical Merkle procedures defined here.
4. **Pure types**: Type definitions carry no I/O; persistence is done in `db/`.
5. **Atomicity**: Genesis and block-import DB writes are transactional.

---

## Data model (high level)

- **Tx**: transfer | deploy | call. Contains `chainId`, `nonce`, `gas`,
  `fee`, `to`, `value`, `accessList`, and a *PQ signature* (bytes + alg_id).
- **Header**: parent hash, roots, `height`, `timestamp`, `chainId`,
  **Θ** (consensus difficulty target), `nonce`, `mixSeed` (entropy domain),
  policy roots (PoIES/PQ/DA), and optional aux data.
- **Block**: header + ordered txs + (opaque) proofs; receipts optional in
  storage (not in consensus hash).
- **Receipts**: status/gasUsed/logs root; lives off to the side in DB.

Exact shapes match:
- `spec/tx_format.cddl`
- `spec/header_format.cddl`

---

## Encodings

- **CBOR**: `core/encoding/cbor.py` enforces canonical form and is used
  for on-disk and network object encoding.
- **SignBytes**: `core/encoding/canonical.py` derives the byte string used by
  hash/sign domains. It *must* match the CDDL fields and ordering.

---

## Databases

`core/db/kv.py` defines logical column families with byte prefixes:

| Prefix | Bucket          | Contents                                 |
|-------:|-----------------|------------------------------------------|
| `m:`   | meta            | chain params, head pointers, version     |
| `h:`   | headers         | height/hash → header bytes               |
| `b:`   | blocks          | height/hash → block body bytes           |
| `r:`   | receipts        | txHash → receipt bytes                   |
| `t:`   | tx_index        | txHash → (height, index)                 |
| `s:`   | state           | account/storage snapshots (key→value)    |

Backends:
- `core/db/sqlite.py` (default)
- `core/db/rocksdb.py` (optional, loaded if available)

---

## Genesis

`core/genesis/genesis.json` contains:
- chain params (subset from `spec/params.yaml`)
- pre-funded accounts, nonces
- policy roots (PoIES, PQ alg-policy, DA)
- genesis timestamp/height=0 parentRoot=0…

`core/genesis/loader.py`:
1. Validates schema and chainId (vs `spec/chains.json`).
2. Computes deterministic **stateRoot** from ordered state entries.
3. Builds the **genesis header**, persists header/block atomically.
4. Initializes meta/head pointers.

Minimal example (truncated):

```jsonc
{
  "chainId": 1,
  "timestamp": 1735689600,
  "params": { "blockGasLimit": 20_000_000, "targetIntervalMs": 2000 },
  "policyRoots": {
    "poies": "0x…",
    "pqAlg": "0x…",
    "da":  "0x…"
  },
  "alloc": [
    {"address": "anim1…", "balance": "1250000000000000", "nonce": 0}
  ]
}


⸻

Boot sequence
	1.	Create a venv & install deps (repo root)

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

	2.	Initialize a DB from genesis

python -m core.boot \
  --genesis core/genesis/genesis.json \
  --db sqlite:///animica.db

	3.	Inspect the head

python -m core.cli_demo --db sqlite:///animica.db

You should see the chain params and the genesis header hash/height.

⸻

Configuration

core/config.py loads from env, CLI, or defaults:
	•	ANIMICA_DB — e.g., sqlite:///animica.db or rocksdb:///./db
	•	ANIMICA_CHAIN_ID — must match genesis & spec/chains.json
	•	ANIMICA_LOG — info|debug|json
	•	ANIMICA_HASH_IMPL — sha3|keccak|blake3 (policy-dependent)
	•	ANIMICA_STORE_CHECKS — strict|off (DB pragmas/integrity)

⸻

CLI helpers
	•	Boot: python -m core.boot --genesis … --db …
	•	Demo: python -m core.cli_demo --db … (prints params/head)

⸻

Integration points
	•	Consensus will call into:
	•	core/chain/block_import.py: structural checks (parents, roots present),
header linking, persistence, head update hook.
	•	core/chain/head.py: read/update canonical head.
	•	RPC reads via:
	•	core/db/block_db.py, core/db/tx_index.py, core/db/state_db.py.
	•	P2P decodes via:
	•	core/encoding/cbor.py + type constructors in core/types/*.
	•	Wallet/SDK rely on:
	•	core/encoding/canonical.py for SignBytes consistency.
	•	PQ addresses: format validated by pq module; core treats addresses as
opaque 32-byte payloads with bech32m UI in clients.

⸻

Testing

From repo root:

pytest -q core

What’s covered:
	•	deterministic CBOR round-trips
	•	genesis load → stable stateRoot
	•	head pointers & block import skeleton
	•	Merkle & hashing helpers

⸻

Security & correctness notes
	•	No clocks in consensus paths: timestamps are data, not authority.
	•	No floats: all economics/gas use integers and fixed-point helpers.
	•	Hash domains: keep spec/domains.yaml in sync; changing it changes everything.
	•	DB atomicity: all state-affecting writes are transaction-bound; crash-safe.

⸻

Performance notes
	•	SQLite is tuned for WAL + PRAGMA synchronous=NORMAL by default.
	•	CBOR encoding is zero-copy where possible via msgspec.
	•	RocksDB offers higher throughput for archival nodes (optional).

⸻

Roadmap (beyond core)
	•	Wire PoIES difficulty & Θ into headers (via consensus module).
	•	Add receipts pruning/snapshots.
	•	State tries & proofs (for light clients) — spec-aligned but implemented in a later milestone.

⸻

Troubleshooting
	•	ChainIdMismatch: ensure ANIMICA_CHAIN_ID, genesis, and spec/chains.json agree.
	•	DB locked: another process is holding the SQLite file; use a different path or stop the other process.
	•	Hash mismatch: verify you didn’t modify spec/*.cddl or spec/domains.yaml without rebuilding vectors.

⸻

License

See repository root LICENSE. third-party attributions in module roots as applicable.

