# Canonical Object Model — Tx / Block / Proof / Receipt

This document defines the **consensus object model** and its **canonical encodings** used across the node (`core/*`), RPC (`rpc/*`), SDKs (`sdk/*`), and tests. The authoritative wire schemas live in `spec/*.cddl` and JSON-Schemas where noted.

**Scope:**
- Transaction (**Tx**): user-signed intent with PQ signature.
- Proof (**ProofEnvelope**): useful-work proofs (HashShare, AI, Quantum, Storage, VDF).
- Receipt (**Receipt**): result of applying a Tx.
- Header / Block (**Header**, **Block**): commitment structure linking roots for state, txs, receipts, proofs, and DA.

> See also:
> - `spec/tx_format.cddl`, `spec/header_format.cddl`, `spec/blob_format.cddl`
> - `core/types/{tx,header,block,receipt,proof}.py`
> - `execution/*`, `consensus/*`, `proofs/*`, `da/*`, `randomness/*`

---

## 0) Encoding, Hashing, Domains

**Canonical CBOR** (a.k.a. "dag-cbor-like", fully deterministic):

- **Maps**: sorted by **short lexicographic** key (UTF-8), no duplicate keys.
- **Integers**: non-negative fit in CBOR major type 0; negative not used in consensus objects.
- **Bytes**: used for 32-byte hashes, commitments, signatures, public keys.
- **Big integers**: where applicable (e.g., `value`), values MUST fit in **u256**; encode as CBOR unsigned integer (no tags) provided it fits (≤ 2^256-1). Canonical JSON uses decimal strings.
- **Booleans**, **arrays**: standard CBOR.
- **No floating point** anywhere.

**Hash function**: unless otherwise specified, **`sha3_256(cbor_bytes)`** yields the canonical object hash (Tx hash, Block hash, etc.). The VM may expose `keccak256` for contract use but consensus roots/hashes use SHA3.

**Signing domains** (see `core/encoding/canonical.py`):
- **Tx SignBytes**: `domain = "animica/tx-v1"`; CBOR structure includes `chainId` and the canonical **unsigned** Tx fields (no `signature` map).
- **Header Nonce/Mix domain**: `domain = "animica/nonce-v1"` for header binding in HashShare draws.

---

## 1) Transaction (Tx)

A **Tx** represents a user action of kind **transfer**, **deploy**, or **call**. The PQ signature proves authorization.

### 1.1 Canonical Fields

| Key            | Type                        | Required | Description |
|----------------|-----------------------------|---------:|-------------|
| `chainId`      | `u32`                       | ✅        | Must match network. Included in SignBytes. |
| `from`         | `Address` (bech32m `anim1…`) | ✅        | Sender address (derivable from `signature.pubkey`, but kept explicit for ergonomics). |
| `nonce`        | `u64`                       | ✅        | Monotonic per sender. |
| `kind`         | `tstr` ∈ {`transfer`,`deploy`,`call`} | ✅ | Dispatch selector. |
| `to`           | `Address` \| `null`         | *kind*   | `transfer`/`call` need `to`; `deploy` uses `null`. |
| `value`        | `u256`                      | ✅        | Amount in aANM for `transfer` and payable calls; 0 otherwise. |
| `gasLimit`     | `u64`                       | ✅        | Upper bound on gas. |
| `gasPrice`     | `u256`                      | ✅        | Price in aANM/gas. Split into base-burn / tip at execution. |
| `accessList`   | `[{ address, storageKeys[] }]` | optional | Deterministic access hints (see `execution/types/access_list.py`). |
| `data`         | `bstr`                      | *kind*   | For `deploy`: contract code+manifest bundle (per `spec/manifest.schema.json`). For `call`: ABI-encoded payload. Empty for `transfer`. |
| `signature`    | `Signature` (see below)     | ✅        | PQ signature (Dilithium3 or SPHINCS+). |

**Signature object**

| Key        | Type    | Description |
|------------|---------|-------------|
| `alg_id`   | `u16`   | From `pq/alg_ids.yaml`. |
| `pubkey`   | `bstr`  | Raw PQ public key bytes (verifier derives `from` and checks match). |
| `sig`      | `bstr`  | Signature over **Tx SignBytes**. |

