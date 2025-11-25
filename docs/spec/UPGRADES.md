# Upgrades — Versioning, Feature Flags, Hard/Soft Fork Gates

This document defines how Animica networks **evolve safely** over time. It covers:

- **Versioning** across protocol, node software, and subsystems.
- **Feature flags** and **activation gates** (height/epoch/timestamp/signal).
- The difference between **hard forks** vs **soft forks** and how we stage them.
- **Fork ID** computation for P2P handshakes and replay protection.
- Rollout, testing, observability, and rollback guidance.

See also:
- [CHAIN_PARAMS.md](./CHAIN_PARAMS.md) — constants & limits  
- [BLOCK_FORMAT.md](./BLOCK_FORMAT.md) — header fields and roots  
- [CAPABILITIES.md](./CAPABILITIES.md) — syscall ABI & gas hooks  
- `consensus/` — Θ/Γ schedule & policy loading  
- `core/genesis/loader.py` — genesis & params hash pinning

---

## 1. Goals & Principles

- **Deterministic:** Activation conditions are unambiguous and computed only from **consensus-visible inputs** (block height, parent timestamps, policy roots).
- **Gradual:** Prefer backwards-compatible (soft) changes; use **two-phase deploys** (acceptance → requirement).
- **Observable:** Every network upgrade has a **name**, **ID**, and **activation boundary**; nodes expose it via RPC and P2P handshake.
- **Rehearsed:** Testnets and devnets carry the same upgrade code paths **before** mainnet.

---

## 2. Taxonomy: What Changes?

| Scope            | Examples                                                                 | Fork Kind     |
|------------------|--------------------------------------------------------------------------|---------------|
| Consensus rules  | PoIES scoring/caps, Θ retarget math, header validity, NMT version        | **Hard/Soft** |
| State execution  | Gas table updates, opcodes, receipts/bloom hashing tweaks                | **Hard**      |
| Data availability| Erasure profile (k,n), DA root rules/NMT version                         | **Hard**      |
| Cryptography     | PQ alg-policy root rotation, zk-verifier enabling/disabling              | **Hard/Soft** |
| RPC/UX           | New JSON-RPC methods, stricter params validation                         | **Soft**      |
| P2P              | Gossip limits, message IDs, flood controls                               | **Soft**      |

**Soft fork:** narrows the set of **valid** blocks/txs (stricter). Legacy nodes might accept _too much_; upgraded nodes reject those, so coordination is still required.

**Hard fork:** changes validity so that **old nodes will reject** canonical blocks after the boundary. Requires synchronized activation.

---

## 3. Versioning Scheme

### 3.1 Protocol Versions
- `protocol.semver`: (major.minor.patch) for **off-chain specification** docs.
- `protocol.network_epoch`: monotonic integer **Fork Number** (`F0 = genesis`). Each **network upgrade** increments this.

### 3.2 Subsystem Versions (examples)
- `consensus.version` (e.g., `poies-2`): scorer/difficulty math set.
- `da.nmt.version` (e.g., `1` → `2`): Namespaced Merkle Tree encoding/version.
- `vm_py.gas.version`: gas schedule table version.
- `zk.registry.version`: verifier registry/vk-cache version.
- `pq.alg_policy.root`: committed hash of algorithm-policy tree.

> Subsystem versions are not independently activated; they become effective **only** under an upgrade entry with a clear gate.

---

## 4. Upgrade Schedule & Fork Gates

Network upgrades are declared in a **schedule** (per network) distributed with node releases (non-consensus file), while the **effects** are consensus-bound (e.g., policies/roots) inside blocks at/after activation.

### 4.1 Canonical Upgrade Record

