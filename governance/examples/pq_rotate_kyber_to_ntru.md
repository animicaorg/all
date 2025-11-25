---
proposal:
  id: GOV-2025-11-PQ-ROT-01
  title: "Rotate default KEX from Kyber-768 → NTRU-HPS-2048-677"
  authors:
    - name: "Animica PQ Working Group"
      contact: "pq@animica.dev"
  created: "2025-10-31"
  type: "upgrade"                  # matches governance/schemas/upgrade.schema.json
  target: "pq"                     # pq / protocol / params
  deposit:
    asset: "ANM"
    amount: "150000"
  voting:
    votingPeriodDays: 10
    quorumPercent: 10
    approvalThresholdPercent: 66.7
  versioning:
    pq:
      previousPolicy: "1.1"
      nextPolicy: "1.2"
    node:
      minRequired: "1.9.0"
      recommended: "1.9.1"
  rollout:
    policyVersion: "1.0"
    phases:
      - name: "localnet-dryrun"
        chainId: 1337
        activationTime: "2025-11-06T15:00:00Z"
        actions:
          - "enable_kex:NTRU-HPS-2048-677"
          - "allowlist_dual_kex:true"
          - "telemetry:pq_handshake_mix"
        abortOn:
          - metric: "handshake_fail_rate_percent_15m"
            op: ">="
            value: 2.0
      - name: "testnet"
        chainId: 2
        activationTime: "2025-11-13T15:00:00Z"
        gates:
          - "wallets_release_approved"
          - "explorer_support_merged"
          - "indexers_green_48h"
        actions:
          - "default_kex:NTRU-HPS-2048-677"
          - "retain_kyber_compat:true"
        abortOn:
          - metric: "peer_compat_drop_percent"
            op: ">="
            value: 3.0
          - metric: "api_error_rate_percent_1h"
            op: ">="
            value: 1.0
      - name: "mainnet-staged"
        chainId: 1
        activationTime: "2025-12-11T15:00:00Z"
        gates:
          - "custodians_signoff"
          - "cex_announcements_posted"
          - "wallets_≥90_percent_adoption"
        canaries:
          - "default_kex=NTRU for 10% of new sessions (48h)"
          - "server_prefers_NTRU with client fallback (72h)"
        actions:
          - "default_kex:NTRU-HPS-2048-677"
          - "kyber_deprecation_banner:true"
        abortOn:
          - metric: "connection_establish_latency_p95_ms"
            op: ">="
            value: 800
          - metric: "failed_handshakes_per_block_p95"
            op: ">="
            value: 5
  registriesTouched:
    - "governance/registries/upgrade_paths.json"
    - "governance/registries/contracts.json"
    - "governance/registries/params.json"
  links:
    discussion: "https://forum.animica.dev/t/gov-2025-11-pq-rot-01"
    policy: "governance/PQ_POLICY.md"
    risks: "governance/risk/PQ_ROTATION_RISKS.md"
---

# GOV-2025-11-PQ-ROT-01 — Rotate default KEX from **Kyber-768** to **NTRU-HPS-2048-677**

**One-liner:** Diversify post-quantum cryptography by switching the **default** key-exchange (KEX) used in peer handshakes and wallet onboarding from Kyber-768 to NTRU-HPS-2048-677, while retaining Kyber compatibility during a staged transition.

---

## 1) Motivation

- **Crypto agility:** We commit to regular PQ rotations to reduce correlated risk.
- **Diversity of assumptions:** NTRU (lattice over cyclotomic rings with convolution) offers different structural assumptions than Kyber’s MLWE.
- **Operational learnings:** Measure real-world performance and handshake success across heterogeneous clients and networks under NTRU defaults.
- **Supply-chain resilience:** Broaden library/vendor support and avoid single-primitive monoculture.

This proposal does **not** claim Kyber is broken; it introduces a policy-driven rotation to maintain agility.

---

## 2) Scope & Backward Compatibility

- Default KEX for **new** sessions and **new wallet onboarding** becomes `NTRU-HPS-2048-677`.
- Kyber-768 remains **allowed** during a deprecation window; nodes and wallets must negotiate via supported list `[NTRU-HPS-2048-677, Kyber-768]` in that order (server-prefers).
- Existing addresses/keys are unaffected; address format and signature schemes are unchanged (ed25519/secp256k1/dilithium3).
- Light clients, explorers, indexers: only metadata/telemetry changes.

---

## 3) Technical Specification

