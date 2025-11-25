# Genesis — State Layout, Premine, Treasury, Initial Validators

This document defines how a **canonical genesis** for the Animica chain is described, validated, and reproduced. It specifies:

- The **on-disk genesis.json** shape.
- The **state layout** at height `0`.
- **Premine** allocations and accounting invariants.
- **Treasury** and system accounts.
- The **initial validators/miners** bootstrap policy (testnets).
- How to derive the **genesis header** and roots deterministically.

> See also:
> - [CHAIN_PARAMS.md](./CHAIN_PARAMS.md) — IDs, constants, limits  
> - [OBJECTS.md](./OBJECTS.md) — Tx/Block/Receipt object model  
> - [ENCODING.md](./ENCODING.md) — domain separation, canonical JSON/CBOR  
> - [BLOCK_FORMAT.md](./BLOCK_FORMAT.md) — header fields & roots  
> - `core/genesis/loader.py` — reference loader & root computation  
> - `core/chain/state_root.py` — canonical state-root calculator

---

## 1. Goals & Principles

- **Deterministic and reproducible**: a given `genesis.json` yields the **same** state root and genesis header across platforms.
- **Minimal**: no contracts or code execution at height `0` (unless explicitly predeployed).
- **Auditable supply**: premine allocations sum to the configured `totalSupply`.
- **Pinned policies**: PoIES policy root and PQ algorithm-policy root are **committed** in the genesis header.
- **Upgrade-safe**: optional bootstrap constraints (e.g., initial validator/miner allowlists) are bounded by **epoch/time windows** and self-disable afterward.

---

## 2. Canonical `genesis.json` Schema

Canonical JSON with sorted keys and normalized numbers (see [ENCODING.md](./ENCODING.md)). High-level shape:

