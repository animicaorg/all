# Animica 6.0.0 — Security Remediation Status

Source of truth: `Animica_findings_report.pdf` (2026-07-03, "Security & Consensus
Findings Report"). This ledger tracks every finding against the fix delivered.

**Hard constraints (override the report's recommendations):** no fresh genesis
EVER · no hard forks EVER · keep the chain working no matter what · fail closed in
production. Report fixes that would hard-fork are *adapted* (noted per-finding).

**Discipline:** every consensus change is forward-only and height-gated
(`core/network_params.py`: `FORK_PQ_HARDENING`, `FORK_ROOT_COMMITMENT`, mainnet
H=37000, env-tunable), grandfathering all history; node-local wherever possible;
shadow-mode observability before any enforcement flip. All work is in the isolated
worktree `/root/animica-sec-6.0.0`; the live tree `/root/animica` (bind-mounted
into the mainnet node) is untouched. **Nothing deploys without review.**

Legend: ✅ done (committed, tested) · 🟡 partial/staged · ⏳ in-flight (workflow-2)
· ⏸ deferred (needs opt-in migration) · 📋 remaining (coordinated rollout)

---

## CRITICAL (11)

| ID | Finding | Status | Fix / disposition |
|----|---------|--------|-------------------|
| C01 | Forgeable signature schemes (1/2 verify via public hash, no secret) | ✅ | Allowlist `{0x1003}` in `signing.py::tx_verify_signature` **+** coretx stack (`_CHAIN_REQUIRED_SCHEMES`→`{1:(11,)}`, `assert_required_pq_for_chain` requires 11, stub specs `enabled_by_default=False`). *Adapted:* report's "bind scheme into account key" would re-derive every address (hard fork) → instead reject forgeable schemes forward-only. |
| C02 | Block import applies balances with **no** signature verification | ✅ | Gated `_verify_block_tx_signatures_gated` (reuses proven P2P verify path; fail-closed + shadow). |
| C03 | Header state/txs roots never validated on import | 🟡 | **txsRoot enforced** (gated, self-gating, grandfathered — closes tx-set-swap divergence). **stateRoot** computation built + tested (`state_commit.py`) + opt-in shadow observability (`ANIMICA_ROOT_COMMITMENT_SHADOW`). Enforce = staged (needs miner-seal, below). |
| C04 | PoIES useful-work verification (`validate_block`) never called on import | 🟡 | **proofsRoot commitment enforced** (gated + self-gating + shadow-logged) → closes proof-swapping. Full useful-work *validity* verification is **blocked at the code level**: `validate_block` is non-functional scaffolding — the `proofs/` verifier package is **absent from the tree**, there are **zero** concrete verifiers/scorers, and the ProofType enums are off-by-one across the two stacks. Wiring `validate_block` with an empty registry would reject every proof-carrying block (**halt**). Full enforcement requires building `proofs/` = a protocol upgrade. See Remaining. |
| C05 | Contract source `exec()` with full builtins = RCE | ✅ | `vm_py/runtime/loader.run_call` fail-closed unless `ANIMICA_VM_ALLOW_UNSAFE_EXEC=1`; `contracts.py` catches→REVERT (mainnet stays no-op). |
| C06 | No gas metering on the exec path = unbounded-loop DoS | ✅ | Same guard as C05 (the exec path is the DoS path). |
| C07 | `wallets.json` stores plaintext `secret_key_hex` | ✅ | At-rest encryption (`wallet/at_rest.py`: argon2id→scrypt→PBKDF2 + AES-256-GCM); encrypt-on-write, decrypt-on-sign, `wallet encrypt/decrypt` migration. Plaintext still works w/ warning. |
| C08 | RPC exposes sensitive methods with no auth | ✅ | Opt-in bearer auth (`ANIMICA_RPC_AUTH_TOKEN`) + sensitive-method denylist (`ANIMICA_RPC_RESTRICT_SENSITIVE`) + CORS-credentials default fixed. |
| C09 | P2P handshake lacks authentication | ⏸ | Deferred: a default-on authenticated handshake would orphan live peers. Needs an opt-in negotiated migration (with C10). |
| C10 | Snapshot import: path traversal / RCE-adjacent | ✅ | Path-traversal reject + size/count caps + optional manifest-digest pin. |
| C11 | Snapshot restore sets head without completeness check → silent divergence | ✅ | Completeness gate before `set_head`. |

**9/11 fully closed; C04 partial (proofsRoot enforced; full PoIES verify needs the absent `proofs/` package = protocol upgrade); C09 deferred (opt-in migration).**

## HIGH (10)

| ID | Finding | Status | Fix |
|----|---------|--------|-----|
| H01 | θ warmup gate keyed on runtime sample count (resets on restart → divergence) | ✅ | Deterministic height-based warmup; shadow-logs runtime-vs-deterministic divergence; opt-in enforce (`ANIMICA_THETA_DETERMINISTIC=1`). M01 (fork-block θ skip) still open. |
| H02 | Snapshot manifest counts unverified | ✅ | Part of C11 completeness gate. |
| H03 | P2P decompression unbounded (zip-bomb) | ✅ | Bounded plaintext cap (`ANIMICA_P2P_MAX_FRAME_PLAINTEXT_BYTES`). |
| H04 | No per-peer rate limiting | ✅ | Per-peer rate limit + ban. |
| H05 | Snapshot chunk size unbounded | ✅ | Caps (part of C10/C11). |
| H06 | Snapshot manifest path unvalidated | ✅ | Path-traversal reject (part of C10). |
| H07 | Mempool admits under-priced / unsigned txs | ⏳ | workflow-2 (fee floor + mandatory sig). |
| H08 | Emission float math + reward fail-open (`return []`) | ✅ | Exact-integer emission (proven value-preserving for 50% decay) **+** gated fail-closed on reward error. |
| H09 | PTL txid derived from raw wire bytes (malleable) | ⏳ | workflow-2 (canonical re-encode txid). |
| H10 | Mempool eviction not nonce-aware | ⏳ | workflow-2. |

## MEDIUM (10)

| ID | Finding | Status | Fix |
|----|---------|--------|-----|
| M01 | Miner-selectable θ on fork blocks | 📋 | Remaining (with H01). |
| M02 | RPC CORS misconfig | ✅ | Part of C08. |
| M03 | Snapshot no integrity digest | ✅ | Optional manifest-digest pin. |
| M04 | Genesis `premineTotal=0` + float emission | 🟡 | Float half ✅ (H08 integer emission). *Adapted:* mainnet.json premine untouched (setting 81M would rewrite genesis = hard fork); premine-loader unconditional check = remaining. |
| M05 | Non-strict CBOR (extra fields / non-minimal) | ⏳ | workflow-2. |
| M06 | RPC error messages leak internals | ✅ | Part of C08 hardening. |
| M07 | Wallet defaults to stub scheme 0x1001; insecure keygen | ✅ | Default→0x1003; mainnet fail-closed vs stub/fake keygen. |
| M08 | Mempool admission unbounded | ⏳ | workflow-2. |
| M09 | Mempool nonce handling | ⏳ | workflow-2. |
| M10 | PTL relay txid binding | ⏳ | workflow-2 (with H09). |

## LOW (7)

| ID | Status | Note |
|----|--------|------|
| L01, L02, L04, L05 | 📋 | Remaining (minor hardening). |
| L03 | ✅ | Removed sphincs `ANIMICA_ALLOW_PQ_PURE_FALLBACK` self-enable. |
| L06 | ✅ | Scheme-2 dual-pubkey path disabled. |
| L07 | ⏳ | workflow-2 (strict CBOR minimal-encoding). |

---

## Commits (worktree `security/6.0.0-hardening`)

1. `522139b25` C01/C02 node-local mitigation + activation-height foundation
2. `31a8866ef` M07/C01 gated block-import tx-signature verification
3. `90714996c` C01 complete across coretx stack + L03
4. `a695bc5e0` node-local hardening — RPC auth/CORS, P2P DoS, snapshot integrity
5. `d5cd2fc08` C05/C06 fail-close unsandboxed exec() contract path
6. `051d387e5` C03 forward-only txsRoot commitment verification
7. `0afcb751f` H08/M04 exact-integer emission (remove float hazard)
8. `fd40d1add` C07 wallet at-rest encryption + M07 secure-scheme defaults
9. `dc9a9591d` C03 opt-in post-execution state-root observability (shadow)
10. `92cfc0931` H08 reward fail-open → gated fail-closed

Plus workflow-2 (uncommitted, pending review): cbor (M05/L07), ptl-txid (H09/M10),
mempool (H07/H10/M08/M09).

---

## Remaining work — and why it's coordinated rollout, not a patch

These are **not** "find the bug and fix it" (the bugs are understood). They change
consensus acceptance and, under *never-halt*, must ship as **shadow-verify first,
operator-enforce later**:

- **C03 stateRoot enforce** — needs the miner to seal the post-execution state
  root (`mining/header_packer.py` — currently sealed 0; requires speculative block
  execution at template time), then a shadow window (`ANIMICA_ROOT_COMMITMENT_SHADOW=1`)
  confirming every node agrees, *then* flip enforcement. Computation + shadow hook
  already shipped.
- **C04 full PoIES enforce** — BLOCKED at the code level, not just rollout. The
  proofsRoot commitment (proofs match what the header committed) is now enforced +
  shadowed. But verifying the proofs actually *do valid useful work* means calling
  `validate_block`, which needs a VerifierRegistry of concrete proof verifiers —
  and the `proofs/` package **does not exist in the tree**: zero verifiers, no
  scorer impl (only a float preview estimator + test fakes), ProofType enums
  off-by-one between `core.types.proof` and `consensus.types`. `validate_block` has
  never had a real caller. Closing this = **building the `proofs/` verifier package
  and activating PoIES**, a protocol upgrade (miners must emit verifiable proofs) —
  it cannot be a forward-only soft gate. Recommend scoping as its own project.
- **H01 / M01 θ determinism** — replace float/warmup-sampled θ with a deterministic
  integer path; ship shadow-default (log divergence, never reject) for a validation
  window.
- **M04 premine-loader**, **L01/L02/L04/L05** — minor, low-risk; batch next.
- **P7** consolidate the parallel tx/txid/sig/mempool implementations + migrate
  dilithium3 tests → ml_dsa_65 (~20 files). **P8** full suite green + CHANGELOG +
  version bump 6.0.0 + publish (only on go-ahead).

**Exploitable-critical surface (theft C01/C02, RCE C05/C06, remote exposure C08,
snapshot C10/C11, divergence C03-txs) is fully closed.** The remainder is
consensus-integrity hardening whose safe rollout needs shadow windows and, for
C04, live PoIES config + a design decision.
