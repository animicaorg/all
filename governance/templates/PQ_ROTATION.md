schema_version: 1
proposal_id: "GOV-YYYY.MM-PQROT-<short-slug>"
kind: "PQRotation"
network: "<mainnet | testnet | devnet>"
title: "Post-Quantum Rotation: <concise title>"
summary: "<1–2 sentence changelog summary>"
description: "<Markdown body; keep concise>"
proposer:
  address: "anim1<bech32m proposer address>"
  display_name: "<Your Name>"
  organization: "<Org or Working Group>"
  contact: "https://forum.example.org/u/<handle>"
created_at: "YYYY-MM-DDTHH:MM:SSZ"
voting_window:
  start_time: "YYYY-MM-DDTHH:MM:SSZ"
  end_time: "YYYY-MM-DDTHH:MM:SSZ"
references: []
labels: ["pq", "security"]
payload:
  policy_version: "1.4.0"
  target_sets:
    signatures: ["dilithium3", "sphincs_shake_128s"]
    handshake_kem: ["mlkem768"]
    hash: "sha3-256"
    address_scheme: "bech32m-pq"
  compatibility:
    min_node_version: "1.2.0"
    min_wallet_version: "1.1.0"
    min_sdk_versions:
      typescript: "1.1.0"
      python: "1.0.0"
      rust: "1.0.0"
  phases:
    - phase_id: "announce"
      name: "Publish policy + optional stage"
      activation:
        method: "time"
        time: "YYYY-MM-DDTHH:MM:SSZ"
      actions:
        enable_signatures: ["dilithium3"]
        enable_kem: ["mlkem768"]
        new_key_default: "dilithium3"
  deprecations: []
---

# Post-Quantum Algorithm Rotation: Normal Cadence & Emergency Variant

> **Status:** Draft → RFC → Vote → Enact → Review  
> **Scope:** Governance over accepted signature/KEM algorithms used for **addresses**, **transaction signatures**, and **P2P handshakes**.  
> **Specs:** See `docs/pq/*`, `spec/pq_policy.yaml`, `pq/alg_ids.yaml`, and registry `governance/registries/pq_alg_policy.json`.  
> **Schemas:** `governance/schemas/proposal.schema.json`, `governance/schemas/pq_rotation.schema.json`.

---

## 1) Executive Summary

- **Motivation:** Maintain cryptographic agility against structural breaks or deprecations; keep the platform aligned with NIST/PQC and real-world performance.  
- **Impacted surfaces:**  
  - **Signatures (addresses/tx):** e.g., Dilithium3, SPHINCS+ SHAKE-128s  
  - **P2P Handshake (KEM):** e.g., Kyber-768  
- **Rotation mode:** `<normal | emergency>`  
- **Effective schedule:** see §4 and §5.  
- **Risk level:** `<low|medium|high>` (see §6).  
- **Back-compat:** Verification of legacy signatures remains until **disabled_after** (see §4.2, §5.4).

---

## 2) Background & Current Policy

- Current allowlist & weights live in `governance/registries/pq_alg_policy.json` (machine-readable) and `spec/pq_policy.yaml` (consensus reference).  
- Address formats: `anim1…` bech32m; payload = `alg_id || sha3_256(pubkey)` (see `pq/py/address.py`).  
- Node classes that must follow rotation:
  - **Wallets** (keygen, signing)
  - **Nodes** (verify, handshake)
  - **SDKs/Extensions** (address derivation, signing, verify)
  - **P2P** (KEM/HKDF suite)

---

## 3) Scope of Change

- **Add/Deprecate/Disable** one or more algorithms for: `{address_sig, tx_sig, p2p_kem}`.  
- **Set weights/priorities** (tie-breaks and defaults).  
- **Mandate minimum client versions** for handshake interop.  
- **Update registries:** `governance/registries/pq_alg_policy.json` and `spec/poies_policy.yaml` (if rewards/fees depend on alg weight—rare).

---

## 4) Normal Cadence Rotation

> Predictable, well-signaled schedule (e.g., **once per 12 months**). No known breaks; objective is hygiene, performance, and alignment with standards.

