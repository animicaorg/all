# Emergency Procedures (Hot-Fix Path, Scope Limits, Postmortems)

**Status:** Adopted  
**Owners:** Security Council (see `governance/SECURITY_COUNCIL.md`)  
**Related:** `governance/VOTING.md`, `governance/THRESHOLDS.md`, `docs/security/THREAT_MODEL.md`, `docs/ops/RUNBOOKS.md`

This document defines the _strict_ process for invoking emergency authority to protect network safety and user funds, the **maximum scope** of permitted actions, required **transparency**, and the **postmortem** obligations after any activation.

---

## 1) Triggers & Severity

### 1.1 Triggers (any of)
- **Actively exploited critical vuln** that risks consensus safety, fund loss, or widespread liveness failure.
- **Key material compromise** (release signing, seed servers, DA roots, or PQ policy roots).
- **Protocol bug** causing deterministic corruption (invalid state transitions, non-determinism).
- **Coordinated DoS** exceeding documented tolerances and threatening safety or finality.

### 1.2 Severity Levels
- **SEV-1 (Critical):** User funds/safety at risk _now_ or chain safety endangered.
- **SEV-2 (High):** Elevated risk or broad liveness failure likely within hours.
- **SEV-3 (Medium):** Degraded service; mitigations can wait for regular governance.

> **Only SEV-1/2** may invoke emergency powers.

---

## 2) Authority & Quorum

- **Invoker:** Security Council per `SECURITY_COUNCIL.md`.
- **Quorum:** ≥ **2/3** of seated members must sign the activation _and_ the remediation plan.
- **Keying:** Actions are signed with the Council’s **multisig** (or threshold scheme).
- **Time-box:** Any emergency state **expires automatically within 72 hours** unless
  ratified by a normal vote (see §9).

---

## 3) Permitted Emergency Actions (Max Scope)

The Council MAY:
1. **Toggle feature flags** that reduce risk without changing ledger balances:
   - Disable/limit high-risk subsystems (e.g., experimental syscalls, capabilities).
   - Raise **rate limits** / **min gas** / tighten **DoS thresholds**.
   - Temporarily **pause mempool admission** for specific transaction classes.
2. **Freeze or lower caps** (e.g., PoIES Γ/caps) to reduce attack surface.
3. **Rotate cryptographic policy roots** to a safer allowlist (PQ/alg-policy) if compromise suspected.
4. **Quarantine providers** (AICF/Quantum) by moving them to denylist pending validation.
5. **Hot-patch node software** (signed release) that:
   - Rejects known-bad messages/blocks/proofs,
   - Enforces stricter validation, or
   - Disables newly identified foot-guns (config or compile-time flags).
6. **Network-level mitigations**:
   - Tighten P2P topic validators, reduce gossip fanout/mesh degree, enable stricter backpressure.
   - Update seed lists to remove malicious bootstrap hosts.

> All actions MUST be **minimally invasive**, **reversible**, and **configurable**.

---

## 4) Prohibited Actions (Bright Lines)

The Council MUST NOT:
- **Alter user balances**, confiscate funds, or reassign state except via hard fork ratified later.
- **Perform secret chain rewrites** beyond the standard safe reorg bound.
- **Introduce new consensus rules** that permanently change block validity (beyond temporary rejects).
- **Censor classes of transactions** not narrowly tailored to contain the exploit.
- **Extend emergency powers beyond 72h** without normal governance ratification.

---

## 5) Activation Protocol (Step-By-Step)

1. **Triage & Evidence**
   - Assign SEV level; collect PoC, logs, hashes, impacted versions.
   - Designate _Incident Lead_ (IL) and _Comms Lead_ (CL).
2. **Convene Council**
   - Present **Minimal Mitigation Plan** (MMP): goal, flags to toggle, expected blast radius, rollback.
   - Achieve **2/3 signatures** on the _Activation Record_ (see §11 templates).
3. **Prepare Patch/Config**
   - Build signed binaries (or config patch), produce **SBOM** and **artifact hashes**.
   - Dry-run in **devnet/localnet**; record run hashes and pass/fail.
4. **Activate**
   - Publish signed advisory (status page + repo security advisory).
   - Release **hot-fix** (tag `emergency/<YYYYMMDD>-<slug>`), attach checksums and signatures.
   - For config-only mitigations, publish the signed _config delta_ and restart guidance.