```jsonc
{
  "id": "aurora",
  "epoch": 3,
  "activation": {
    "kind": "height",
    "value": 1_280_000
  },
  "effects": {
    "consensus": {"poies_version": 2, "theta_ema_v2": true},
    "da": {"nmt_version": 2, "erasure_profile": {"k": 64, "n": 128, "shardSize": 1024}},
    "vm": {"gas_table_version": 3},
    "zk": {"enable": ["groth16.bn254"], "disable": [], "vk_cache_hash": "0x..."},
    "pq": {"alg_policy_root": "0x..."}
  },
  "notes": "Enable PoIES v2 clamps; DA NMT v2; gas table v3; rotate PQ alg policy."
}

Activation kinds:
	•	height: activates at block number H ≥ value.
	•	timestamp: activates when parent median timestamp ≥ value (avoid wall-clock drift).
	•	epoch: uses consensus epochs (e.g., Θ retarget window boundaries).
	•	signal: activates when an on-chain policy root or registry root is updated (proof-of-commit mechanism).
	•	vote (optional/testnets): activates when N of M bootstrap miners signal for X consecutive windows.

Mainnet SHOULD prefer height or signal gates; timestamp gates are only acceptable with median-of-N safeguards.

4.2 Two-Phase Soft-Fork Pattern
	1.	ACCEPT phase: nodes accept both old and new constraints for N blocks; mempool enforces the stricter rule to push network convergence.
	2.	REQUIRE phase: after boundary H*, blocks violating the new rule are invalid.

This reduces accidental splits before H*.

⸻

5. Hard-Fork Mechanics

A hard fork flips validity. We enforce:
	•	The first block at/after the boundary must reflect all root changes (e.g., poiesPolicyRoot, algPolicyRoot, da NMT version transition).
	•	If the fork adjusts header fields (format/version), the block exactly at H* must serialize with the new format.
	•	Reorg across boundary: the rules used depend on the height of the candidate block (rules are pure functions of height/parent-state), guaranteeing determinism.

⸻

6. Fork ID (P2P Handshake)

Nodes exchange a Fork ID during HELLO (see p2p/protocol/hello.py) to quickly detect incompatible schedules.

We follow an EIP-2124–like construction:

FORKHASH = CRC32( genesisHash[0:4] || H1 || H2 || ... || Hn )
FORKNEXT = nextScheduledHeightOr0

	•	Hi are strictly increasing activation heights for all past hard forks on this network.
	•	FORKNEXT helps detect future divergence.

Peers with different FORKHASH are connected with reduced trust, and block/header sync is refused past the last common fork boundary.

⸻

7. Replay & Cross-Network Protection
	•	chainId remains the primary replay domain separator for tx signatures.
	•	After a hard fork with signature domain change (rare), SignBytes domain tag must incorporate protocol.network_epoch to ensure non-ambiguity (see ENCODING.md).

⸻

8. Common Upgrade Scenarios

8.1 Retarget/Θ Update
	•	Gate: epoch (retarget boundary).
	•	Effects: consensus.difficulty parameters (EMA length, clamps).
	•	Test vectors: consensus/tests/test_difficulty_retarget.py.

8.2 PoIES Policy Update
	•	Gate: signal via policy root update embedded in block header.
	•	Effects: per-proof caps/Γ/escort changes (see consensus/policy.py).
	•	Soft vs Hard: usually hard (acceptance predicate changes).

8.3 DA NMT Version Bump
	•	Gate: height.
	•	Effects: new NMT node encoding rules; daRoot computation changes.
	•	Hard fork: Yes.

8.4 VM Gas Table Update
	•	Gate: height.
	•	Effects: execution/gas/table.py version; affects OOG boundaries → hard.

8.5 PQ Algorithm-Policy Rotation
	•	Gate: signal (alg-policy Merkle root included/pinned).
	•	Effects: allowed signature/KEM sets; address formats unchanged; hard/soft depending on deprecations.

8.6 ZK Verifier Enablement
	•	Gate: height.
	•	Effects: capabilities/host/zk.py accepts new scheme_id/circuit_id; soft if only adding; hard when deprecating/making proofs mandatory in some flows.

⸻

9. Node Behavior Around Boundaries
	•	Mempool: pre-enforce stricter rules before H* (configurable preEnforceBlocks window) to reduce post-H* churn.
	•	RPC: expose /chain.getUpgrades and /chain.getForkId style methods (see rpc/methods/chain.py).
	•	Metrics: counters for blocks seen before/after fork, rejected-old-rule, rejected-new-rule.

⸻

10. Rollout Process (Ops)
	1.	Draft spec PR with upgrade ID, gate, and effects; land in docs/spec.
	2.	Code behind feature flags; default off on mainnet, on in devnet.
	3.	Testnet rehearsal:
	•	Dry-run shadow-fork (optional).
	•	Publish test vectors and block headers around H*.
	4.	Release candidates:
	•	Include the schedule; show FORKNEXT in logs.
	•	Announce target boundary; freeze date.
	5.	Activation:
	•	Observe mempool behavior, block validity stats.
	•	Roll back only before H*; after H* treat as final unless chain halts.
	6.	Post-activation:
	•	Bump protocol.network_epoch.
	•	Update p2p Fork ID table.

⸻

11. Edge Cases & Reorgs
	•	If a reorg crosses H*, the node recomputes validity under the rules of the candidate chain at each height; do not cache pre/post rules across heights.
	•	Timestamp gates use median-of-parent-window to avoid miner clock skew. Do not use local wall clock.

⸻

12. Reference: Upgrade Schedule File (non-consensus)

Shipped with releases as chain.upgrades.json (not hashed in headers; the effects are):

{
  "network": "animica-mainnet",
  "genesisHash": "0xabc…",
  "upgrades": [
    {
      "id": "aurora",
      "epoch": 3,
      "activation": {"kind": "height", "value": 1280000},
      "effects": {
        "consensus": {"poies_version": 2, "theta_ema_v2": true},
        "vm": {"gas_table_version": 3},
        "da": {"nmt_version": 2},
        "zk": {"vk_cache_hash": "0xfeed…"}
      }
    }
  ]
}

Clients compute FORKHASH from past hard-fork heights inside this file. Divergence indicates mismatched releases.

⸻

13. Testing & Vectors
	•	Unit tests per subsystem:
	•	consensus/tests/test_*
	•	execution/tests/test_intrinsic_gas.py (boundary cases)
	•	da/tests/test_nmt_*
	•	Golden headers: store pre/post-H* headers and roots; validating nodes must reproduce hashes.
	•	Fuzz mempool admission around boundary ±K blocks.

⸻

14. Observability & Tooling
	•	RPC:
	•	chain.getForkId → { forkHash, forkNext }
	•	chain.getUpgrades → array of known upgrades and current phase
	•	Logs:
	•	“Entering ACCEPT/REQUIRE phase for  at height …”
	•	Explorer:
	•	Badge on the first block with new rules; link to release notes.

⸻

15. Security Considerations
	•	Treat timestamp gates with caution; always use median-of-N parent timestamps.
	•	Upgrades that alter cryptographic primitives must pin roots/hashes in the first block of activation.
	•	Avoid simultaneous multiple hard forks at the same height; compose into a single upgrade entry.

⸻

16. Backward/Forward Compatibility Checklist
	•	Headers/CBOR schemas unchanged or versioned (see ENCODING.md).
	•	Receipts/logs/bloom remain decodable by old explorers (or version field gated).
	•	RPC surface keeps backward-compatible defaults; new fields are optional.
	•	P2P message IDs stable; if changed, negotiate via version bit + topic migration.

⸻

17. Example: “Aurora” Hard Fork (End-to-End)
	•	Gate: height = 1_280_000
	•	Effects: PoIES v2, Θ EMA v2, NMT v2, Gas v3, PQ policy root 0x…, zk vk-cache hash 0x…
	•	Pre-enforcement: mempool enforces Gas v3 200 blocks prior.
	•	At block 1_280_000:
	•	Header includes new policyRoots.
	•	DA root uses NMT v2 encoding.
	•	Execution gas metering uses table v3; receipts change gasUsed accordingly.
	•	P2P FORKHASH updated; FORKNEXT = 0 until next scheduled upgrade.

⸻

18. Deactivation / Deprecation
	•	For verifiers and algorithms, use a sunset period:
	•	Phase 1: mark as deprecated; mempool denies new txs requiring deprecated paths.
	•	Phase 2: disable at a hard-fork boundary.
	•	Keep verification code for at least one network epoch to serve historical queries.

⸻

19. Minimal Node Requirements
	•	Nodes must refuse to start on a network if their chain.upgrades.json yields a FORKHASH not compatible with peers past the last common fork.
	•	Nodes must expose current network epoch and fork boundaries in /metrics.

⸻

20. Appendix: Data Structures

type Activation =
  | { kind: "height"; value: number }
  | { kind: "timestamp"; value: number }        // median-of-parents
  | { kind: "epoch"; value: number }
  | { kind: "signal"; rootKind: "poies"|"alg"|"zk"; value: Hex32 };

interface Upgrade {
  id: string;                 // short, kebab-case
  epoch: number;              // network fork number
  activation: Activation;
  effects: {
    consensus?: { poies_version?: number; theta_ema_v2?: boolean };
    da?: { nmt_version?: number; erasure_profile?: {k:number;n:number;shardSize:number} };
    vm?: { gas_table_version?: number };
    zk?: { enable?: string[]; disable?: string[]; vk_cache_hash?: string };
    pq?: { alg_policy_root?: string };
  };
  notes?: string;
}

