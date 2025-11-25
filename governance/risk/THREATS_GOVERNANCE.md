# THREATS — GOVERNANCE
_Capture-resistance, bribery, and censorship risks for Animica governance_

**Version:** 1.0  
**Status:** Draft (living document)  
**Scope:** On-chain voting, proposal workflow, stewards/multisigs, AICF/Quantum providers, registries/schemas, upgrade activation.

---

## 1) Threat Model

### Assets at Risk
- **Protocol control:** params, PQ suites, VM opcodes, upgrade paths.
- **Funds & economics:** treasury, deposits/escrow, slashing pools.
- **Safety properties:** liveness, correctness, censorship-resistance.
- **Reputation & legitimacy:** fairness of process, transparency trail.

### Adversaries
- **Bribers & cartels:** coordinate off-chain incentives to sway votes.
- **Censors:** block proposals/ballots or withhold activation txs.
- **Sybil swarms:** inflate signaling or overwhelm discussions.
- **Privileged insiders:** rogue stewards/multisigs, key misuse.
- **Service providers:** AICF/Quantum/DA/Beacons manipulating Γ or data.
- **External platform actors:** exchange custodians voting customer funds.

---

## 2) Primary Threats

### T1 — Governance Capture (Cartel/Plutocracy)
**Vector:** Concentrated voting power or custodial power dominates outcomes; long-range creeping capture via delegation.  
**Impact:** Params skewed, harmful upgrades, rent-seeking.  
**Signals:** Rapid delegation swings; high Gini; repeated self-serving proposals.

**Mitigations**
- **Bounds registry:** hard limits in `params_bounds.json` (non-negotiable rails).  
- **Supermajority for critical classes:** ≥66.7% for PQ/VM/upgrade.  
- **Delegation caps & disclosures:** optional soft-caps, custodian transparency.  
- **Timelocks + canaries:** delayed activation with abort switch (see `UPGRADE_FLOW.mmd`).  
- **Diversity dashboards:** concentration metrics in explorer.

---

### T2 — Bribery & Vote Buying
**Vector:** Payments for yes/no; conditional bribes via off-chain escrow; airdrop-style retro bribes.  
**Impact:** Legitimacy loss; economically irrational outcomes.  
**Signals:** Sudden whale wallets voting in lockstep; new addresses with synchronized timing.

**Mitigations**
- **Commit–reveal ballots (optional class):** reduces real-time conditioning.  
- **Quorum + approval thresholds:** raise cost of successful bribes.  
- **Deposit policy:** spam deterrence; cost to propose (`DEPOSIT_AND_REFUNDS.md`).  
- **Attestation & disclosures:** require delegate COI statements.  
- **Post-vote randomness audits:** sample voters to provide provenance.

---

### T3 — Ballot / Proposal Censorship
**Vector:** Prevent proposal intake, suppress ballot submission, or censor activation txs at mempool/operators.  
**Impact:** Minority silencing; stalled upgrades; governance deadlock.  
**Signals:** Divergence between off-chain snapshots and on-chain ballots; mempool rejects for valid txs.

**Mitigations**
- **Multiple submission relays:** redundant endpoints; WS/HTTP fallbacks.  
- **Mempool anti-censorship monitors:** publish admit/latency histograms.  
- **Escalation path:** emergency mirror repos & ingest; steward relay.  
- **Transparency timers:** publish intake-to-open SLA and violations.

---

### T4 — Registry/Schema Tampering
**Vector:** Covert edits to `params_current.json`, schemas, or upgrade paths outside process.  
**Impact:** Silent policy changes; validator-client drift.  
**Signals:** Checksum mismatches; unexpected diffs; unsigned merges.

**Mitigations**
- **Signed releases:** `chains/checksums.txt` + detached `chains/signatures/*`.  
- **CI gates:** `test_schemas.py`, `check_registry.py`, protected branches.  
- **Two-person rule:** multisig/codeowners for governance paths.

---

### T5 — Provider (AICF/Quantum/Beacon/DA) Manipulation
**Vector:** Collusion to bias Γ/randomness/DA availability; selective denial to nudge outcomes or rollouts.  
**Impact:** Economic distortions; failed canaries; governance misreads telemetry.  
**Mitigations**
- **Slashing & Recourse:** see `SLASHING_AND_RECOURSE.md`.  
- **Diversity caps & weighting:** limit provider share; random audits.  
- **Signed telemetry & replayable evidence:** reproducible verification.

