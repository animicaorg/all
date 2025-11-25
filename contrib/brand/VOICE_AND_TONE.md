# Animica — Voice & Tone Guide

Animica’s voice is **confident, precise, and constructive**. We speak like expert engineers and thoughtful builders. We avoid hype and vagueness; we show proof (data, audits, benchmarks) and give next steps (commands, links, files).

---

## Principles

1) **Clarity over flourish**  
   - Prefer short sentences, concrete nouns, active verbs.  
   - Replace abstractions with specifics (numbers, file paths, commands).

2) **Precision over hype**  
   - Don’t promise; explain tradeoffs. Cite measurements and dates.

3) **Constructive & respectful**  
   - Encourage, never condescend. Assume good intent.

4) **Actionable by default**  
   - Every message should enable a next action (e.g., “Run `make devnet`”).

5) **Consistency**  
   - Use the same terms across products; align with design tokens and API names.

---

## Style Rules

- Person: **1st-person plural** (“we,” “let’s”) for product voice; **2nd person** (“you”) for guides.  
- Tense: Present for capabilities; future only for time-bound events with dates.  
- Jargon: Allowed if essential; define on first use.  
- Numbers: Use digits (e.g., 3 s blocks, 14 ANM reward).  
- Dates: **YYYY-MM-DD**; time with timezone (UTC).  
- Capitalization: Sentence case for UI labels and docs headings.  
- Terminology (canonical): Animica, PoIES, VM (Python), DA, PQ, Γ (Gamma).  
- Emojis: Avoid in product docs; allowed sparingly in social posts.

---

## Tone Ladder

| Context                    | Tone                                |
|---------------------------|-------------------------------------|
| Security advisory         | Formal, concise, directive          |
| Protocol/governance docs  | Neutral, rigorous, source-linked    |
| API/SDK docs              | Friendly, exact, example-first      |
| Release notes             | Factual, outcome-oriented           |
| Marketing/site            | Optimistic, proof-backed            |
| Social                    | Crisp, inviting, never sensational  |

---

## Canonical Phrases & Terms

- **Correct**: “deterministic Python VM”, “post-quantum signatures (Dilithium3/SPHINCS+)”, “data availability via NMT+erasure coding”, “commit-reveal randomness (VDF+QRNG)”, “gated rollout with canaries”.  
- **Avoid**: “world-class,” “revolutionary,” “unhackable,” “web3 magic,” “soon™”.

---

## Microcopy Patterns

### Buttons / Actions
- Primary: “Connect Wallet”, “Deploy Pool”, “Propose Upgrade”  
- Secondary: “View Docs”, “Download CSV”, “Copy Address”  
- Destructive: “Delete Key” (confirm modal required)

### Empty States
- “No transactions yet.”  
  “Send your first transfer from **Receive**.”

### Tooltips
- Keep ≤ 90 chars, one sentence: “PoIES aggregates useful-work shares into Γ per block.”

### Form Labels
- Short noun phrases: “RPC URL”, “Chain ID”, “Gas Limit”

---

## Error & Alert Copy

**Structure:** What happened → Why (if known) → What to do.

- **Error (client)**: “Failed to connect to RPC. Check your URL and network status, then retry.”  
- **Error (validation)**: “Address must be bech32m (hrp: `am`).”  
- **Warning**: “This upgrade requires a 2-phase rollout. Review the checklist before proceeding.”  
- **Success**: “Proposal submitted. Track status in **Governance → Proposals**.”

Avoid blame (“you did X wrong”). Offer recovery steps and a link/log ID when possible.

---

## Data & Claims

Always accompany claims with **source**:

- Benchmarks → link to methodology, commit hash, hardware.  
- Security → link to audit/report, CVE, PR.  
- Tokenomics → link to on-chain proposal or signed artifact.  
- Use absolute dates: “Activated on **2025-02-14** (UTC).”

---

## Voice by Surface

### 1) Product UI (Explorer / Wallet / Studio)
- **Do**: “Syncing headers… ~12 s remaining.”  
- **Don’t**: “Please wait patiently while we connect.”

Provide progressive disclosure: show advanced info behind “Details”.

### 2) CLI Help
- First line: single-sentence summary.  
- Then flags with defaults; always include examples.

animica chains validate --all

Validate chain registry JSONs against schemas.
yaml
Copy code

### 3) API/SDK Docs
- Start with a runnable snippet; then parameters and types.  
- Include request/response examples with realistic values.

### 4) Governance & Proposals
- Neutral language; separate **Motivation**, **Specification**, **Risks**, **Rollout**, **Backout**.  
- Use checklists and acceptance criteria.

### 5) Release Notes
- Organize by **Added / Changed / Fixed / Security**.  
- Link to PRs and issues; list migrations explicitly.

---

## Inclusive Language

- Prefer neutral terms: “allowlist/denylist.”  
- Avoid idioms and culture-specific jokes in product surfaces.  
- Accessibility: write alt text for images explaining purpose, not decoration.

---

## Examples

### Good
> “We reduced block validation time by **18% (p95)** on M2/16GB by parallelizing NMT proofs (commit `a1b2c3d`). See **Benchmarks** for setup.”

### Needs Work
> “It’s insanely fast now and basically instant.”

### Good
> “This proposal drops `maxBlobSizeBytes` from **4 MB → 3 MB** to reduce propagation time. Backout switch: re-enable 4 MB if p95 > 2.0 s after 72 h.”

### Needs Work
> “We changed some DA parameters to make it better.”

---

## SEO & Titles

- Keep page titles ≤ 60 chars; lead with the noun:  
  - “Animica Data Availability — NMT & Erasure Coding”  
  - “Animica VM — Deterministic Python Contracts”

Meta description ≤ 160 chars: describe exactly what’s on the page.

---

## Internationalization

- Avoid concatenating strings; write full sentences.  
- Don’t embed units in variables (e.g., prefer “3 s” not “3s”).  
- Use ISO formats for dates and keep timezones explicit.

---

## Snippet Templates

**Callout (Docs)**
> **Why it matters** — Determinism removes class of heisenbugs and simplifies audits. See the VM spec for invariants and gas model.

**CLI Footer**
> Need help? `animica help <command>` or see docs: /docs/cli

**Security Advisory**
- Impact, Affected versions, Mitigation, Timeline (UTC), Credits, Links.

---

## Review Checklist

- [ ] Precise, no hype  
- [ ] Next step is obvious  
- [ ] Consistent terminology  
- [ ] Numbers & dates are concrete and sourced  
- [ ] Accessible (contrast, alt text, no color-only meaning)

---

## Updates

Changes to voice/tone require a short PR with: rationale, before/after examples, affected surfaces (UI, CLI, docs, marketing).

