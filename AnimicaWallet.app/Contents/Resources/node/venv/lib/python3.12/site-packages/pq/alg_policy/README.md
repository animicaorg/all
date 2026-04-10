# Animica PQ Algorithm Policy — Merkle Root (v1)

This document describes how the **algorithm policy** (“alg-policy”) is modeled, hashed, and committed to a single **Merkle root** that the chain uses as its canonical reference for **post-quantum signatures** and **KEMs**.

The Merkle root binds:
- which algorithms are allowed (e.g., **Dilithium3**, **SPHINCS+ SHAKE-128s**, **Kyber-768 (KEM)**),
- their identifiers and sizes,
- enable/disable windows and deprecation rules,
- optional per-alg weights/thresholds for multi-sig/dual-sig policies.

This root is referenced in:
- `spec/pq_policy.yaml` (human policy),
- `spec/alg_policy.schema.json` (machine schema),
- block headers (policy root commitment),
- node configuration and address rules.

The reference builder CLI lives at:  
`pq/cli/pq_alg_policy_root.py`

---

## 1) Policy object (high-level)

A policy JSON document (validated by `spec/alg_policy.schema.json`) contains:

```jsonc
{
  "version": 1,
  "entries": [
    {
      "id": "dilithium3",
      "name": "CRYSTALS-Dilithium Level 3",
      "kind": "sig",                     // "sig" or "kem"
      "enabled": true,                   // allowed for new signatures/handshakes
      "sunsetAfter": null,               // optional height/epoch cutover
      "keySizes": { "pk": 1952, "sk": 4000 },
      "sigSizes": { "sig": 3293 },       // for KEMs, use ct/ss instead
      "kemSizes": null,
      "weight": 1.0,                     // optional weighting in multi-alg policies
      "minVersion": "1.0.0",             // minimum node/runtime version expected
      "notes": "Primary PQ signature, fast verification"
    },
    {
      "id": "sphincs-shake-128s",
      "name": "SPHINCS+ SHAKE-128s",
      "kind": "sig",
      "enabled": true,
      "sunsetAfter": null,
      "keySizes": { "pk": 32, "sk": 64 },
      "sigSizes": { "sig": 7856 },
      "kemSizes": null,
      "weight": 0.2,
      "minVersion": "1.0.0",
      "notes": "Stateless hash-based fallback (slow, conservative)"
    },
    {
      "id": "kyber768",
      "name": "Kyber-768",
      "kind": "kem",
      "enabled": true,
      "sunsetAfter": null,
      "keySizes": { "pk": 1184, "sk": 2400 },
      "sigSizes": null,
      "kemSizes": { "ct": 1088, "ss": 32 },
      "weight": 1.0,
      "minVersion": "1.0.0",
      "notes": "KEM for P2P handshakes"
    }
  ],
  "thresholds": {
    "sig": { "minAlgs": 1, "minWeight": 1.0 },   // dual-sign or weighted policies (optional)
    "kem": { "minAlgs": 1, "minWeight": 1.0 }
  }
}

Notes
	•	enabled=false allows historical verification but forbids new signatures/handshakes.
	•	sunsetAfter can be a block height/epoch at which enabled flips logically for new material.
	•	thresholds are advisory; on-chain enforcement is determined by the consensus/runtime using the committed root.

⸻

2) Canonicalization & hashing (v1)

To produce a unique Merkle root, we deterministically encode each entry and then hash:
	•	Canonical JSON: UTF-8, sorted keys, separators=(",", ":"), ensure_ascii=false, no NaN/Inf.
(See pq/cli/pq_alg_policy_root.py → _canon_dumps.)
	•	Domain separation constants (hard-coded v1):
	•	DOM_LEAF  = "animica|alg-policy|leaf|v1|"
	•	DOM_NODE  = "animica|alg-policy|node|v1|"
	•	DOM_EMPTY = "animica|alg-policy|empty|v1|"
	•	Hash function: SHA3-512.
	•	Leaf hash:

leaf = SHA3-512( DOM_LEAF || canonical_json(entry) )


	•	Inner node hash:

node = SHA3-512( DOM_NODE || left || right )


	•	Empty root (no entries):

empty = SHA3-512( DOM_EMPTY )



⸻

3) Sorting & tree shape
	1.	Sort entries by (entry["id"], entry["name"]) ascending.
	2.	Build the leaf array in that order.
	3.	Construct a binary Merkle tree pairing adjacent nodes.
	4.	If a level has an odd number of nodes, duplicate the last node (“BLAKE-style padding”).
	5.	Continue until a single root remains.

ASCII sketch:

level 0 (leaves):  L0  L1  L2  L3  L4
pairing         -> N01 N23 N44
level 1         ->    M0  M1  M2
pairing         ->       R01  R22
level 2 (root)  ->           ROOT


⸻

4) CLI usage

Build a root from a policy JSON:

python -m pq.cli.pq_alg_policy_root \
  --in pq/alg_policy/example_policy.json

Structured JSON output:

python -m pq.cli.pq_alg_policy_root \
  --in pq/alg_policy/example_policy.json --json

Merkle proof for a given id or name:

python -m pq.cli.pq_alg_policy_root \
  --in pq/alg_policy/example_policy.json \
  --proof dilithium3 --json

Debug the tree:

python -m pq.cli.pq_alg_policy_root \
  --in pq/alg_policy/example_policy.json --print-tree

Optional schema validation:

python -m pq.cli.pq_alg_policy_root \
  --in policy.json \
  --schema spec/alg_policy.schema.json


⸻

5) Verifying a Merkle proof (pseudo-code)

Given: leaf_entry, proof_path = [(sibling_hex, side), ...], and root_hex.

from pq.py.utils.hash import sha3_512
DOM_LEAF  = b"animica|alg-policy|leaf|v1|"
DOM_NODE  = b"animica|alg-policy|node|v1|"

h = sha3_512(DOM_LEAF + canonical_json(leaf_entry))

for (sib_hex, side) in proof_path:
    sib = bytes.fromhex(sib_hex)
    if side == "R":
        h = sha3_512(DOM_NODE + h + sib)
    else:  # side == "L"
        h = sha3_512(DOM_NODE + sib + h)

ok = (h.hex() == root_hex)

If the tree had an odd node at a level and the target leaf was that last node, the proof contains its own hash as the sibling for that level; verification remains deterministic.

⸻

6) Versioning & compatibility
	•	This is v1 of the alg-policy Merkle construction. If the encoding, domains, or tree rules change, we will bump to v2 with different domain strings.
	•	Nodes must not accept a policy root computed under a different version.
	•	The current root is committed in:
	•	spec/pq_policy.yaml (human-readable)
	•	spec/params.yaml (chain params that the node uses)
	•	Block headers (consumed by light clients).

⸻

7) Governance & rotation
	•	Adding/removing algorithms or toggling enabled requires producing a new root and updating chain params (governance flow defined in governance/).
	•	sunsetAfter lets a network pre-announce deprecation before flipping enabled.
	•	Address rules may depend on id (e.g., PQ address families); keep id stable to avoid migration churn.

⸻

8) Security considerations
	•	Canonical JSON is essential. Whitespace or key-order differences must not change hashes; use the provided CLI or an exact re-implementation.
	•	Duplicate IDs are rejected by schema/CLI; IDs must be unique and stable over time.
	•	Domain separation prevents cross-protocol hash reuse.
	•	SHA3-512 chosen for conservative capacity; if policy size grows large, tree reduces hashing to O(n).
	•	The root commits only to the allowlist & metadata. Actual cryptographic security depends on algorithm implementations (e.g., liboqs) and parameter choices.

⸻

9) Minimal example policy

{
  "version": 1,
  "entries": [
    { "id": "dilithium3", "name": "CRYSTALS-Dilithium Level 3",
      "kind": "sig", "enabled": true,
      "keySizes": {"pk": 1952, "sk": 4000}, "sigSizes": {"sig": 3293},
      "kemSizes": null, "weight": 1.0, "minVersion": "1.0.0", "notes": "" },
    { "id": "sphincs-shake-128s", "name": "SPHINCS+ SHAKE-128s",
      "kind": "sig", "enabled": true,
      "keySizes": {"pk": 32, "sk": 64}, "sigSizes": {"sig": 7856},
      "kemSizes": null, "weight": 0.2, "minVersion": "1.0.0", "notes": "" },
    { "id": "kyber768", "name": "Kyber-768",
      "kind": "kem", "enabled": true,
      "keySizes": {"pk": 1184, "sk": 2400}, "sigSizes": null,
      "kemSizes": {"ct": 1088, "ss": 32},
      "weight": 1.0, "minVersion": "1.0.0", "notes": "" }
  ],
  "thresholds": { "sig": {"minAlgs": 1, "minWeight": 1.0}, "kem": {"minAlgs": 1, "minWeight": 1.0} }
}

Build root:

python -m pq.cli.pq_alg_policy_root --in pq/alg_policy/example_policy.json


⸻

10) Tests & vectors
	•	Module vectors: pq/test_vectors/*.json
	•	CLI proof & root determinism are covered in:
	•	pq/tests/test_alg_policy_root.py (see repository tests)
	•	Cross-module validation: the committed root should match spec/pq_policy.yaml and spec/params.yaml.

⸻

11) Where the root is used
	•	P2P: peers advertise their supported alg set; the handshake binds to the policy root to prevent downgrade.
	•	RPC: nodes expose the current alg-policy root and version for wallet/SDK verification.
	•	Consensus: headers include the root; reorgs do not affect the value until governance updates it.

⸻

Keep this README in sync with:
	•	spec/alg_policy.schema.json
	•	pq/cli/pq_alg_policy_root.py
	•	spec/pq_policy.yaml
	•	spec/params.yaml