### 3.1 Policy & Params
- `pq.policy.version = "1.2"`
- `pq.kex.allowed = ["NTRU-HPS-2048-677", "Kyber-768"]`
- `pq.kex.default = "NTRU-HPS-2048-677"`
- `pq.kex.deprecation.kyber.default_disable_date = "2026-03-01T00:00:00Z"`  # informational; separate vote required to *remove* support

### 3.2 Handshake Negotiation
- Client advertises `kex_supported[]` (ordered by preference).
- Server selects the first intersection with its own ordered `kex_allowed[]`.
- Telemetry tags: `pq_kex=ntru_hps_2048_677` or `pq_kex=kyber_768`.

### 3.3 Wire Formats & Limits
- Message sizes must remain ≤ current max handshake payload (64 KiB); NTRU HPS-2048-677 fits within bounds.
- Retry/backoff unchanged; failed NTRU attempts should fall back to Kyber once before circuit-breaking the peer.

### 3.4 Libraries & Validation
- Reference implementations: `libntru` (C) with constant-time bindings; pure-Python deterministic fallback for tests.
- Test vectors shipped in PQ test suite; cross-verified against upstream vectors.

---

## 4) Rollout & Gates

See machine-readable header. Human summary:

1. **Localnet dry-run (Nov 6, 2025):** Dual-KEX enable, stress tests, handshake mix telemetry.
2. **Testnet (Nov 13, 2025):** Default NTRU with Kyber fallback; wallets/explorers updated.
3. **Mainnet staged (Dec 11, 2025):** Start with server-prefers NTRU and 10% new sessions forcing NTRU; expand to 100% after 72h if green.

**Abort switches** trigger immediate revert to Kyber default via param toggle and release of emergency advisories.

---

## 5) Monitoring & Success Criteria

- Handshake success rate within **0.5%** of Kyber baseline over 72h.
- p95 connection establish latency **< 800 ms**.
- ≥ **70%** of new wallets provision with NTRU during first 14 days.
- No increase in peer disconnects or sync stalls beyond baseline ±1%.

Dashboards: `pq.handshakes.{success,latency}`, `p2p.disconnects`, `wallet.onboarding.kex_share`.

---

## 6) Risks & Mitigations

- **Interoperability gaps:** Some clients may be Kyber-only. *Mitigation:* retain Kyber in `allowed` set; publish SDK updates before testnet phase.
- **DoS by larger keys/ciphertexts:** NTRU payload sizes are within configured limits; *Mitigation:* strict length checks and rate limits.
- **Implementation bugs / side-channels:** Use constant-time primitives, fuzz/corpus tests; enable CI with valgrind/ctgrind where applicable.
- **Ecosystem fragmentation:** Clear comms plan (see `TRANSPARENCY.md`), wallet banners, deprecation timeline opt-in.

See `governance/risk/PQ_ROTATION_RISKS.md` for detailed threat analysis.

---

## 7) Backout / Emergency Plan

- Flip `pq.kex.default` back to `Kyber-768` via emergency param change (multisig).
- If a concrete vuln is reported, publish CVE-style advisory and force-prefer the unaffected KEX; rotate keys where applicable.

---

## 8) On-Chain / Registry Changes

- **Params (chain):**
  - `pq.policy.version = "1.2"`
  - `pq.kex.default = "NTRU-HPS-2048-677"`
  - `pq.kex.allowed = ["NTRU-HPS-2048-677","Kyber-768"]`
- **Registries:**
  - `governance/registries/upgrade_paths.json`: allow `policy 1.1 → 1.2`
  - `governance/registries/contracts.json`: update PQ metadata hashes for node/wallet manifests.

---

## 9) Wallet & DevEx Notes

- Wallets: bump `@animica/keys` to ≥ `v0.7.0` (adds NTRU bindings).
- SDKs: expose `getSupportedKex()`, `setPreferredKex("ntru")`.
- Explorers: annotate handshake streams with PQ KEX tag; show rotation banner.

---

## 10) Sign-Off Checklist

- [ ] PQ test vectors pass on CI (linux/mac arm64/amd64)
- [ ] Node/wallet releases published
- [ ] Indexer/explorer updated
- [ ] Ops runbook updated; on-call briefed
- [ ] Comms posted per `TRANSPARENCY.md`

---

*This is an **example** PQ rotation proposal designed to validate against schemas and drive tooling (ballot generation, tests). Replace IDs/timestamps as needed for real votes.*
