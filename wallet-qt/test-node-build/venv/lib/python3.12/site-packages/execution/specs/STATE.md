# STATE.md — Account/Storage Model & Merkle Roots

This document is the normative description of Animica’s **execution state model** and the way **state roots** are computed and committed into block headers. It must match the code in:

- `execution/state/{accounts,storage,view,journal,snapshots}.py`
- `execution/runtime/{transfers,contracts,fees}.py`
- `execution/receipts/{builder,logs_hash}.py`
- `core/chain/state_root.py`, `core/utils/{merkle,hash}.py`, `core/db/state_db.py`
- `core/types/{header,block}.py` (where roots are placed)

Determinism requirement: given identical inputs (params, block context, previous state, transaction list), all nodes produce byte-identical roots.

---

## 1) Address & Keys (canonical bytes)

**Address (internal key form).** Accounts are keyed by the **address payload** used on-chain:

addr_bytes = alg_id || sha3_256(pubkey)

- `alg_id`: 1 byte canonical algorithm id (e.g., Dilithium3, SPHINCS+).
- The external human address is `bech32m("anim", addr_bytes)`, but **all state/storage sorting uses raw `addr_bytes`** (lexicographic by byte).

**Storage key.** Contract/account storage is a dictionary from `key: bytes` to `value: bytes`. Keys are arbitrary byte strings up to `params.storage_key_max_len`. Values up to `params.storage_value_max_len`. Sorting is **lexicographic over raw bytes**.

---

## 2) Account Object (consensus shape)

Each account is a canonical CBOR map with fixed keys:

