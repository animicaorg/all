# Animica Maintainers · Ownership & Escalation

> Source of truth for **module ownership**, **review responsibilities**, and **escalation paths**.
> Complements: `governance/ROLES.md`, `governance/GOVERNANCE.md`, and `docs/dev/RELEASES.md`.

---

## 1) Ownership Table

> Legend: **L1** = primary maintainers (day-to-day), **L2** = backups/reviewers, **Sec** = security steward for sensitive changes, **Rel** = release captain contact.
> Replace placeholders like `@alice` with real handles.

| Path / Module                       | Scope Highlights                                                           | L1 Maintainers              | L2 / Backups                | Sec Steward     | Rel Captain     |
|------------------------------------|----------------------------------------------------------------------------|-----------------------------|-----------------------------|-----------------|-----------------|
| **core/**                           | types, db, genesis, block/header I/O, canonical encoders                   | @alice, @bob                | @carol                      | @sec-jo         | @rel-one        |
| **consensus/**                      | PoIES scorer, Θ retarget, fork-choice, validator                           | @dan, @eve                  | @frank                      | @sec-jo         | @rel-one        |
| **proofs/**                         | verifiers (Hash/AI/Quantum/Storage/VDF), attestations                      | @grace                      | @heidi                      | @sec-ivy        | @rel-two        |
| **da/**                             | NMT, erasure, DAS verifier, retrieval API                                  | @ivan                       | @jules                      | @sec-ivy        | @rel-two        |
| **p2p/**                            | transports (TCP/QUIC/WS), gossip, sync, discovery                           | @kim                        | @lee                        | @sec-jo         | @rel-one        |
| **rpc/**                            | FastAPI app, methods, WS hub, middleware, metrics                           | @maya                       | @nick                       | @sec-ivy        | @rel-two        |
| **mempool/**                        | admission, priority/fee market, eviction, reorg                             | @olga                       | @paul                       | @sec-jo         | @rel-one        |
| **execution/**                      | state machine, receipts/logs, schedulers, adapters                          | @quinn                      | @riley                      | @sec-ivy        | @rel-two        |
| **vm_py/**                          | validator, compiler/IR, runtime, stdlib, gas table                          | @sara                       | @tomas                      | @sec-ivy        | @rel-two        |
| **capabilities/**                   | blob/compute/zk/random/treasury host providers & ABI                        | @uma                        | @victor                     | @sec-ivy        | @rel-two        |
| **aicf/**                           | provider registry, staking, queue/matcher, settlement, SLA                  | @walt                       | @xena                       | @sec-jo         | @rel-three      |
| **pq/**                             | PQ primitives, address, policy root tooling                                 | @yara                       | @zane                       | @sec-jo         | @rel-three      |
| **zk/**                             | verifiers (Groth16/PLONK/STARK), VK registry, adapters, native accel        | @alice                      | @frank                      | @sec-ivy        | @rel-two        |
| **sdk/python/**                     | Python SDK, RPC/WS, contracts, DA/AICF/randomness                           | @carol                      | @dan                        | @sec-jo         | @rel-three      |
| **sdk/typescript/**                 | TS SDK (browser/node), wallets, contracts                                   | @kim                        | @maya                       | @sec-ivy        | @rel-three      |
| **sdk/rust/**                       | Rust crate: RPC/WS, contracts, proofs                                       | @nick                       | @olga                       | @sec-jo         | @rel-three      |
| **wallet-extension/**               | MV3 extension, provider, PQ keyring, E2E tests                               | @paul                       | @quinn                      | @sec-ivy        | @rel-wallet     |
| **wallet-flutter/**                 | Mobile/desktop wallet app (Flutter), native bridges                          | @riley                      | @sara                       | @sec-ivy        | @rel-wallet     |
| **studio-wasm/**                    | Pyodide VM, browser simulator                                               | @tomas                      | @uma                        | @sec-ivy        | @rel-web        |
| **studio-services/**                | deploy/verify/faucet proxy (FastAPI), storage                               | @victor                     | @walt                       | @sec-jo         | @rel-web        |
| **studio-web/**                     | web IDE (React/Astro), wasm bindings                                        | @xena                       | @yara                       | @sec-ivy        | @rel-web        |
| **website/**                        | marketing site, status endpoints, i18n                                      | @zane                       | @carol                      | @sec-jo         | @rel-web        |
| **installers/**                     | signing pipelines (macOS/Win/Linux), Tauri, Sparkle                          | @eve                        | @grace                      | @sec-ivy        | @rel-release    |
| **docs/**                           | specs, guides, API refs, diagrams                                           | @edit-a, @edit-b            | @edit-c                     | @sec-jo         | @rel-docs       |
| **ops/** / **scripts/**             | devnet/k8s, seeds, CI scaffolding                                           | @ivan                       | @lee                        | @sec-jo         | @rel-ops        |

> Tip: keep `CODEOWNERS` aligned; see appendix.

---

## 2) Responsibilities & Expectations

- **Review SLOs**: First response ≤ **1 business day**; actionable review within **3 business days**. Mark drafts as such.
- **Security-sensitive paths** (keys, signing, consensus, proofs, PQ, zk): require **two L1s + Sec** sign-off for merges to protected branches.
- **Release readiness**: Owners ensure tests/vectors/benchmarks updated; changelog entries present; upgrade docs drafted.
- **Backups**: L2s cover during PTO; update this file when rotations change.

---

## 3) Escalation Paths

**Standard (non-emergency)**  
1. Discuss in the PR/issue; tag L1 owners.  
2. If blocked > 3 business days → ping L2.  
3. Still blocked → **Release Captain** for the train negotiates scope or sequencing.  
4. Cross-module conflict → joint design review with affected L1s + a neutral maintainer.  
5. Process deadlock → raise to governance facilitators per `GOVERNANCE.md`.

**Emergency (security / chain health)**  
1. **IMMEDIATELY** notify **Security Steward** and `security@…` (PGP fingerprint in `docs/security/RESPONSIBLE_DISCLOSURE.md`).  
2. Open a private incident channel; engage **Security Council** if rails may be needed.  
3. Assign an **Incident Commander** (not necessarily the author); follow `docs/ops/RUNBOOKS.md`.  
4. After-action report within **7 days**; link to fixes and CVE/ADV if applicable.

---

## 4) Compatibility & Change Control

- Semantic changes require: spec PR ↔ code PR, vectors, migration notes, **feature flag** or **height gate**.
- Breaking RPC/SDK changes: implement **deprecation window**; bump semver; update `docs/rpc/*` and SDK readmes.
- VK / PQ policy roots: updates must include **hash receipts**, registry proofs, and testnet burn-in.

---

## 5) Becoming a Maintainer

- Demonstrated high-quality contributions, reviews, and incident participation.  
- Sponsorship by **two** current maintainers (from the target area), 1-month shadow period.  
- Confirmation via governance process defined in `GOVERNANCE.md`.

---

## 6) Contact Matrix

- **security@…** (PGP): emergencies, vuln reports.  
- **release@…**: train schedules, freeze windows.  
- **ops@…**: incidents, seeds/NAT/discovery.  
- **docs@…**: style, terminology, structure.

---

## 7) Appendix: CODEOWNERS snippet

> Keep this coarsely aligned; teams can expand per subdir.

Core protocol

/core/                 @alice @bob @carol
/consensus/            @dan @eve @frank
/proofs/               @grace @heidi
/da/                   @ivan @jules
/p2p/                  @kim @lee
/rpc/                  @maya @nick
/mempool/              @olga @paul
/execution/            @quinn @riley
/vm_py/                @sara @tomas
/capabilities/         @uma @victor
/aicf/                 @walt @xena
/pq/                   @yara @zane
/zk/                   @alice @frank

Tooling & UX

/sdk/python/           @carol @dan
/sdk/typescript/       @kim @maya
/sdk/rust/             @nick @olga
/wallet-extension/     @paul @quinn
/wallet-flutter/       @riley @sara
/studio-wasm/          @tomas @uma
/studio-services/      @victor @walt
/studio-web/           @xena @yara
/website/              @zane @carol
/installers/           @eve @grace
/docs/                 @edit-a @edit-b

---

**Version:** v1.0 — update this file via PR; include rotation rationale and confirm in weekly maintainers’ notes.
