# Animica Governance · Charter

> This Charter articulates the **mission, values, and conflict-of-interest (CoI) policy** for participants in Animica governance: proposers, reviewers/maintainers, voters/delegates, guardians, custodians, and operators. It complements:
> - `governance/GOVERNANCE.md` (constitution: roles, powers, checks & balances)
> - `docs/governance/OVERVIEW.md` (actors & flows)
> - `docs/security/RESPONSIBLE_DISCLOSURE.md` (vulnerability reporting)
> - `docs/security/SUPPLY_CHAIN.md` (reproducibility & signing)

---

## 1) Mission

Animica governance exists to **safely evolve** the protocol—parameters, security policies, and version-gated features—while preserving:
- **Safety & liveness** of the chain under adversarial conditions,
- **Determinism & reproducibility** of code, specs, and artifacts,
- **Neutrality** toward applications and users,
- **Transparency & accountability** for all decisions and rollouts.

We optimize for **credible neutrality** and **long-term sustainability** of useful work (PoIES), ensuring the network stays verifiable, permissionless, and post-quantum ready.

---

## 2) Values

1. **User Sovereignty** — Keys and balances are inviolable; governance cannot perform arbitrary state edits.
2. **Technical Rigor** — Decisions anchored in specs, vectors, benchmarks, and documented risks.
3. **Open Process** — Public RFCs, artifact hashes, CI logs, and rationale before activation.
4. **Least Privilege** — Roles, keys, and permissions are scoped, rotated, and time-bounded.
5. **Diversity & Inclusion** — Welcome varied expertise (consensus, DA, ZK, PQ, wallet, ops) across geographies.
6. **Professional Conduct** — Respectful, documented discourse; reasoned dissent is encouraged.
7. **Accountability** — Clear ownership, attestation of readiness, and postmortems for incidents.

---

## 3) Conflict-of-Interest (CoI) Policy

### 3.1 Purpose
To protect decision integrity by **identifying, disclosing, and managing** personal, financial, or organizational interests that could reasonably be perceived to influence governance actions.

### 3.2 Scope
Applies to **all** participants taking an official role in proposals: proposers, reviewers/maintainers, guardians, custodians (e.g., PQ policy & ZK VK registries), voting delegates, and operators acting in an official capacity for rollouts.

### 3.3 What is a CoI?
A **Conflict of Interest** exists when a secondary interest **could** improperly influence (or appear to influence) the performance of official duties. Examples include:
- **Financial**: Equity, tokens, options, token warrants, or revenue sharing in vendors, L2s, wallets, miners, ZK providers, or AICF providers affected by a proposal.
- **Employment/Consulting**: Paid work or advisory roles (including grants/bug bounties) tied to impacted entities.
- **Gifts/Hospitality**: > de minimis value (see §3.7) from impacted parties within the last 12 months.
- **Intellectual Property**: Patents or license positions materially advantaged by a change.
- **Research Funding**: Sponsored studies whose outcomes align with a proposal’s passage.
- **Family/Close Ties**: Immediate family or household members with the above interests.

### 3.4 Obligations

**Disclosure**  
- File/refresh a CoI statement **prior** to participating on a given proposal and **at least annually**.  
- Update within **7 days** if circumstances change.  
- Use the template in **Appendix A**.

**Recusal**  
- If a reasonable person could perceive bias, the participant must **recuse** from review, certification, guardian action, or custodian signing on that proposal.  
- Disclose recusal in the public tracker; recusal **does not** silence technical comments posted as community input (clearly labeled as such).

**Abstain vs. Vote**  
- Delegates with a material CoI should **abstain** or transparently justify any vote, disclosing rationale and mitigation.

**Gifts & Hospitality**  
- Decline or publicly disclose anything > **USD 200** per incident or > **USD 500** aggregate/year from a single entity likely to be impacted.  
- Never accept gifts that come with conditions on votes, reviews, or actions.

**Outside Compensation**  
- Negotiations for employment or compensation with an impacted party must be disclosed; recuse until the cooling-off period (see §3.8).

### 3.5 Review & Enforcement

- **Registry**: A public CoI registry (hash-redacted for sensitive data where justified) is maintained per network.  
- **Verification**: Random spot checks; attestation via signature on CoI statements.  
- **Non-Compliance**: May trigger warnings, public notice, **removal from role**, or temporary bans from official functions.  
- **Appeals**: A lightweight, time-boxed appeal process handled by a rotating triad of maintainers not involved in the case.

### 3.6 Special Cases