```cbor
{
  0: nonce       ; uint (u64 domain)
  1: balance     ; uint (u256 domain via CBOR bignum canonical form)
  2: codeHash    ; bstr (32 bytes) — 0x00…00 for EOAs
  3: storageRoot ; bstr (32 bytes) — Merkle root of this account’s storage
}

	•	Nonce increments on any successful state-changing transaction from the account.
	•	Balance is debited/credited by transfers and fee settlement.
	•	codeHash is the canonical content hash of the deployed code (for the Python VM this is the deterministic code digest; EOAs use all-zero).
	•	storageRoot is the per-account storage Merkle root (Section 4). EOAs use the empty root.

Canonical encoding is deterministic CBOR (canonical map ordering, minimal ints). No fields are omitted; zero values are encoded explicitly.

⸻

3) Global State = Map(Address → Account)

The chain state at any height is the ordered map of all existing accounts keyed by addr_bytes. “Existing” means: account is present if it has a non-zero balance or non-zero nonce or non-empty code or a non-empty storage (i.e., storageRoot != EMPTY_MERKLE_ROOT). Pruning is allowed only when these reach the all-zero/empty tuple; such deletions are journaled and reflected in the next root.

⸻

4) Storage Model & Per-Account Storage Root

For each account A, its storage is a set of (key, value) pairs (both bytes) represented as leaves of a canonical Merkle. Empty storage ⇒ storageRoot = EMPTY_MERKLE_ROOT.

4.1 Storage leaf hashing

LeafStorage(key, value) =
  H( "state.storage.leaf" || len(key)||key || len(value)||value )

	•	H = sha3_256.
	•	len(x) is the canonical unsigned length prefix encoded as uvarint (same helper used in core/utils/bytes.py).
	•	Domain tag strings are ASCII, not CBOR, and are concatenated as bytes.

4.2 Storage Merkle

Leaves are the above LeafStorage in lexicographic order by key. Internal node:

Node(l, r) = H( "merkle.node" || l || r )

Single leaf tree ⇒ storageRoot = H("merkle.node" || leaf || leaf).
Empty tree ⇒ EMPTY_MERKLE_ROOT = H("merkle.empty").

⸻

5) Account Leaf & Global State Root

5.1 Account leaf hashing

First compute the account body CBOR bytes acct_cbor from the map in §2.
Then the account leaf:

LeafAccount(addr_bytes, acct_cbor) =
  H( "state.account.leaf" || addr_bytes || acct_cbor )

5.2 Global state Merkle

Sort by addr_bytes (lexicographic). Leaves are LeafAccount. Internal node is the same Node(l,r) as §4.2. The global state root is:

stateRoot = MerkleRoot( [LeafAccount(...)] )

This stateRoot is committed in the block header (see core/types/header.py).

⸻

6) Receipt/Logs Roots (for completeness)
	•	receiptsRoot: Merkle root over receipt hashes (see execution/receipts/logs_hash.py), deterministic order is by transaction index within the block.
	•	logsBloom / logsRoot: Built from events emitted during execution (see execution/state/events.py and execution/receipts/builder.py). These are not part of stateRoot but are siblings in the header root set.

⸻

7) State Transitions (high-level)

For each transaction in block order:
	1.	Prechecks (stateless & stateful): signature domain, chainId, nonce, balance for max cost (intrinsic + worst-case fees).
	2.	Intrinsic gas is charged; a GasMeter is initialized.
	3.	Execution (transfer/deploy/call):
	•	Reads/writes go through execution/state/view.py + journal.py.
	•	Storage writes use bytes→bytes; higher-level typed helpers in the VM are sugar only.
	•	Contract deploy sets codeHash; EOAs keep zero.
	4.	Finalize (fees & refunds): update balances for payer/coinbase/treasury; build Receipt.
	5.	Commit the journal into the KV store, updating accounts and per-account storageRoot.
	6.	After all txs, compute stateRoot over the materialized accounts and place roots in the header.

Reorgs revert by snapshot ids (execution/state/snapshots.py) and recompute roots.

⸻

8) Canonical Serialization & Numbers
	•	CBOR is the only encoding for objects hashed into state: canonical map ordering, definite lengths, minimal integer encoding.
	•	Big integers (balances) follow CBOR bignum rules via the project’s canonical helpers (core/utils/serialization.py).
	•	Zero values: encode as integer 0 or 32-byte zero bstr; never elide fields.

⸻

9) DB Layout (non-consensus but normative for implementation)

Prefixes (see core/db/kv.py):
	•	state:acct:{addr_bytes} → CBOR Account (map §2)
	•	state:stor:{addr_bytes}:{key} → raw value (bytes)
	•	state:code:{codeHash} → code bytes (optional; VM/runtime may store code externally but hash must match)
	•	Derived indexes for iteration are read-only conveniences; consensus depends only on the bytes used to compute roots.

⸻

10) Algorithms (pseudocode)

def storage_root_for_account(addr_bytes, kv_iter):
    # kv_iter yields (key, value) for this addr
    leaves = [ H(b"state.storage.leaf" + uvarint(len(k)) + k
                                   + uvarint(len(v)) + v)
               for (k, v) in sorted(kv_iter, key=lambda kv: kv[0]) ]
    return merkle_root(leaves)  # Node = H("merkle.node"||L||R), empty = H("merkle.empty")

def account_leaf(addr_bytes, account_map_cbor):
    return H(b"state.account.leaf" + addr_bytes + account_map_cbor)

def compute_state_root(accts_iter):
    # accts_iter yields (addr_bytes, account_cbor)
    leaves = [ account_leaf(a, cbor) for (a, cbor) in sorted(accts_iter, key=lambda it: it[0]) ]
    return merkle_root(leaves)

All helpers use sha3_256, deterministic CBOR, and the same Merkle node function.

⸻

11) Invariants & Edge Cases
	•	EMPTY_MERKLE_ROOT is a fixed function output (H("merkle.empty")) and is used for:
	•	accounts with empty storage, and
	•	the global state when there are no accounts (theoretical).
	•	The tuple (nonce=0, balance=0, codeHash=0^32, storageRoot=EMPTY) implies the account may be pruned.
	•	Sorting is strictly byte-lexicographic; no locale or numeric interpretation.
	•	Any change to hashing, encoding, or domain tags is a consensus change and requires bumping versions and vectors.

⸻

12) Testing Pointers
	•	da/tests/test_blob_commitment.py (style for deterministic hashing)
	•	execution/tests/test_transfer_apply.py (state root determinism after transfers)
	•	execution/tests/test_executor_roundtrip.py (block application → stable root)
	•	core/utils/merkle.py unit tests (pairing & empty cases)

End of spec.
