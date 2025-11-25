---
adr_id: "ADR-YYYY-NN"
title: "<Concise decision title>"
status: "Proposed" # Proposed | Accepted | Rejected | Deprecated | Superseded by ADR-YYYY-MM
date: "YYYY-MM-DD"
owners:
  - "<@handle or Name>"
reviewers: []
component: ["consensus","runtime","zk","wallet","p2p","da","vm","infra","docs","security"]
scope: "consensus|api|module|service|library|ops"
tags: ["design","performance","security","pq","poies","plumbing"]
links:
  supersedes: null
  superseded_by: null
  depends_on: []
  related: []
---

# ADR: <Concise decision title>

> Keep ADRs crisp (≈1–2 pages). Link deep details instead of inlining them. Prefer diagrams where helpful.

## 1. Context

**Problem Statement.**  
What problem are we solving and for whom?

**Forces & Constraints.**  
- Business/Protocol requirements: …
- Technical constraints (compatibility, data formats, consensus rules, libs, licenses): …
- Non-functional concerns (latency, throughput, UX, operability, auditability): …

**Assumptions.**  
- Enumerate any assumptions that influence scope or feasibility.

**Out of Scope.**  
- Clarify what this ADR explicitly does not address.

## 2. Decision

**One-sentence decision.**  
State the chosen direction in one clear sentence.

**Detailed Description.**  
- What will we build/change?
- Boundaries (where this starts/ends in the system)
- Affected modules/APIs/CLIs/config flags

> If relevant, include a quick sequence/flow diagram.

## 3. Rationale

Why is this the best option given the context and forces?
- Aligns with protocol goals because…
- Minimizes risk of…
- Improves maintainability because…

## 4. Alternatives Considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| A | … | … | … |
| B | … | … | … |
| Do nothing | … | … | … |

## 5. Impact Analysis

**Compatibility / Migrations.**  
- Backward/forward compatibility
- Data migrations (state, DB), replay behavior
- Network/consensus implications

**Security & Privacy.**  
- Threats introduced/removed (link to STRIDE/LINDDUN notes)
- Key material, secrets, or trust roots affected
- Supply chain considerations (SBOM, signatures)

**Performance.**  
- Expected effect on latency/throughput/memory/IO
- Benchmarks or budgets (p95/p99 targets)

**Reliability & Operations.**  
- Failure modes and recovery
- Observability (metrics, logs, traces, alerts)

**Cost & Complexity.**  
- Engineering effort, operational burden, tooling changes

## 6. Design Sketch

- High-level architecture (components, boundaries)
- Data model / schemas (IDs, hashing, domain separation)
- API/ABI changes (REST/RPC/WS/FFI)
- Persistence layout / migrations
- Configuration and feature flags

> Prefer linking to living design docs and prototypes.

## 7. Rollout Plan

**Phases.**  
1. Experiment / behind flag on devnet  
2. Testnet opt-in  
3. Default on testnet  
4. Mainnet guarded launch  
5. Cleanup legacy paths

**Guardrails & Abort Switches.**  
- Metrics/SLOs to watch, thresholds to pause/rollback
- Backward-compatible fallback path

**User & Ecosystem Comms.**  
- Wallet/SDK/explorer docs and deprecation notices

## 8. Testing Strategy

- Unit, property/fuzz, integration, soak
- Compatibility suites (vectors/fixtures)
- Security tests (abuse inputs, differential, side channels)
- Performance tests and reproducible benches

## 9. Open Questions

- List unresolved issues, risks, or dependencies.

## 10. Decision Record

- Proposed: YYYY-MM-DD by <owner>
- Reviewed: …
- Accepted/Rejected: …
- Superseded by: ADR-YYYY-MM (if applicable)

## 11. References

- Prior ADRs: …
- Specs & standards: …
- Issues/PRs: …
- External research: …

---

### Readiness Checklist (gate for “Accepted”)

- [ ] Compatibility/migration plan written and reviewed
- [ ] Security review completed (and actions tracked)
- [ ] Performance budgets/benchmarks documented
- [ ] Observability (metrics/logs/alerts) defined
- [ ] Rollout & rollback plan with owners
- [ ] Docs updated (user/dev/op) and examples added
- [ ] Test coverage plan implemented in CI
- [ ] Licensing & third-party deps vetted

