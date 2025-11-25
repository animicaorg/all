# Multisig Policies — Spend, Upgrades & Rotation

**Version:** 1.0  
**Status:** Active  
**Scope:** Thresholds and procedures for governance-related multisigs on Animica (mainnet/testnet).  
**Related:** `governance/keys/README.md`, `governance/ops/addresses/*`, `governance/policies/TRANSPARENCY.md`, `governance/risk/*`.

---

## 0) Multisig Wallets (by role)

| ID | Network | Purpose | Owners (roles) | Threshold |
|---|---|---|---|---|
| `gov.mainnet` | mainnet | Execute **on-chain governance** actions (queue/activate proposals, param sets) | 5 stewards (community/technical mix) | **3-of-5** |
| `treasury.mainnet` | mainnet | Foundation treasury spends, grants, market ops | 7 signers (board + finance + ops) | **4-of-7** |
| `ops.mainnet` | mainnet | Keys for incident mitigations (non-financial toggles) | 4 signers (SRE leads) | **2-of-4** |
| `gov.testnet` | testnet | Testnet governance | 3 signers | **2-of-3** |
| `treasury.testnet` | testnet | Faucets, bounties, test incentives | 3 signers | **2-of-3** |

> Canonical addresses are recorded in `governance/ops/addresses/{mainnet,testnet}.json`.

---

## 1) Action Matrix — Required Thresholds

| Action | Multisig | Min Threshold | Extra Requirements |
|---|---|---:|---|
| Queue **standard proposal** after passing on-chain vote | `gov.mainnet` | 3-of-5 | Link to tally JSON; timelock ≥ `gov.activate.timelock_days` |
| **Activate** proposal at scheduled height/time | `gov.mainnet` | 3-of-5 | Go/No-Go recorded; abort switch prepared |
| **Emergency abort** within abort window | `ops.mainnet` | 2-of-4 | Incident SEV-1 opened; announcement within 15 minutes |
| **Param hotfix** in Emergency Mode (within absolute bounds) | `gov.mainnet` + `ops.mainnet` | 3-of-5 + 2-of-4 | Max 14-day validity; follow-up proposal required |
| Treasury **grant ≤ $50k** equivalent | `treasury.mainnet` | 3-of-7 | Grant record & milestone plan |
| Treasury **grant > $50k** or multi-tranche | `treasury.mainnet` | 4-of-7 | Board minutes + multisig note |
| Treasury **market operations** (LP/hedging) | `treasury.mainnet` | 4-of-7 | Risk playbook ref; daily cap limits |
| **Key rotation** (add/remove signer) | respective | Current threshold | 7–30 day notice window; see §5 |
| **Testnet** mirrored actions | `*.testnet` | 2-of-3 | Faster timelines; still documented |

---

## 2) Signing & Provenance Requirements

Every executed tx or queued action must attach (in notes/metadata or PR description):

- **Proposal ID / PR** reference (if governance-related)  
- **Tally JSON** link (binding vote) or incident ID (emergency)  
- **Checksums** of affected artifacts (e.g., `chains/checksums.txt`)  
- **Runbook**/activation height and **abort** policy pointer

**Commitment example (YAML):**
```yaml
action: governance.activate
proposalId: GOV-2025-11-VM-OPC-01
tally: governance/examples/tallies/GOV-2025-11-VM-OPC-01.json
height: 1234567
checks:
  - chains/checksums.txt@sha256:... 
  - governance/risk/UPGRADE_RISK_CHECKLIST.md@v1.0
go_no_go: approved-2025-11-20T17:00Z
abort_window_blocks: 5000
3) Spending Guardrails (Treasury)
Per-tx cap (non-programmatic): $250k USD equivalent (4-of-7).

Daily aggregate cap: $500k; weekly $1.5M (unless board-approved waiver).

Stablecoin exposure floor: ≥ 12 months runway post-spend.

DEX/CEX custody split: distribute risk; never > 40% of liquid treasury on a single venue.

Vendor/Grant KYC: follow compliance/ runbook (external repo), attach COI disclosures.

4) Emergency Powers (scope & limits)
Emergency Mode may be invoked for Sev-1 incidents (consensus safety, key compromise, catastrophic regressions). Allowed:

Reduce block gas within bounds

Tighten DA rates within bounds

Disable newly activated feature flags

Pause specific AICF/Beacon providers

Not allowed: changing PQ algorithms, overriding passed tallies, or treasury drains.

Sunset: Emergency changes auto-expire ≤ 14 days; follow-up proposal required.

5) Key Rotation Policy
Cadence:

Governance & ops multisigs: review quarterly; rotate any signer ≥ 24 months old.

Treasury: rolling rotation; at least 1 key refreshed per quarter.

Procedure (add signer):

New signer generates key on HSM/token; posts .asc and attestation.

PR updates maintainers.asc (if repo signer) and ops/addresses/*.json.

Multisig owners co-sign “add owner” tx to reach threshold.

Publish notice in Transparency log with activation date.

Procedure (remove signer):

Open issue with reason (departure/compromise).

Execute “remove owner” tx; raise threshold if needed temporarily.

If compromise: rotate affected secrets; mark revocation certificate in gpg/revocations/.

Grace windows: For planned rotations, keep both old+new live for 7–30 days.

6) Recovery & Deadman Switch
Deadman: If a multisig cannot reach threshold for 14 days during a Sev-1, a pre-authorized break-glass 2-of-2 (ops.mainnet + steward chair) may disable a single feature flag within bounds. Requires immediate Transparency post and governance ratification.

Escrowed revocations: Each signer must store a sealed revocation cert with the security officer.

7) Implementation Notes
Prefer deterministic nonce signing paths; forbid cloud-HSM auto-upgrades during activations.

Record txids of governance actions in the proposal close-out.

Use tagged releases for node binaries; multisig notes must include version strings (git SHA/semver).

8) Testnet Policy
Mirror mainnet policies with relaxed caps and faster cadence.

Encourage dry runs of rotations and activations on testnet at least 7 days prior to mainnet.

9) Auditing & Transparency
Maintain an append-only Multisig Minutes thread (Discourse): link every action, votes, txids, and artifacts.

Quarterly report: signer roster, key ages, policy deviations, pending waivers.

10) Change Log
1.0 (2025-10-31): Initial thresholds, emergency scope, rotation cadence, and documentation requirements.