```jsonc
{
  "chainId": 1,
  "network": "animica-mainnet",
  "timestamp": 1735689600,            // UNIX seconds; fixed and documented
  "paramsHash": "0x...",               // SHA3-256 of spec/params.yaml (canonicalized)
  "poiesPolicyRoot": "0x...",          // hash of spec/poies_policy.yaml
  "algPolicyRoot": "0x...",            // hash of spec/alg_policy.schema.json / policy tree
  "da": {
    "erasureProfile": {"k": 64, "n": 128, "shardSize": 1024},
    "nmtVersion": 1
  },
  "economics": {
    "totalSupply": "1000000000000000000000000000",  // bigint string (wei-like units)
    "treasury": {
      "address": "anim1qq...treasury",
      "initialBalance": "400000000000000000000000000"
    },
    "aicfFund": {
      "address": "anim1qq...aicf",
      "initialBalance": "100000000000000000000000000"
    }
  },
  "premine": [
    {"address": "anim1qq...team1", "amount": "150000000000000000000000000", "vesting": {"cliff": 0, "period": 2592000, "steps": 36}},
    {"address": "anim1qq...ecosys", "amount": "200000000000000000000000000"},
    {"address": "anim1qq...testers", "amount": "50000000000000000000000000"}
  ],
  "system": {
    "coinbase": "anim1qq...foundation_miner",
    "reservedNamespaces": [
      {"ns": "0x00000018", "label": "system"},
      {"ns": "0x00000024", "label": "contracts"}
    ],
    "predeploys": [
      // optional; see §4.3
    ]
  },
  "bootstrap": {
    "initialValidators": [             // testnet or gated mainnet warm-up
      {
        "nodeId": "peerid-hex-or-b58",
        "address": "anim1qq...val1",
        "weight": 1,
        "untilHeight": 10000
      }
    ],
    "seeds": [
      "/dns4/seed1.animica.org/tcp/30333/p2p/peerid...",
      "/dns4/seed2.animica.org/tcp/30333/p2p/peerid..."
    ]
  },
  "notes": "Provenance: tag v1.0.0, params.yaml @ sha256:..., poies_policy.yaml @ sha256:..."
}

Validation: the loader re-checks paramsHash, poiesPolicyRoot, algPolicyRoot against local copies before accepting genesis.

⸻

3. State Layout at Height 0

State is modeled as sorted key–value pairs persisted in the KV store, then reduced via the canonical Merkle (see core/utils/merkle.py):

3.1 Logical Buckets
	•	state/accounts/<address> → Account {nonce, balance, code_hash}
	•	state/storage/<address>/<key> → Bytes (only if predeploys)
	•	state/system/treasury → {address}
	•	state/system/aicf → {address}
	•	state/system/paramsHash → Hash32
	•	state/system/poiesPolicyRoot → Hash32
	•	state/system/algPolicyRoot → Hash32

All bucket keys are byte-prefixed with deterministic prefixes; the state-root calculator consumes a flattened, sorted view.

3.2 Empty Roots
	•	txsRoot, receiptsRoot, proofsRoot = empty Merkle roots as defined in BLOCK_FORMAT.md.
	•	daRoot = NMT root of no blobs, i.e., the canonical empty DA root for the configured NMT version.

3.3 Nonce & Balance Semantics
	•	All premine recipients start with nonce = 0.
	•	Balances are bigint strings in JSON; converted to integers exactly (no floats).

3.4 Optional Predeploys

Predeploys are encoded as:

{
  "address": "anim1qq...counter",
  "codeHash": "0x...",
  "storage": [
    ["0x00", "0x..."],
    ["0x01", "0x..."]
  ]
}

The loader writes code_hash and storage keys; no constructor execution occurs.

⸻

4. Premine & Accounting Invariants

Let:
	•	S = economics.totalSupply
	•	T = economics.treasury.initialBalance
	•	F = economics.aicfFund.initialBalance
	•	P = sum(premine[i].amount)

Invariant:

T + F + P == S

The loader rejects genesis if the equality does not hold.

4.1 Recommended Budgeting (illustrative)

Bucket	Share	Rationale
Treasury	40%	Grants, security, long-term ops
Ecosystem (premine)	20%	Incentives, liquidity
Team & Contributors	15%	Multi-year vesting
AICF Fund	10%	AI/Quantum compute incentives
Testers/Airdrops	5%	Early users
Market Making/Reserves	10%	Exchange/liquidity provisioning

Adjust for your network; pin the final values in genesis.json.

4.2 Vesting Metadata

Vesting is advisory at genesis (off-chain or via future contracts). If on-chain enforcement is desired later, predeploy a vesting contract and route allocations through it.

⸻

5. Treasury & System Accounts
	•	Treasury: receives T at height 0 and is referenced by execution/treasury hooks for fee splits (see execution/runtime/fees.py).
	•	AICF Fund: credited F and consumed by aicf/ settlement rules (transfers triggered during settlement epochs).
	•	Coinbase: header field takes effect from block 1; at genesis it is only recorded as metadata.

All system addresses must be valid bech32m Animica addresses (see ADDRESSES.md).

⸻

6. Initial Validators / Miners (Bootstrap Policy)

Animica uses PoIES; validators here refer to bootstrap block producers/miners for controlled bring-up on testnets or warm-up phases.
	•	bootstrap.initialValidators[] is an allowlist enforced by consensus validation for headers up to untilHeight.
	•	Each entry provides a nodeId (P2P peer-id), optional address, and an integer weight (for tie-breaking/backoffs).
	•	After max(untilHeight) the allowlist is ignored, and the network is fully permissionless.

If not set (mainnet), the allowlist check is disabled from genesis.

⸻

7. Genesis Header Derivation

Given the populated KV state, compute:
	1.	stateRoot — via canonical Merkle of flattened state/*.
	2.	txsRoot = EMPTY_TXS_ROOT
	3.	receiptsRoot = EMPTY_RECEIPTS_ROOT
	4.	proofsRoot = EMPTY_PROOFS_ROOT
	5.	daRoot = EMPTY_DA_ROOT (per NMT version)
	6.	theta — initial Θ from params (difficulty/threshold baseline)
	7.	mixSeed — domain-separated seed (e.g., H("mix|genesis"|chainId))

Header (height = 0):

Header {
  parentHash: 0x000…000,
  number: 0,
  stateRoot, txsRoot, receiptsRoot, proofsRoot, daRoot,
  beaconRoot: 0x000…000 (until beacon enabled),
  theta: Θ0,
  policyRoots: { poies: <poiesPolicyRoot>, algPolicy: <algPolicyRoot> },
  mixSeed: H(domain | chainId)
}

The block hash is computed over the canonical header encoding (see ENCODING.md).

⸻

8. Reproducibility Checklist
	•	Pin params.yaml and policy files to content hashes in genesis.json.
	•	Run core/genesis/loader.py in deterministic mode (env: LC_ALL=C, Python version recorded).
	•	Emit a machine-readable attestation:

sha256(genesis.json) = …
stateRoot           = …
headerHash          = …


	•	Store the attestation alongside the release tag.

⸻

9. Example (Devnet Minimal)

{
  "chainId": 1337,
  "network": "animica-devnet",
  "timestamp": 1735689600,
  "paramsHash": "0x2be8…",
  "poiesPolicyRoot": "0x9af1…",
  "algPolicyRoot": "0xa3d4…",
  "da": {"erasureProfile": {"k": 8, "n": 16, "shardSize": 512}, "nmtVersion": 1},
  "economics": {
    "totalSupply": "1000000000000000000000000",
    "treasury": {"address": "anim1qq...tr", "initialBalance": "400000000000000000000000"},
    "aicfFund": {"address": "anim1qq...ai", "initialBalance": "100000000000000000000000"}
  },
  "premine": [
    {"address": "anim1qq...u1", "amount": "300000000000000000000000"},
    {"address": "anim1qq...u2", "amount": "200000000000000000000000"}
  ],
  "system": {
    "coinbase": "anim1qq...cb",
    "reservedNamespaces": [{"ns": "0x00000018", "label": "system"}],
    "predeploys": []
  },
  "bootstrap": {
    "initialValidators": [
      {"nodeId": "12D3KooW…", "address": "anim1qq...val", "weight": 1, "untilHeight": 1000}
    ],
    "seeds": ["/dns4/seed.dev.animica.local/tcp/30333/p2p/12D3KooW…"]
  },
  "notes": "Devnet example; do not use for mainnet."
}


⸻

10. Loader Acceptance Rules (Summary)

A genesis file is accepted iff:
	1.	Schema is valid and keys are canonicalized.
	2.	paramsHash/policy roots match local files.
	3.	Premine sum satisfies T + F + P == S.
	4.	All addresses are valid (bech32m, supported PQ alg flavor).
	5.	Optional bootstrap window is bounded (untilHeight > 0 and below configured cap).
	6.	The derived header fields match recomputation.

⸻

11. Security Considerations
	•	Never ship a genesis that depends on mutable external URLs. Pin exact content hashes.
	•	Keep treasury/aicf keys offline; withdrawals should require multi-sig policies (contract-level).
	•	If an allowlist is used at genesis, ensure a hard cutoff (untilHeight) to avoid indefinite centralization.
	•	Audit supply math and publish independent recomputation scripts.

⸻

12. Operational Notes
	•	Distribute genesis.json with the release tarball and attach an attestation with hashes.
	•	Nodes must be started with the exact genesis.json path or a known SHA sum.
	•	Explorer/Studio instances should display the pinned paramsHash/policy root upon connection.