> **Note:** Nodes must reject if `from` ≠ `bech32m( alg_id || sha3_256(pubkey) )`.

### 1.2 CDDL Sketch (normative in `spec/tx_format.cddl`)
```cddl
Tx = {
  chainId: uint,
  from: bstr, ; bech32m is presentation; CBOR carries raw payload (hrp external)
  nonce: uint,
  kind: tstr, ; "transfer" / "deploy" / "call"
  to: bstr / null,
  value: uint,
  gasLimit: uint,
  gasPrice: uint,
  accessList: ? [* { address: bstr, storageKeys: [* bstr] }],
  data: bstr,
  signature: {
    alg_id: uint,
    pubkey: bstr,
    sig: bstr
  }
}

1.3 Invariants
	•	gasLimit ≥ intrinsic for kind. See execution/gas/intrinsic.py.
	•	value=0 for deploy unless payable system rules say otherwise.
	•	accessList addresses unique; storage keys unique per address.
	•	Sig must verify for SignBytes with chainId included; nonce must be next for from.

⸻

2) ProofEnvelope

Useful-work proofs attach to blocks and contribute to PoIES scoring (ψ). Each proof envelope is type-tagged and has a nullifier to prevent reuse within a TTL window.

2.1 Canonical Fields

Key	Type	Description
type_id	u16	See consensus/types.py / proofs/types.py (HashShare=1, AI=2, Quantum=3, Storage=4, VDF=5).
body	bstr	CBOR or JSON body per proof schema (proofs/schemas/*).
nullifier	bstr	Deterministic `sha3_256(domain
headerBind	bstr	Binding to target header template where applicable (e.g., HashShare mixSeed, prev hash).

Verification maps body → metrics → ψ-inputs with proofs/policy_adapter.py, then consensus/scorer clips by caps and totals (see spec/poies_policy.yaml).

⸻

3) Receipt

Minimal apply result, committed via receiptsRoot in the header.

Key	Type	Description
status	u8 (0/1/2)	SUCCESS / REVERT / OOG (see execution/types/status.py).
gasUsed	u64	Charged gas after refunds.
logs	[LogEvent]	Deterministic event list. Optional bloom is derived.

LogEvent

Key	Type
address	Address
topics	[bstr] (≤ 4)
data	bstr

Receipts are CBOR-encoded (execution/receipts/encoding.py). A logs Merkle/bloom is computed deterministically (see execution/receipts/logs_hash.py).

⸻

4) Header

The header binds consensus state, Merkle roots, DA commitments, and timing/difficulty.

4.1 Canonical Fields

Key	Type	Description
parentHash	bstr[32]	Hash of parent header (sha3_256 over canonical header CBOR, with nonce included).
number	u64	Height.
timestamp	u64	Unix seconds (monotonic, within policy skew).
chainId	u32	Network id.
stateRoot	bstr[32]	Canonical state (Merkle) root.
txsRoot	bstr[32]	Merkle root over ordered Tx array (by block inclusion order).
receiptsRoot	bstr[32]	Merkle root over ordered Receipt array (1-to-1 with Tx order).
proofsRoot	bstr[32]	Merkle root over included ProofEnvelopes (PoIES inputs).
daRoot	bstr[32]	Namespaced Merkle Tree (NMT) root for DA (see da/nmt/commit.py).
theta	u64	Current acceptance threshold (μ-nats micro-threshold packing).
mixSeed	bstr[32]	Domain for HashShare draws (randomness/beacon mixed).
nonce	bstr[8]	HashShare/retarget u-draw material (domain-separated).

Deterministic header hash: sha3_256(cbor(Header)). Some proofs may bind to a header template excluding nonce (documented in proofs/hashshare.py).

⸻

5) Block

A Block is the tuple:
	•	header: Header
	•	txs: [Tx]
	•	proofs: [ProofEnvelope]
	•	receipts: ?[Receipt] (optional in block gossip; nodes may serve receipts on RPC)