---

### T6 — Information Operations & Harassment
**Vector:** Doxxing, targeted harassment, brigading, thread flooding to chill participation.  
**Impact:** Self-censorship; biased deliberation; moderator burnout.  
**Mitigations**
- **Community Guidelines:** enforcement ladder; cross-channel coordination.  
- **Rate limits & slow-mode:** during votes; thread split & summaries.  
- **Minutes & summaries:** reduce rumor surface; amplify canonical sources.

---

### T7 — Custodial / Exchange Voting
**Vector:** Exchanges vote user-deposited ANM or threaten delist/liquidity.  
**Impact:** Outsized leverage; hidden conflicts.  
**Mitigations**
- **Custodian transparency:** require tagged custodian addresses & public policy.  
- **Social layer pressure & listing MoUs:** disclose governance neutrality.  
- **Power normalization (optional):** lower weight of custodial wallets if policy allows.

---

## 3) Controls Matrix (Prevent / Detect / Respond)

| Threat | Prevent | Detect | Respond |
|---|---|---|---|
| T1 Capture | Bounds, supermajority, delegation caps | Gini dashboards, sudden swings | Timelock/abort, emergency review |
| T2 Bribery | Commit–reveal, deposits, disclosures | Pattern analysis, timing clusters | Nullify tainted signals (with proof), re-open vote |
| T3 Censorship | Multi-relay, alt endpoints | Mempool latency admits, relay diffs | Steward relay, mirrored intake, public incident |
| T4 Tampering | Signed releases, CI | Checksum diffs, CODEOWNERS | Revert, postmortem, strengthen perms |
| T5 Providers | Slashing policy, diversity caps | Telemetry audits, equivocation checks | Quarantine, slash, reassign tasks |
| T6 Harassment | Moderation policy | Report volumes, lock triggers | Ejections, appeals, summaries |
| T7 Custodial | Policy MoUs, tagging | Address clustering, exchange flows | Public disclosure, weighting policy |

---

## 4) Parameters (tunable; bounded in registries)

Suggested keys (see `params_bounds.json` / `params_current.json`):

gov.vote.quorum_percent # e.g., 10.0–30.0
gov.vote.approval_threshold_percent # e.g., 50.0–80.0
gov.vote.commit_reveal.enabled # bool
gov.vote.window_days # 3–14
gov.vote.snapshot.type # "height" | "timestamp"
gov.delegation.max_to_single_percent # soft guidance metric
gov.provider.max_share_percent # hard limit for scheduling
gov.activate.timelock_days # 1–14
gov.activate.abort_window_blocks # N blocks after enable

yaml
Copy code

---

## 5) Process Hardening

- **Documented intake SLA** and escalation route (see `ops/calendars/governance.md`).  
- **Reproducible proposals:** machine-readable YAML headers, schema versions pinned.  
- **Dual logs:** off-chain forum + on-chain refs (txids), cross-linked in minutes.  
- **“Two keys to turn”** for activations: steward multisig + DAO vote, where applicable.  
- **Post-activation review:** mandatory 7-day after-action with metrics.

---

## 6) Monitoring & Metrics (publish dashboards)

- Voting: participation%, approval%, unique voters, delegation churn.  
- Concentration: top-N share, HHI/Gini over time.  
- Censorship: mempool admit latency, reject rates by reason.  
- Provider health: availability, equivocation counts, slashes/quarantines.  
- Governance ops: intake-to-open median, PR-to-merge lead time.

---

## 7) Playbooks (quick links)

- **Incident (Sev-1/2):** see `TRANSPARENCY.md §10`.  
- **Upgrade Abort:** see `UPGRADE_FLOW.mmd` (E5 abort path).  
- **Provider Misbehavior:** `SLASHING_AND_RECOURSE.md`.  
- **Deposit/Spam:** `DEPOSIT_AND_REFUNDS.md`.  
- **Community Safety:** `COMMUNITY_GUIDELINES.md`.

---

## 8) Open Questions / TBD

- Commit–reveal adoption per proposal class?  
- Quadratic/conviction voting experiments (off-chain signals only)?  
- Delegation transparency requirements for custodians?  
- Formal detection heuristics publication and privacy trade-offs?

---

## 9) Change Log

- **1.0 (2025-10-31):** Initial threat listing, controls matrix, parameters, and playbooks.

