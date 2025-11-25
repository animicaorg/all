# Conflicts of Interest · Disclosure, Recusal & Recourse Rules

> **TL;DR**  
> 1) *Disclose early, update quickly.*  
> 2) *When in doubt, recuse.*  
> 3) *Independent review decides close calls; transparent records enable trust.*

Complements: `governance/GOVERNANCE.md`, `governance/ROLES.md`, `governance/DELEGATION.md`, `docs/security/THREAT_MODEL.md`.

---

## 1) Scope & Who This Covers

These rules apply to:
- **Token voters & delegates** (incl. recommended delegates directory),
- **Maintainers** (module owners; see `governance/MAINTAINERS.md`),
- **Editors** (docs/spec/site),
- **Security Council** (emergency powers),
- **AICF committee & reviewers**,
- **Vendors/Providers** (including AI/Quantum providers),
- **Foundation/Company staff and contractors**.

“*Acting in a governance role*” includes: proposing, voting, merging code under governance authority, approving releases, adjudicating disputes, awarding grants, or setting parameters/policy.

---

## 2) Definitions

- **Conflict of Interest (COI):** A situation where personal, financial, or organizational interests *could reasonably be perceived* to influence impartial governance actions.  
- **Material Financial Interest:** Ownership, debt, or derivative positions (incl. tokens, options, SAFTs, rev-share) that are *reasonably likely* to be affected by a decision.  
- **Related Party:** Employer, client, investment vehicle, portfolio company, spouse/partner, or dependent.  
- **Gifts/Consideration:** Anything of value (cash, tokens, travel, services, airdrops/allocations).

**Materiality thresholds (defaults):**
- Token or equity **≥ 1%** of circulating supply/company equity **or** position value **≥ $25,000 USD**, whichever is lower.  
- Direct compensation **≥ $5,000 USD** in the prior 12 months from a party affected by the decision.

Projects may adopt stricter thresholds; publish deviations in writing.

---

## 3) Principles

1. **Transparency by default** — public disclosures enable scrutiny.  
2. **Proportionality** — recusal scales with risk of bias and decision impact.  
3. **Independence** — reviewers of COI are independent from the parties.  
4. **Non-retaliation** — disclosures & recusals must not result in adverse treatment.  
5. **Timeliness** — update disclosures within **7 days** of material change.

---

## 4) What Must Be Disclosed

Maintain a public **Disclosure Profile** (see template in §11) and update as needed.

Disclose at minimum:
- **Employment & Advisory**: employer/clients, board roles, funded research.  
- **Holdings & Instruments**: material token/equity/convertibles/loans; lockups/vesting.  
- **Compensation**: grants, payments, bounties, revenue shares relating to Animica or affected projects.  
- **Relationships**: related-party ties to entities directly impacted (vendors, exchanges, providers).  
- **Gifts/Airdrops**: received in the last 12 months from parties impacted by decisions.  
- **Outside Roles**: governance roles in competing or dependent protocols.  
- **Trading Policy** acknowledgment (see §7).

---

## 5) Triggers for Recusal

Recusal is expected when **any** of the following apply:

- You (or a related party) have a **material financial interest** directly affected by the outcome.
- You receive **compensation** tied to the decision (e.g., bonus on parameter change, bounty for a specific merge).
- You hold an **executive/board** or **grant requester** role in an entity under consideration.
- You are involved in **vendor selection** where your employer/client bids or benefits.
- You have **non-public information** (NPI) relevant to market-sensitive decisions (e.g., upcoming parameter changes).
- You previously **advocated for a side** in a paid capacity.

### Recusal actions
- Do not vote/merge/approve.  
- State the recusal on record (short reason).  
- Nominate an independent alternate when appropriate.  
- Abstain from private lobbying on the item.

---

## 6) Close Calls & Waivers

If impacts are **indirect**, **immaterial**, or highly diversified (e.g., index fund), recusal may be unnecessary.  
Request a **COI Review** via the **Conflict Review Panel (CRP)** (see §10) **before** acting.

A **waiver** may be granted when:
- Interest is below thresholds, AND
- No reasonable person would perceive bias, AND
- The role is essential and alternatives would cause undue delay/harm.

Waivers are **publicly logged** with rationale and expiry.

---

## 7) Trading & Blackout Policy (Governance Roles)

To reduce appearance of impropriety:

- **Blackout Window:** No trading **Animica native assets** (ANM or governance-sensitive derivatives) from **T-48h to T+24h** around your vote/merge/decision on market-sensitive items (e.g., fees, Θ/Γ changes, listings, major upgrades).  
- **No Shorting** protocol-native assets while serving in a governance role.  
- **No Front-Running**: if you have NPI, you must not trade until it is public and a cooling-off period of **24h** has elapsed.  
- **Reporting:** Significant trades (≥ $25k) by Security Council, Maintainers, or Delegates in the last 30 days may be disclosed upon request to the CRP for audit under confidentiality.

