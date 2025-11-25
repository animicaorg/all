---
# YAML front-matter is optional but helpful for indexers
proposal_id: "GOV-YYYY.MM-<ShortSlug>"
proposal_kind: "<ParamChange | Upgrade | PQRotation | Policy | Treasury>"
network: "<mainnet | testnet | devnet>"
authors:
  - name: "<Your Name>"
    contact: "<@handle or email>"
sponsors: []            # optional: list of delegates/orgs supporting Draft
created: "YYYY-MM-DD"
discussion: "https://forum.example.org/t/…"   # or GitHub/RFC link
snapshot:
  height: 0             # block height for voting power snapshot (if used)
  time: "YYYY-MM-DDTHH:MM:SSZ"
voting_window:
  start: "YYYY-MM-DDTHH:MM:SSZ"
  end: "YYYY-MM-DDTHH:MM:SSZ"
---

# <Title of Proposal>

> **Status:** Draft  
> **Dispositions:** Request for Comments → Vote → Enact → Review  
> **Schema:** See \`governance/schemas/*.json\`. The machine-readable envelope lives in \`proposal.json\` with the detailed payload in \`payload.json\`.

---

## 1) Summary

A crisp 3–5 sentence overview of **what** will change and **why** now.

- **Kind:** <ParamChange|Upgrade|PQRotation|…>  
- **Scope:** <chain params / protocol feature / PQ policy / treasury / docs only>  
- **Impact:** <low | medium | high>  
- **Breaking changes:** <none | list>

---

## 2) Motivation & Problem Statement

- Current behavior / policy
- Problems, incidents, or data motivating change (include charts or links)
- Non-goals and constraints

---

## 3) Specification (Authoritative)

Describe the exact change in unambiguous terms.

- If **ParamChange**: list each parameter → new value and bounds
  - Table: name, module, old, new, min, max, rationale
- If **Upgrade**: semantic version, feature gates toggled, migrations
- If **PQRotation**: algorithm set deltas, deprecations, grace windows
- Wire formats / IDs / hashes if relevant

> The canonical machine-readable payload must be provided in \`payload.json\`  
> and validate against the appropriate schema (see below).

---

## 4) Rationale & Alternatives

- Design tradeoffs
- Alternatives considered (and why rejected)
- Prior art, references, and comparable systems

---

## 5) Backwards Compatibility

- Impact on nodes, wallets, SDKs, contracts, indexers
- Fallback / compatibility shims
- Rollout flags / env vars for staged activation

---

## 6) Security Considerations

- New attack surface; effects on DoS limits, spam economics
- Cryptography & trusted setup implications (if any)
- Key rotations, recovery, downgrade risks
- Monitoring & fast-revert criteria

---

## 7) Economic / Resource Impact

- Fee/reward changes, issuance, treasury effects
- Gas/CPU/IO/latency expectations; rough estimates
- Effect on miners/providers/infrastructure costs

---

## 8) Governance Policy Mapping

- Which registry entries change (params, PQ policy, module owners, etc.)
- Quorum / thresholds required (reference governance/THRESHOLDS.md)
- Who signs off (module owners / security council / maintainers)

---

## 9) Rollout Plan

- Phases (announce → implement → testnet → mainnet)
- Feature flags & kill switches
- Timelines and specific block heights/versions
- Communication plan (docs, release notes)

---

## 10) Testing & Validation

- Unit / integration / e2e coverage expectations
- Test vectors to add or update
- Shadow / canary results (if available)

---

## 11) Observability & Success Metrics

- Dashboards, alerts, and SLOs to watch
- Success criteria; rollback triggers

---

## 12) Dependencies & Risks

- External dependencies (libraries, hardware, services)
- Migration risks and contingency plans

---

## 13) Appendix

- Diagrams, tables, extended proofs, data slices
- Links to PRs and commits (update as the work lands)

---

## Required Files & Schema Validation

This Markdown document is for humans. Two JSON files accompany every proposal:

- **proposal.json** — envelope (conforms to \`governance/schemas/proposal.schema.json\`)
- **payload.json** — proposal-kind specific payload, one of:
  - \`param_change.schema.json\`
  - \`upgrade.schema.json\`
  - \`pq_rotation.schema.json\`

**Validate:**
```bash
# Envelope
npx --yes ajv-cli validate \
  -s governance/schemas/proposal.schema.json \
  -d proposal.json --strict=true

# Payload (select one)
npx --yes ajv-cli validate \
  -s governance/schemas/param_change.schema.json \
  -d payload.json --strict=true

Canonical hash (for reproducibility):

python - <<'PY'
import json,sys,hashlib
def canon(p): return json.dumps(p, separators=(",",":"), sort_keys=True).encode()
env = json.load(open("proposal.json")); pay = json.load(open("payload.json"))
print("proposal_hash=", "0x"+hashlib.sha3_256(canon(env)).hexdigest())
print("payload_hash=",  "0x"+hashlib.sha3_256(canon(pay)).hexdigest())
PY

Record these hashes in the discussion thread and release notes.

⸻

Sign-offs
	•	Module owners ✅
	•	Security review ✅
	•	Economic analysis ✅
	•	Docs updated ✅
	•	Rollout plan approved ✅

Signers (optional, if your process uses signatures):
	•	Author(s): `anim1…` — `dilithium3` sig file: `proposal.sig.json`
	•	Sponsor(s): …