### 4.1 Stages & Timeline (illustrative)
| Stage | Window | Effects |
|---|---|---|
| **Announce** | T0 | Publish proposal & payload. Wallets/SDKs add support (optional). |
| **Optional** | T0 → T0+90d | New algs **allowed**; not default. Legacy still default. |
| **Default** | T0+90d → T0+180d | New wallets default to new algs; nodes verify both. |
| **Deprecated** | T0+180d → T0+360d | Warn on creating new legacy keys/txs; still verify. |
| **Disable** | ≥T0+360d | Nodes stop accepting **new** objects with legacy algs. Historical verification retained for chain integrity. |

> Concrete dates are supplied in the machine-readable payload (§8).

### 4.2 Compatibility
- **Verification:** nodes must verify legacy signatures until **disabled_after**.  
- **Address UX:** wallet UI flags “legacy (discouraged)” after *Deprecated*.  
- **P2P:** handshake suite adds new KEM as **preferred**, keeps legacy for fallback until **min_version** threshold is met.

---

## 5) Emergency Rotation

> Fast path when a critical weakness or ecosystem break is discovered (CVE, practical attack, standardized downgrade, keygen flaw, implementation disaster).

### 5.1 Triggers (any ⇒ emergency path)
- Catastrophic cryptanalysis or forgery feasible under practical costs.  
- Supply-chain compromise of a default library (keygen/sign/verify).  
- NIST curb/stops usage or severe implementation class break.  
- Major ecosystem incident with credible exploitation.

### 5.2 Immediate Actions (T0…T0+24h)
- **Freeze default keygen** for affected alg in wallets/SDKs (feature flag).  
- **Raise network alert** (status page, feeds).  
- **Ship node patch:** soft gate refusing *new* txs with affected alg (configurable allowlist for grace).  
- **P2P:** if KEM affected, prefer alternate KEM immediately; enforce **min_version** sooner.

### 5.3 Short-Horizon (≤7 days)
- **Hotfix release** with enforced policy; testnet activation within 24–48h.  
- **Key migration tooling** shipped in wallets/SDKs.  
- **Backstop grace**: chain keeps verifying existing historical signatures; optionally reject **new** objects after cutoff H/T.

### 5.4 Disable & Cleanup (≤30–60 days)
- **Disable** acceptance of new signatures under affected alg at block **H\***.  
- **Quarantine** handshake suite: remove from preference set once **peer adoption** ≥ X%.  
- **Post-mortem & attestations** published.

---

## 6) Risk & Mitigations

| Risk | Vector | Mitigation |
|---|---|---|
| Interop split | Mixed clients during switch | Phased schedule + min_version gates + testnet soak |
| Wallet lock-in | Users stuck on legacy addresses | Dual-sig/multisig migration guides, batch move scripts |
| Performance regressions | Larger sig/verify costs | Benchmarks & fee calibration, SDK preflights |
| P2P partition | KEM mismatch | Version/caps negotiation & fallback, observability alerts |

---

## 7) Operator & Wallet Actions

**Operators:** update nodes to the pinned versions; verify `pq_alg_policy` root; monitor P2P min_version adoption.  
**Wallets/SDKs:** enable new algs, set defaults per timeline, expose migration flows (export/import, multisig permits).  
**Explorers:** tag addresses by alg_id; show warnings on deprecated usage.

---

## 8) Machine-Readable Payload

Provide **two** JSON blobs: `proposal.json` (envelope) and `payload.json` (authoritative policy change). Validate against schemas.

