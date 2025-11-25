<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Change Index · Breaking Changes by Version

A single place to **track and cross-reference breaking changes** across the stack (specs, node, RPC, VM, DA, P2P, SDKs, wallets).  
Pairs with the human-readable changelog in `docs/CHANGELOG.md` and the module-local CHANGELOGs.

> **Scope.** “Breaking” means existing integrations may fail to compile, verify, or interoperate without action.  
> Examples: on-chain format changes, RPC/ABI contract, consensus math, cryptography policy, security-sensitive defaults.

---

## How to use this index

- Start here during **upgrades** and when **pinning versions** for reproducible builds.
- Each row links to:
  - the **spec** section defining the new behavior,
  - **migration notes** with concrete steps,
  - **tests** proving the new invariants.

For a narrative of all changes (including non-breaking), see `docs/CHANGELOG.md`.

---

## SemVer & channels

- **SemVer:** `MAJOR.MINOR.PATCH`
  - **MAJOR**: may include *breaking* protocol or public API changes.
  - **MINOR**: backward-compatible features; may add optional fields (gated).
  - **PATCH**: bug fixes & clarifications; no behavior change by default.
- **Channels:** stable / beta / nightly. Breaking changes only land in **beta** first with a deprecation window unless security-critical.

---

## Quick matrix

| Version | Area | What changed (one-liner) | Impact | Spec / Docs | Migration / Tests |
|---|---|---|---|---|---|
| **0.9.0** | **RPC / tx** | `tx.sendRawTransaction` now requires **CBOR canonical** envelope (no hex JSON wrapper) | Clients that posted hex-encoded payloads will fail | [`docs/spec/TX_FORMAT.md`](../spec/TX_FORMAT.md), [`docs/rpc/JSONRPC.md`](../rpc/JSONRPC.md) | Update SDKs to `encode_cbor()`; see `rpc/tests/test_tx_flow.py` |
| 0.9.0 | **Mempool** | Replacement policy: min effective-fee delta raised from 10% → **15%** | Some RBF replacements rejected | [`docs/spec/MEMPOOL.md`](../spec/MEMPOOL.md) | Tune wallet fee bump; see `mempool/tests/test_replacement.py` |
| **0.8.0** | **Tx / Access List** | Access list item encoding changed to `(address, [storageKeys...])` (order is canonical) | Non-canonical encoders produce different hashes | [`docs/spec/TX_FORMAT.md`](../spec/TX_FORMAT.md) | Use canonical sort; `execution/tests/test_access_list_build.py` |
| 0.8.0 | **VM Gas** | Refund cap **80% → 20%** and cold-access charge adjusted | Gas estimates must be recalibrated | [`docs/vm/GAS_MODEL.md`](../vm/GAS_MODEL.md) | Re-estimate via studio-wasm; `execution/tests/test_receipts_hash.py` |
| **0.7.0** | **Consensus** | Θ retarget EMA window widened; clamp bands tightened | Block interval variance changes; mining settings | [`docs/spec/DIFFICULTY_RETARGET.md`](../spec/DIFFICULTY_RETARGET.md) | Verify with `consensus/tests/test_difficulty_retarget.py` |
| 0.7.0 | **Proof Envelope** | `proofsRoot` calculation now includes **receipt-leaf domain** tag | Old blocks fail validation | [`docs/spec/proofs/ENVELOPE.md`](../spec/proofs/ENVELOPE.md) | Regenerate vectors; `proofs/tests/test_cbor_roundtrip.py` |
| 0.7.0 | **DA / Header** | `da_root` moved from tx-section to header root set; NMT version = `1` | Light clients must follow new root | [`docs/spec/MERKLE_NMT.md`](../spec/MERKLE_NMT.md), [`docs/spec/BLOCK_FORMAT.md`](../spec/BLOCK_FORMAT.md) | `da/tests/test_light_client_verify.py` |
| **0.6.0** | **P2P Handshake** | Transcript binds **alg-policy root**; peers without it are rejected | Old nodes can’t connect | [`docs/pq/HANDSHAKE.md`](../pq/HANDSHAKE.md), [`docs/spec/P2P.md`](../spec/P2P.md) | Rotate seeds; `p2p/tests/test_handshake.py` |
| 0.6.0 | **PQC Policy** | Dilithium3 default → **SPHINCS+** allowed; address HRP unchanged | Wallets may need fallback signer | [`docs/pq/POLICY.md`](../pq/POLICY.md), [`docs/spec/ADDRESSES.md`](../spec/ADDRESSES.md) | Check `pq/tests/test_registry.py` |

