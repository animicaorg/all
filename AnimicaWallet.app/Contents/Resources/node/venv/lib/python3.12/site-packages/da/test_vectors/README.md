# DA Test Vectors

Canonical, deterministic vectors used to validate **Namespaced Merkle Trees (NMT)**, **Reed–Solomon erasure coding**, and **Data Availability Sampling (DAS)**.  
They are consumed by tests under `da/tests/` and by CLI demos.

- NMT encoding rules come from `da/nmt/codec.py` and `da/schemas/nmt.cddl`.
- Availability proof shape is described in `da/schemas/availability_proof.cddl`.
- Blob/envelope layout is in `da/schemas/blob.cddl`.

> Conventions: all byte strings are **lowercase hex** with `0x` prefix; integers are **decimal**; big-endian where relevant.

---

## Files

- `nmt.json` — Leaf → root/namespace, inclusion & range-proof vectors.
- `erasure.json` — RS(k,n) encode/decode and recovery scenarios.
- `availability.json` — DAS samples against an NMT root (positive/negative).

---

## JSON Shapes

### `nmt.json`
```json
{
  "namespace_bytes": 8,
  "cases": [
    {
      "name": "tiny-three-leaves",
      "leaves": [
        { "ns": "0x0000000000000001", "data": "0x68656c6c6f" },
        { "ns": "0x0000000000000001", "data": "0x776f726c64" },
        { "ns": "0x00000000000000ff", "data": "0x00" }
      ],
      "expect": {
        "min_ns": "0x0000000000000001",
        "max_ns": "0x00000000000000ff",
        "root": "0x<32-bytes>",
        "inclusion_proofs": [
          {
            "leaf_index": 0,
            "ns": "0x0000000000000001",
            "data": "0x68656c6f",              // original data (pre-encoding)
            "proof": {
              "branches": ["0x...", "0x..."], // sibling hashes, left→right
              "positions": ["R","L"],          // branch side hints per level
              "leaf_hash": "0x<32-bytes>"      // for convenience
            },
            "valid": true
          }
        ],
        "range_proofs": [
          {
            "ns_min": "0x0000000000000001",
            "ns_max": "0x0000000000000001",
            "proof": {
              "left_bound": "0x<32-bytes>",
              "right_bound": "0x<32-bytes>",
              "branches": ["0x...", "..."]
            },
            "valid": true
          }
        ]
      }
    }
  ]
}

erasure.json

{
  "profiles": [
    {
      "name": "k16-n32",
      "k": 16,
      "n": 32,
      "share_bytes": 1024,
      "input": {
        "data_len": 12288,
        "sha3_256": "0x<32-bytes>"
      },
      "encode_expect": {
        "shares": ["0x..", "0x.."],          // length = n
        "data_shares": 16,
        "parity_shares": 16
      },
      "recover_cases": [
        {
          "missing_indices": [1,3,5,7,20,22,24,26,28,30,31,0,2,4,6,8], // 16 missing
          "recovered_sha3_256": "0x<32-bytes>"
        }
      ]
    }
  ]
}

availability.json

{
  "namespace_bytes": 8,
  "matrix": {
    "k": 16,
    "n": 32,
    "share_bytes": 1024
  },
  "root": "0x<32-bytes>",
  "samples": [
    {
      "indices": [0, 17, 23, 31],
      "proofs": [
        { "index": 0,  "branches": ["0x..","0x.."], "positions": ["L","R"] },
        { "index": 17, "branches": ["0x..","0x.."], "positions": ["R","L"] },
        { "index": 23, "branches": ["0x..","0x.."], "positions": ["L","L"] },
        { "index": 31, "branches": ["0x..","0x.."], "positions": ["R","R"] }
      ],
      "valid": true
    },
    {
      "indices": [5, 6],
      "proofs": [
        { "index": 5, "branches": ["0x.."], "positions": ["L"] },
        { "index": 6, "branches": ["0x.."], "positions": ["R"] }
      ],
      "valid": false
    }
  ]
}


⸻

Running the tests

# All DA tests
pytest -q da/tests

# Specific suites
pytest -q da/tests/test_nmt_tree.py
pytest -q da/tests/test_erasure_rs.py
pytest -q da/tests/test_availability_proofs.py


⸻

Determinism & Canonicalization
	•	Namespace width is fixed per vector via namespace_bytes; libraries should reject mismatches.
	•	Leaf encoding is canonically namespace || length || data per da/nmt/codec.py.
	•	Hashes are SHA3-256 (protocol default).
	•	Ordering: leaves appear sorted by (namespace, original order) in NMT construction tests unless the case explicitly sets an out-of-order input to exercise reordering.

⸻

Adding new vectors
	1.	Keep sizes small; prefer ≤32 shares and ≤1 KiB share size.
	2.	Provide both positive and negative cases (bad branches, wrong index, truncated proof).
	3.	Include helpful name strings; CI uses them in failure messages.