**proposal.json**
```json
{
  "proposal_id": "GOV-2025.06-PQROT-aurora",
  "kind": "PQRotation",
  "title": "PQ Rotation: Dilithium3→(Dilithium3+SPHINCS+), Kyber768 stays",
  "network": "mainnet",
  "rotation_mode": "normal",
  "discussion": "https://forum.example.org/t/pq-rotation-aurora",
  "voting_window": { "start": "2025-06-10T00:00:00Z", "end": "2025-06-17T00:00:00Z" }
}

payload.json

{
  "policy_version_from": "1.3",
  "policy_version_to": "1.4",
  "affected": ["address_sig", "tx_sig", "p2p_kem"],
  "alg_ids": {
    "address_sig": {
      "allow": ["dilithium3", "sphincs_shake_128s"],
      "default": "dilithium3",
      "deprecated": ["sphincs_shake_128s"],
      "disabled": []
    },
    "tx_sig": {
      "allow": ["dilithium3", "sphincs_shake_128s"],
      "default": "dilithium3"
    },
    "p2p_kem": {
      "allow": ["kyber768"],
      "default": "kyber768",
      "min_version": "v1.8.0"
    }
  },
  "rotation_mode": "normal",
  "timeline": {
    "announce_at": "2025-06-18T00:00:00Z",
    "optional_after": "2025-09-16T00:00:00Z",
    "default_after":  "2025-12-15T00:00:00Z",
    "deprecated_after":"2026-03-15T00:00:00Z",
    "disabled_after":  "2026-06-15T00:00:00Z"
  },
  "emergency": null,
  "notes": "No P2P KEM change; SPHINCS+ remains secondary for cold storage."
}

emergency payload example (replace rotation_mode and add block cutoff):

{
  "policy_version_from": "1.4",
  "policy_version_to": "1.4e",
  "affected": ["address_sig","tx_sig"],
  "alg_ids": {
    "address_sig": { "allow": ["sphincs_shake_128s"], "default": "sphincs_shake_128s", "disabled": ["dilithium3"] },
    "tx_sig":      { "allow": ["sphincs_shake_128s"], "default": "sphincs_shake_128s", "disabled": ["dilithium3"] }
  },
  "rotation_mode": "emergency",
  "emergency": {
    "reason": "CVE-YYYY-XXXX practical forgery path for Dilithium3 impl Z",
    "cutoff": { "block_height": 1234567, "utc_time": "2025-07-01T12:00:00Z" },
    "grace_verify_legacy": true,
    "notify_channels": ["status", "rss", "wallet-push", "validators"]
  },
  "timeline": { "announce_at": "2025-06-28T10:00:00Z" }
}

Validate payloads

npx --yes ajv-cli validate -s governance/schemas/proposal.schema.json -d proposal.json --strict=true
npx --yes ajv-cli validate -s governance/schemas/pq_rotation.schema.json -d payload.json --strict=true

Canonical hashes (record for audit)

python - <<'PY'
import json,hashlib
canon=lambda o: json.dumps(o,sort_keys=True,separators=(',',':')).encode()
for f in ("proposal.json","payload.json"):
    with open(f) as fh:
        print(f"{f}_sha3_256=0x{hashlib.sha3_256(canon(json.load(fh))).hexdigest()}")
PY


⸻

9) Registry & Spec Updates
	•	Update governance/registries/pq_alg_policy.json to reflect allow/default/deprecated/disabled sets.
	•	Recompute and publish alg-policy Merkle root with pq/cli/pq_alg_policy_root.py.
	•	Pin root in spec/alg_policy.schema.json references where applicable.

⸻

10) Monitoring & Success Criteria
	•	Wallet default rate adoption ≥ 80% within 90 days of Default stage (normal).
	•	P2P min_version adoption ≥ 70% within 14 days (emergency) / 60 days (normal).
	•	No increase in signature verification failures; RPC rate for InvalidSignature stable.
	•	Benchmarks show ≤ X% overhead on verify p95.

⸻

11) Migration Playbooks
	•	End-user: “Move funds to new address” wizard; dual-sig permit for contracts; batch tools for many UTXO-like holdings.
	•	Validators/Providers: rotate node identity keys; publish new peer-ids; verify on explorers.
	•	Backups: export legacy keys before deprecation; store prints of new addresses.

⸻

12) Post-Rotation Review
	•	Incident report (if emergency), measurements, residual risk, library updates, SBOMs.
	•	Decide on removing legacy verification codepaths in next major.

⸻

Appendix A — Decision Checklist
	•	Payload validates against schema
	•	Testnet soak complete
	•	Wallets/SDKs released with new defaults
	•	P2P min_version enforced (if KEM change)
	•	Registries updated + Merkle root pinned
	•	Comms prepared (status page, blog, docs)

