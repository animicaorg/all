# PQ Policy — Rotation Cadence & Emergency Levers

_This document defines how Animica rotates **post-quantum (PQ)** algorithms and what emergency levers exist to quickly deprecate or quarantine an algorithm in case of cryptanalytic breaks or implementation flaws._

Related specs:
- `spec/pq_policy.yaml` — canonical on-chain policy knobs (enabled algs, thresholds)
- `spec/alg_policy.schema.json` — schema for the Merkle-tree **alg-policy** object hashed into a root
- `pq/alg_ids.yaml` — canonical IDs and names
- `pq/py/*` — reference implementations and guards

> **Non-governable via params:** Adding a _new_ algorithm family or wire format that requires code changes. That is a **protocol upgrade** (see `docs/spec/UPGRADES.md`).  
> **Governable via params:** Enabling/disabling existing, already-implemented algorithms; raising/lowering thresholds; updating the _alg-policy root_ (pinning versions and deprecations).

---

## 1) Scope & Goals

- Maintain a robust PQ posture for:
  - **Signatures (addresses & transactions):** Dilithium3 and SPHINCS+ (SHAKE-128s).
  - **KEM (P2P handshake):** Kyber-768.
- Provide **predictable rotations** to refresh keys/firmware and phase out weak variants.
- Offer **rapid kill-switches** to disable a compromised algorithm without halting the chain.
- Preserve **backwards compatibility** windows so users can migrate safely.

Security target: NIST Level 2–3 equivalent for end-user keys; handshake confidentiality at Level 3.

---

## 2) Baseline Algorithm Set

| Purpose | Algorithms | IDs (example) | Default Status |
|---|---|---|---|
| Signatures | Dilithium3, SPHINCS+ (SHAKE-128s) | `dilithium3`, `sphincs_shake_128s` | **Enabled** |
| KEM (P2P) | Kyber-768 | `kyber768` | **Enabled** |

Wallets MAY offer both signature families; addresses encode `alg_id || sha3_256(pubkey)` (see `docs/spec/ADDRESSES.md`). Nodes MUST verify per `spec/pq_policy.yaml`.

---

## 3) Rotation Cadence (Normal)

**Cadence:** Semi-annual (every ~6 months), aligned with minor releases.

Each rotation includes:
1. **Policy draft** PR updating `spec/pq_policy.yaml` and the **alg-policy Merkle tree** entries (weights, status, deprecations) → new **`alg_policy_root`**.
2. **Staging window** _T+0 to T+30 days_: Nodes accept both old and new policy roots (dual-pin) if supported. Wallets prompt users to rotate where necessary.
3. **Activation** _T+30 days_: Governance enacts the bundle (timelocked ≥48h). Old root becomes **legacy-accepted** for a grace period (see §5).
4. **Grace sunset** _T+90–120 days_: Legacy signatures/handshakes still verify but are flagged **deprecated** in RPC. After the sunset, policy may move them to **disabled**.

**Key hygiene expectations**
- Validators and service operators rotate long-lived keys at least every 12 months.
- Firmware/SDK pinning to audited PQ libraries; reproduce hash-locked builds (see `docs/security/SUPPLY_CHAIN.md`).

---

## 4) Thresholds & Policy Knobs

In `spec/pq_policy.yaml` (illustrative):