---

## 8) Gifts, Airdrops, and Sponsored Travel

- **Prohibited:** gifts > **$200** value per source per year if the source is directly affected by your governance decisions.  
- **Airdrops/Allocations:** If non-programmatic (targeted to you/your org), disclose prior to acting on related proposals.  
- **Travel/Events:** Allowed if openly declared; no exclusive perks that would create undue influence.

---

## 9) Recusal & Disclosure Workflow

1. **Identify** potential COI (self or external report).  
2. **Disclose** promptly in your profile and, if actionable item is pending, on the proposal/PR thread:
   - “Recusing due to [employer/client/holding]; see profile.”  
3. **Notify** the relevant chair (Maintainer lead, Council secretary, Governance facilitator).  
4. **Record** in the COI log (public).  
5. **Assign** an independent alternate reviewer/maintainer if needed.  
6. **CRP Review** (optional/required for complex cases) → decision/waiver posted.

---

## 10) Conflict Review Panel (CRP)

**Composition:** 3–5 independent reviewers appointed per `governance/GOVERNANCE.md`, with staggered terms.  
**Duties:**
- Maintain the public **COI Registry** and recusal log.
- Adjudicate **disputes**, **appeals**, and **waiver requests** within **5 business days**.
- Recommend **sanctions** (below) to the appointing authority.

**Process:**
- Intake (public or confidential if NPI).  
- Triage: material? urgent?  
- Request statements and evidence.  
- Decide: *Recuse / No Recusal / Waiver with conditions*.  
- Publish minimal public summary (protect NPI).

---

## 11) Public Disclosure Template (YAML)

```yaml
version: "coi-profile/v1"
identity:
  handle: "@alice"
  contact: "alice@example.org"
  verification: ["PGP:0xABCD...", "DID:web:alice.example"]
roles:
  - "Delegate (params,pq_policy)"
  - "Maintainer (consensus/)"
employment:
  current:
    - org: "Example Labs"
      role: "Research Engineer"
      since: "2023-04"
  advisory:
    - org: "Widget Protocol"
      since: "2024-06"
holdings:
  tokens:
    - asset: "ANM"
      type: "spot"
      exposure: "range: $25k–$50k"
    - asset: "WIDG"
      type: "vesting"
      exposure: "range: $10k–$25k"
  equity:
    - company: "Widget Inc."
      exposure: "range: <1%"
compensation_last_12m:
  - source: "Widget Protocol (advisory)"
    value: ">$5k"
gifts_airdrops:
  - source: "ProviderQ"
    value: "$150 conference ticket discount"
  - source: "N/A"
conflicts_policy_ack:
  trading_blackout_ack: true
  last_updated: "2025-10-11"


⸻

12) Sanctions & Remedies

When violations occur, proportional remedies may include:
	•	Required disclosure update within a deadline.
	•	Mandatory recusal on specified domains for a time-bound period.
	•	Loss of “recommended” delegate status until cured.
	•	Removal from maintainer/editor roles (per process).
	•	Security Council censure or temporary suspension (per charter).
	•	Grant clawbacks or ineligibility for future grants/bounties for willful non-disclosure.
	•	Public notice summarizing the violation and remedy.

Appeals may be filed to the CRP within 14 days of a decision.

⸻

13) Examples (Non-Exhaustive)
	•	Maintainer merging a vendor selection change where their employer is bidding → Recuse; another maintainer merges after review.
	•	Delegate holding <$5k in a broadly diversified index that includes a minor competitor → Likely no recusal, disclose.
	•	Security Council member with large ANM short position around an emergency param change → Prohibited by trading policy; sanctions likely.
	•	AICF reviewer evaluating a provider from which they received consulting fees last quarter → Disclose & Recuse.
	•	Docs editor employed by an exchange editing listing criteria → Disclose; Recuse on that PR.

⸻

14) Records & Retention
	•	COI Registry (public): profiles, recusals, waivers (minimal details).
	•	Confidential annex (limited access): supporting documents, NPI.
	•	Retain records ≥ 2 years after role ends.

⸻

15) Reporting Concerns
	•	Prefer open issue in the governance repo with tag conflict-report.
	•	If NPI/retaliation risk: encrypted email to CRP (publish keys in repo).
	•	Anonymous reporting is accepted; provide verifiable context where possible.

⸻

16) Interaction with Other Policies

This document works alongside:
	•	Security (responsible disclosure, supply chain) — see docs/security/*.
	•	Delegation & Disclosures — governance/DELEGATION.md.
	•	Maintainer Role Definitions — governance/MAINTAINERS.md.
	•	Emergency Procedures — governance/SECURITY_COUNCIL.md.

Where policies conflict, GOVERNANCE.md precedence applies; the CRP interprets ambiguities.

⸻

17) Change Log & Versioning
	•	v1.0 — Initial adoption. Future amendments follow the governance RFC process with a minimum 7-day comment window.