5. **Coordinate**
   - Page operators, seed maintainers, large providers/miners, explorers, wallets.
   - Provide step-by-step rollout and expected network effects.
6. **Monitor**
   - Track metrics: fork rate, head lag, mempool pressure, error codes, rejected items.
   - Adjust parameters **within approved scope** only.

---

## 6) Rollback & Sunset

- Each mitigation MUST include a **rollback plan** (feature flags/patch reversion).
- **Auto-sunset** occurs at 72h unless ratified; nodes SHOULD ship a timer-gated revert.
- Post-ratification, convert temporary checks into permanent fixes or remove entirely.

---

## 7) Release & Signing Requirements

- All emergency artifacts:
  - Signed by release key (separate from Council key).
  - Include **SBOM**, build recipe, reproducible build notes, and **deterministic hashes**.
  - Tagged with `emergency/*` and linked to a **security advisory**.
- CI: allow emergency bypass of non-critical checks, but **NEVER** skip tests that affect consensus safety.

---

## 8) Communications

**Channel order (fastest first):**
1. **Status Page** (degraded/maintenance banners).
2. **Signed Advisory** in repo (SECURITY.md appendix), plus RSS.
3. **Operator mailing list / PGP** broadcast.
4. Public posts (blog, social) with plain-language summary.

All statements:
- Provide **hashes**, scope, impact, and **operational guidance**.
- Avoid exploit details until fixes are broadly applied.

---

## 9) Ratification After the Fact

- Within **72 hours**, publish a **Policy/Upgrade** governance proposal describing:
  - What was changed and why,
  - Evidence of need,
  - Diff & hashes,
  - Sunset/rollback plan.
- Passing thresholds per `governance/THRESHOLDS.md`:
  - **Policy** or **Upgrade (soft/hard)** as appropriate.
- Failure to ratify triggers **mandatory rollback** to pre-emergency behavior (or alternative proposal).

---

## 10) Evidence, Audit & Retention

- Preserve build logs, CI artifacts, SBOMs, binary/object hashes, council signatures, and comms.
- Retain **≥ 12 months** (or per-regulatory requirements).
- Make a **public redacted bundle** available where safe; keep full bundle for auditors.

---

## 11) Templates

### 11.1 Activation Record (signed)

Incident-ID: INC-YYYYMMDD-
SEV: 1|2
Scope: (flags|params|patch versions)
Hashes:
	•	src: 
	•	bin-linux-amd64: 
	•	bin-macos-universal: 
	•	bin-windows-x64: 
SBOM: 
Repro-Build:  <nix/lockfile hash> (see docs/research/PQ_MIGRATION.md for formats)
Rollback: 
Sunset: <UTC timestamp +72h>
Signatures (≥ 2/3):
	•	: 

### 11.2 Operator Rollout (checklist)
- [ ] Stop node; backup DB (snapshot).
- [ ] Apply config delta OR install hot-fix binary.
- [ ] Validate binary hash/signature.
- [ ] Restart; confirm chainId/head height; monitor logs.
- [ ] Report success/failure to status form.

### 11.3 Public Postmortem (blameless)

Title: Postmortem —  ()
Summary: <1–3 sentences>
Impact: <users, funds, downtime, versions affected>
Timeline (UTC):
T0 detect → T1 convene → T2 release → T3 stabilize → T4 ratify/rollback
Root Cause: 
Contributing Factors: <process, tests, assumptions>
Detection Gaps: 
Actions:
	•	Short-term fixes (landed)
	•	Long-term remediations (owners & deadlines)
Indicators & Monitoring: <new alerts/dashboards>
Artifacts:
	•	Tags/Hashes: 
	•	SBOM/Build logs: 
Council Signatures: 

---

## 12) Conflict of Interest & Recusal

Council members with a **material conflict** (e.g., vendor directly impacted or potential gain) MUST disclose and **recuse**. If quorum jeopardized, appoint vetted alternates per `SECURITY_COUNCIL.md`.

---

## 13) Drills & Review

- Run at least **semi-annual** emergency drills on **testnet/localnet**.
- Review & update this document after each drill or real incident.

---

## 14) Versioning

- v1.0 — Initial publication aligned with Council charter and voting thresholds.
- Changes to this document require a **Policy** proposal.

