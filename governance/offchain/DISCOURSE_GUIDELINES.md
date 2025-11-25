# DISCOURSE GUIDELINES
_Discussion forum etiquette for proposals, reviews, and community topics_

**Version:** 1.0  
**Status:** Active  
**Scope:** Applies to all threads in the official Discourse (or comparable forum) used by the Animica community.

Related: `governance/policies/COMMUNITY_GUIDELINES.md`, `governance/policies/TRANSPARENCY.md`, `governance/risk/*`, `governance/diagrams/GOVERNANCE_FLOW.mmd`, `governance/offchain/SNAPSHOT_INTEGRATION.md`.

---

## 1) Purpose & Expectations

- **Forum = long-form, searchable discussion.** Use it for design, proposals, reviews, and summaries.  
- **Be specific, cite evidence, link artifacts.** Prefer data, benchmarks, and reproducible steps over vibes.  
- **Respect the Code of Conduct.** The Community Guidelines apply here in full.

---

## 2) Thread Types & Prefixes

Use clear prefixes in titles:

- **[RFC]** Early design seeking feedback.  
- **[PROPOSAL]** Ready for schema + bounds checks; includes proposal ID when assigned.  
- **[RISK]** Risk analysis, incident, or postmortem discussion.  
- **[ANN]** Announcements, release notes, meeting minutes (moderators only).  
- **[Q&A]** Questions that have a concrete answer.  
- **[IDEA]** Exploratory brainstorming (may graduate to RFC).

**Title format examples**
- `[RFC] DA blob size modeling for 1–8 MB`
- `[PROPOSAL] GOV-2025-11-VM-OPC-01 — Activate OP_BLAKE3`
- `[RISK] Analysis: Γ control oscillation in high-vol regimes`

---

## 3) Categories & Tags

Create or file under the closest category; add tags to aid discovery.

- **Categories:** `governance`, `vm`, `pq`, `da`, `mempool`, `providers`, `wallets`, `dex`, `research`, `ops`.  
- **Tags (examples):** `proposal`, `rfc`, `risk`, `parameters`, `upgrade`, `gamma`, `kyber`, `ntru`, `dilithium3`, `sphincs+`.

> Moderators may retag/move for consistency; this is not a punishment.

---

## 4) Required Links for Proposals

In the opening post for any **[PROPOSAL]** thread, include:

- Link to the PR or repo path (proposal file with YAML header).  
- Links to **schemas** and **registries** referenced.  
- Risk checklist and any diagrams.  
- Snapshot (advisory) manifest if opened: `governance/ops/snapshots/<id>.snapshot.json`.  
- Planned **vote window** and **snapshot height/ts** (tentative is OK).

---

## 5) Writing Style

- **One topic per thread.** If a proposal bundles multiple knobs, enumerate them and how they interact.  
- **Short paragraphs; bold key terms;** use lists for trade-offs.  
- **Link primary sources** (papers, benchmarks, PRs) and quote sparingly.  
- **Avoid images for text.** Paste text/CSV/JSON where possible so it’s searchable.

---

## 6) Evidence & Reproducibility

- Post **exact commands**, versions, seeds, and machines used.  
- Attach minimal **fixtures** (bounded size) or link to a repo branch.  
- For metrics/graphs, include raw data or a script to regenerate them.

---

## 7) Constructive Debate

- **Disagree with ideas, not people.**  
- Prefer **“show a counter-example”** over “that won’t work.”  
- If you claim regressions or risks, provide **numbers or traces**.  
- Summarize the other side fairly (“steelman”) before offering an alternative.

---

## 8) Conflict of Interest (COI)

If you stand to benefit (vendor, grant, provider, exchange, funded research), add a small **COI** line in your first post or reply:

COI: I operate <provider/exchange>, hold <token>, or receive funding from <org>.

yaml
Copy code

Lack of COI does not imply neutrality; disclosures help readers weigh inputs.

---

## 9) Topic Lifecycles

- **[IDEA] → [RFC] → [PROPOSAL]**: Promote once you have artifacts and bounds analysis.  
- **Stale threads:** After 45 days of inactivity, moderators may close with a summary and links to successors.  
- **Superseded:** Add a top-of-post note pointing to the new thread/PR.

---

## 10) Moderation & Safety

- Follow enforcement ladder in `COMMUNITY_GUIDELINES.md`.  
- **Report** issues via the forum’s flag or `report@animica.dev`.  
- **On-topic only.** Off-topic or repetitive posts may be moved/merged.  
- **No doxxing, harassment, or baiting.** Immediate action may be taken (E3–E5).

---

## 11) Decision Summaries

When a decision is made (approve/reject/changes requested):

- Edit the **top post** with a short **Decision** block:
Decision: Approved at height H on YYYY-MM-DD. Tally: 68% Yes / 22% No / 10% Abstain.
Links: PR #1234 • Tally JSON • Minutes • Release notes

yaml
Copy code
- Link to the corresponding **Transparency** notes and on-chain txids.

---

## 12) Templates

**RFC template (paste into new topic):**
```markdown
# [RFC] <title>
**Motivation:** <problem/goal>  
**Background:** <prior art, constraints>  
**Design sketch:** <approach, diagrams/links>  
**Risks:** <known failure modes + mitigations>  
**Open questions:** <list>  
**Next steps:** collect feedback → refine → convert to PROPOSAL
PROPOSAL topic template (pairs with repo file):

markdown
Copy code
# [PROPOSAL] GOV-YYYY-MM-XXX — <title>
**Repo:** <link to proposal file/PR>  
**Summary:** <1–2 paragraphs>  
**Bounds & deltas:** <what keys change; within limits?>  
**Risk:** <checklist link>  
**Activation plan:** <height/date, canaries, abort>  
**Advisory signal:** <Snapshot link if any>
13) Polls & Off-Chain Signals
Use forum polls only for rough sentiment; they do not replace on-chain governance.

For Snapshot usage and manifests, see SNAPSHOT_INTEGRATION.md.

14) Content Size & Attachments
Prefer text and links; keep attachments small.

Large binaries (videos, data dumps) → link to external hosting with checksums.

15) Accessibility
Provide alt text for images/diagrams.

Use headings and lists for screen readers.

Avoid color-only distinctions in charts.

16) Redactions & Privacy
If sensitive info must be summarized, follow TRANSPARENCY.md §7:

Clearly mark REDACTED segments with reason and sunset date if applicable.

17) Escalation
Technical disputes → propose a minimal experiment and timeline.

Process disputes → open a meta thread under governance with links and a concrete change request.

18) Quick Checklist (Before Posting)
 Clear title with [RFC]/[PROPOSAL]/… prefix

 Correct category + useful tags

 Links to PRs, schemas, data

 Repro steps or vectors

 COI disclosure if applicable

 Action request (what feedback or decision is needed?)

19) Change Log
1.0 (2025-10-31): Initial Discourse etiquette and templates.