5.1 Invariants
	1.	Root consistency
	•	txsRoot = Merkle root of CBOR-encoded txs in exact order.
	•	receiptsRoot = Merkle root of CBOR-encoded receipts, aligned 1:1 with txs.
	•	proofsRoot = Merkle root of CBOR-encoded proofs.
	•	daRoot equals computed NMT root for blobs pinned by the block (if any).
	2.	Lengths
	•	If receipts present: len(receipts) == len(txs).
	3.	Consensus binding
	•	chainId in header equals all tx.chainId.
	•	theta derived by retarget from previous blocks (see consensus/difficulty.py).
	4.	Limits
	•	Encoded block size ≤ limits.max_block_bytes.
	•	Σ gasUsed of receipts ≤ gas.block_gas_limit.

⸻

6) Merkle Layouts (informative)

We use a canonical binary Merkle tree (core/utils/merkle.py) over leaf = sha3_256(cbor(element)) with pairwise concat sha3_256(left||right). For NMT (DA), see da/nmt/* which incorporates namespace ranges; that root is distinct and placed in daRoot.

⸻

7) Canonical JSON Views (RPC)

For external APIs (RPC, SDKs), objects have canonical JSON forms:
	•	Hashes/roots: 0x-prefixed hex (lowercase).
	•	Big integers: decimal strings (to avoid JS rounding).
	•	Addresses: bech32m HRP anim (anim1…).

Examples are in rpc/models.py and rpc/tests/*. Round-trips must preserve CBOR identity for consensus paths.

⸻

8) Object Lifecycles
	•	Tx
	•	Admission: mempool stateless checks (mempool/validate.py) + PQ precheck.
	•	Stateful checks on apply (balance/nonce/gas) in execution/runtime/*.
	•	ProofEnvelope
	•	Verified by proofs/* modules; mapped to ψ inputs and clipped by consensus/caps.py.
	•	Nullifier recorded for TTL in consensus/nullifiers.py.
	•	Receipt
	•	Built from ApplyResult (execution/types/result.py) by execution/receipts/builder.py.
	•	Block
	•	Constructed by miner/packer (mining/header_packer.py), then validated by consensus/validator.py and persisted via core/db/*.

⸻

9) Size & Gas Accounting (summary)
	•	Intrinsic gas: per execution/gas/intrinsic.py by kind and payload size (deploy_per_code_byte).
	•	Block gas limit: gas.block_gas_limit in spec/params.yaml.
	•	Refunds: bounded by gas.refund_max_ratio.
	•	Fees split: execution/runtime/fees.py burns base portion and tips coinbase; policy in spec/params.yaml.

⸻

10) Security & DoS Guards
	•	All arrays length-bounded by limits.* and per-object size caps.
	•	Deterministic map key ordering prevents malleability in CBOR.
	•	PQ verification first on admission to minimize DB hits.
	•	Proof nullifier TTL prevents replay; header/template binds prevent cross-fork reuse.

⸻

11) Examples (abridged)

11.1 Tx (transfer) — JSON view

{
  "chainId": 1,
  "from": "anim1qq…",
  "nonce": 42,
  "kind": "transfer",
  "to": "anim1xx…",
  "value": "1000000000000000000",
  "gasLimit": 50000,
  "gasPrice": "1000",
  "data": "0x",
  "signature": {
    "alg_id": 1,
    "pubkey": "0x…",
    "sig": "0x…"
  }
}

11.2 Header (roots abbreviated)

{
  "parentHash": "0x…",
  "number": 123456,
  "timestamp": 1712345678,
  "chainId": 1,
  "stateRoot": "0x…",
  "txsRoot": "0x…",
  "receiptsRoot": "0x…",
  "proofsRoot": "0x…",
  "daRoot": "0x…",
  "theta": 834215,
  "mixSeed": "0x…",
  "nonce": "0x0000000000000000"
}


⸻

12) Normative References
	•	CDDL: spec/tx_format.cddl, spec/header_format.cddl, spec/blob_format.cddl
	•	JSON-Schema: spec/abi.schema.json, spec/manifest.schema.json
	•	Implementations:
	•	Types: core/types/*.py
	•	Encoding: core/encoding/{cbor,canonical}.py
	•	Merkle: core/utils/merkle.py, da/nmt/*
	•	Receipts: execution/receipts/*
	•	Proofs: proofs/*, consensus/*