```yaml
signatures:
  enabled: [dilithium3, sphincs_shake_128s]
  deprecated: []
  disabled: []
  address_rules:
    # e.g., allow both; future: require->multi (out of scope here)
    allowed_alg_ids: [dilithium3, sphincs_shake_128s]
  min_versions:
    dilithium3: ">=3.1.0"        # library/impl semantic guard
    sphincs_shake_128s: ">=1.0"  # maps via alg-policy tree

kem:
  enabled: [kyber768]
  deprecated: []
  disabled: []
  min_versions:
    kyber768: ">=3.0.0"

policy_root:
  alg_policy_root: "0x<sha3-512-merkle-root>"
  activated_from_height: <H>

	•	enabled/deprecated/disabled sets drive mempool admission and P2P acceptance.
	•	min_versions gate known-bad impls (maps to the Merkle tree entries).
	•	alg_policy_root pins the set to a hash committed by governance.

⸻

5) Deprecation Windows
	•	Deprecated → Disabled (signatures): ≥90 days after activation. Transactions signed with deprecated algs still verify during this window; RPC and wallets should warn loudly.
	•	Deprecated → Disabled (KEM): ≥30 days is acceptable because KEM only affects new sessions. Peers should support multiple KEMs during this period.

Hard minimums: 30 days (KEM), 90 days (signatures), unless an emergency is declared (§6).

⸻

6) Emergency Levers

Triggered by credible cryptanalytic breaks, catastrophic implementation bugs, side-channel disclosures, or supply-chain compromise.

Levers (in priority order):
	1.	Quarantine (soft-kill):
	•	Move algorithm to deprecated immediately and set mempool admission off by default for new transactions unless a feature flag is set (--pq-allow-deprecated for manual override during migration).
	•	P2P: stop advertising the compromised KEM; continue accepting inbound from healthy peers.
	2.	Disable (hard-kill):
	•	Move algorithm to disabled. Nodes reject new transactions/handshakes using it.
	•	Existing chain data remains valid; no retroactive invalidation.
	3.	Pin New Root:
	•	Issue a new alg_policy_root removing compromised versions or tightening min_versions.
	•	Use a fast governance path with reduced timelock (≥6h) reserved for security incidents.
	4.	Network Signaling:
	•	RPC method (e.g., chain.getParams) exposes pqIncident: { alg_id, severity, activated_at }.
	•	Wallets & services show blocking banners; studio/explorer add advisories.
	5.	Transport Fallback:
	•	P2P handshake KEM rotation: disable Kyber variant X → enable Kyber variant Y (if already implemented) or fall back to a pre-negotiated hybrid (PQ + classical) profile if compiled in. (Adding new KEMs is a protocol upgrade.)

Operator Checklist (Emergency):
	•	Rotate validator & service keys not using the compromised alg.
	•	Sweep user funds by re-signing transactions from a healthy algorithm (wallet prompts).
	•	Update to the governance-approved policy package; verify alg_policy_root hash.

⸻

7) Alg-Policy Merkle Tree
	•	Each leaf: { alg_id, family, impl_version, status, notes, deprecation_height? }.
	•	Tree root: sha3-512 over canonicalized leaves; published as alg_policy_root.
	•	Governance signs the root with chain governance keys; nodes verify signatures and hash in CI.
	•	See pq/alg_policy/* and pq/cli/pq_alg_policy_root.py.

⸻

8) Backward Compatibility & UX
	•	Addresses: Algorithm-specific; disabling an algorithm does not break historical lookups. Wallets must not generate new addresses for disabled/depred algs.
	•	RPC/Explorer: Tag accounts and transactions with alg_id and status (ok, deprecated, disabled at time of view).
	•	Mempool: Fast-path precheck consults spec/pq_policy.yaml; reject disabled algs with clear error codes.

⸻

9) Governance Process
	1.	Draft policy PR with:
	•	Updated spec/pq_policy.yaml
	•	Regenerated alg_policy_root (tool output + signed attestation)
	•	Risk assessment (attack surface, migration UX, SDK updates)
	2.	CI gates:
	•	Policy schema validation
	•	Node startup with new policy
	•	Wallet/SDK compatibility tests
	3.	Timelock & Announcement:
	•	Publish height/ETA; broadcast via status page and feeds.
	4.	Post-activation audit:
	•	Monitor admission rates, error logs, handshake success, helpdesk volume.

⸻

10) Rollback Plan

If unintended breakage occurs:
	•	Issue hotfix policy restoring prior root and re-enabling old alg(s) as deprecated for 14 days.
	•	Publish incident report and migration instructions.
	•	Root cause: implementation bug vs. governance package error vs. ecosystem lag.

⸻

11) Appendix — Example Emergency Patch

bundle: pq-emergency-2025-03-17
effective_height: 1234567
signatures:
  enabled: [sphincs_shake_128s]
  deprecated: []
  disabled: [dilithium3]   # temporary disable due to side-channel bulletin
kem:
  enabled: [kyber768]
policy_root:
  alg_policy_root: "0xNEWROOT…"   # excludes affected impl versions
timelock_hours: 6
signatures_attached:
  - governance-key-1: 0x…
  - governance-key-2: 0x…

Nodes display: “PQ Incident: dilithium3 disabled; rotate keys and update wallets.”

⸻

12) References
	•	NIST PQC Round 3 selections and KATs
	•	PQClean, liboqs implementation notes
	•	Chain specs: docs/spec/TX_FORMAT.md, docs/spec/ADDRESSES.md, docs/pq/*