> **Legend:** **bold** versions indicate *first* release where behavior is *required on-network*.  
> Earlier minor/patch releases in beta may gate the behavior behind feature flags.

---

## Detailed cross-references

### Transaction & Block Encodings
- Spec: [`docs/spec/TX_FORMAT.md`](../spec/TX_FORMAT.md), [`docs/spec/BLOCK_FORMAT.md`](../spec/BLOCK_FORMAT.md), [`docs/spec/ENCODING.md`](../spec/ENCODING.md)  
- Modules/Tests: `core/types/tx.py`, `core/encoding/cbor.py`, `execution/tests/*`, `rpc/tests/test_tx_flow.py`  
- Migration playbook:
  1. Pin SDK versions that include the new encoder.
  2. Re-encode golden vectors (`spec/test_vectors/txs.json`).
  3. Rebuild any fixtures in explorers/wallets.

### Consensus / PoIES / Retarget
- Spec: [`docs/spec/poies/RETARGET.md`](../spec/poies/RETARGET.md), [`docs/spec/poies/ACCEPTANCE_S.md`](../spec/poies/ACCEPTANCE_S.md)  
- Modules/Tests: `consensus/difficulty.py`, `consensus/tests/test_difficulty_retarget.py`  
- Migration: update miner templates and hashrate targets; confirm acceptance thresholds.

### Proofs / Envelope / DA
- Spec: [`docs/spec/proofs/ENVELOPE.md`](../spec/proofs/ENVELOPE.md), [`docs/spec/MERKLE_NMT.md`](../spec/MERKLE_NMT.md), [`docs/spec/DA_ERASURE.md`](../spec/DA_ERASURE.md)  
- Modules/Tests: `proofs/*`, `da/*` test vectors  
- Migration: recompute `proofsRoot`/`da_root`; refresh light client code.

### RPC / WebSockets
- Spec: [`docs/rpc/JSONRPC.md`](../rpc/JSONRPC.md), [`docs/rpc/WEBSOCKETS.md`](../rpc/WEBSOCKETS.md)  
- Modules/Tests: `rpc/methods/*`, `rpc/tests/*`, SDK tests  
- Migration: bump SDKs; verify parameter names and envelope types; adjust rate limits if changed.

### VM / Gas / ABI
- Spec: [`docs/vm/GAS_MODEL.md`](../vm/GAS_MODEL.md), [`docs/vm/ABI.md`](../vm/ABI.md)  
- Modules/Tests: `vm_py/*`, `execution/gas/*`  
- Migration: re-estimate gas; re-generate ABI client stubs; run studio-wasm sims.

### PQ Policy / Wallets
- Spec: [`docs/pq/POLICY.md`](../pq/POLICY.md), [`docs/spec/ADDRESSES.md`](../spec/ADDRESSES.md)  
- Modules/Tests: `pq/tests/*`, wallet unit tests  
- Migration: ensure fallback signers; rotate keys if required by policy.

---

## Deprecation windows & feature flags

- Every breaking change must include:
  - **Feature flag** (default off) in at least one **beta** release.
  - **Telemetry** or test gating to detect old clients during the window.
  - **Removal date** targeting the next **major** (or documented minor if testnet-only).

Track flags under: `core/config.py`, `rpc/config.py`, `vm_py/config.py`, and document in `docs/CHANGELOG.md`.

---

## Authoring a new entry

Add a row to the matrix and a per-area note:

```markdown
| 1.0.0 | Area | Brief change | Impact | [Spec link](../spec/FOO.md) | Migration steps |

Checklist:
	•	Update docs/CHANGELOG.md
	•	Link PRs / commits
	•	Add/adjust test vectors
	•	Announce in release notes & ops runbooks

⸻

See also
	•	docs/CHANGELOG.md — full narrative and minor changes
	•	docs/dev/RELEASES.md — release process and signing
	•	Module-local CHANGELOGs (where present)

⸻

Last updated: YYYY-MM-DD