- **Custodians (PQ/ZK registries)**: Must have **zero** financial stake in vendors whose artifacts they co-sign, except broad index exposure (<1% of portfolio) and with public disclosure.  
- **Guardians**: Elevated standard; any material CoI forces **mandatory recusal** for that emergency action.  
- **Auditors/Researchers**: May be funded, but funding sources must be disclosed in reports cited by proposals.  
- **Operator Incentives**: Normal block rewards/fees do **not** constitute a CoI; side-payments tied to a proposal do.

### 3.7 De Minimis Thresholds

- Gifts/hospitality: see §3.4 (USD 200 / 500).  
- Token holdings: disclosure required when holdings in an impacted entity **exceed the larger of** USD 5,000 **or** 0.05% of supply/FDV.  
- Research grants: disclose any funding **> USD 5,000** in the last 12 months from impacted parties.

### 3.8 Cooling-Off Periods

- **Post-Role Employment**: 90 days between guardian/custodian/maintainer actions on a proposal and accepting employment/compensation from a party materially benefitting.  
- **Proposal Origination**: Proposers joining a benefitting entity within 60 days should disclose and expect heightened scrutiny on subsequent proposals.

---

## 4) Process Integrity

1. **Public Record** — Proposal envelopes, artifacts, hashes, CI logs, reviews, votes, recusals, and guardian actions are archived and linkable.  
2. **Open Meetings** — Where feasible, reviews occur in public forums with recorded minutes.  
3. **Data Room** — Central index of proposal materials (spec diffs, vectors, economics, security audits).  
4. **Metrics After Activation** — Publish performance and incident analyses; roll back or adjust via rails if needed.  
5. **Security First** — Emergency handling follows `governance/GOVERNANCE.md §8` with a postmortem.

---

## 5) Communications

- **No Selective Disclosure** — Material, non-public information (e.g., zero-day vulnerabilities) is shared under the responsible-disclosure process only.  
- **Attribution & Citations** — Credit contributors; cite external research clearly.  
- **Respect & Inclusion** — Maintain professional tone; no harassment, personal attacks, or doxxing.

---

## 6) Training & Attestation

- **Annual Training** — Short module covering Charter, CoI scenarios, and reporting channels.  
- **Annual Attestation** — Sign a statement affirming compliance; renew CoI declaration.  
- **Orientation** — New role holders complete training **before** exercising powers.

---

## 7) Reporting & Whistleblowing

- **Channels**: Anonymous form, signed email, and security key via `docs/security/RESPONSIBLE_DISCLOSURE.md`.  
- **Protection**: No retaliation against good-faith reporters; maintain confidentiality within legal limits.  
- **Handling**: Triage acknowledged within **5 business days**, with status updates until resolution.

---

## 8) Amendments

- Charter amendments use the **UpgradePlan** process in `governance/GOVERNANCE.md §12`.  
- Safety-critical sections (§3–§5, §7) require higher thresholds, as defined in the Constitution.

---

## Appendix A — CoI Disclosure Template

Name / Handle:
Role(s): (Proposer / Maintainer / Guardian / Custodian / Delegate / Operator)
Network(s): (Mainnet / Testnet / Localnet)
	1.	Financial Interests (tokens, equity, options, warrants):
	•	Entity / Project:
	•	Nature & Size (or “within de minimis”):
	•	Date(s) acquired:
	2.	Employment / Consulting / Grants (past 12 months):
	•	Entity / Project:
	•	Role, compensation (can state “confidential; above/below threshold”):
	•	Period:
	3.	Gifts / Hospitality (past 12 months above de minimis):
	•	Provider:
	•	Approx. value:
	•	Context:
	4.	Intellectual Property:
	•	Patent / License:
	•	Relevance to proposal(s):
	5.	Family / Close Ties:
	•	Nature of relationship:
	•	Entity / Project:
	6.	Current Proposal(s) in Scope:
	•	Proposal IDs / Titles:
	•	Intended participation (review / vote / guardian action / custodian sign):
	7.	Mitigations:
	•	Recusal? (yes/no + scope)
	•	Abstain? (yes/no)
	•	Public disclosure link(s):

I attest the above is accurate and complete to the best of my knowledge, and I will update this statement within 7 days of any material change.

Signature (PQ, e.g., Dilithium3):
Date:

---

## Appendix B — Quick CoI Checklist (Before You Act)

- [ ] Have I checked for any financial/employment/IP ties to impacted parties?  
- [ ] Have I filed/updated my CoI statement for this proposal?  
- [ ] Should I recuse or abstain? Have I documented the recusal publicly?  
- [ ] Are my comments labeled as personal/technical input when recused?  
- [ ] Could a reasonable observer perceive bias? If yes, disclose and mitigate.  

---

*Hash of this file may be recorded in governance metadata for integrity.*
