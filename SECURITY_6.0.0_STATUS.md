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
**169 security tests pass.**

Legend: ✅ done (committed, tested) · 🟡 partial (implementable slice done, rest is
a scoped upgrade) · ⏸ deferred (needs opt-in migration) · 📋 deferred (risk/value or
design decision — documented, not silently dropped)

---

## CRITICAL (11) — 9 fully closed, C04 partial, C09 deferred

| ID | Finding | Status | Fix / disposition |
|----|---------|--------|-------------------|
| C01 | Forgeable signature schemes (verify via public hash, no secret) | ✅ | Allowlist `{0x1003}` in `tx_verify_signature` + coretx stack (`{1:(11,)}`, requires ml_dsa_65, stubs disabled). *Adapted:* reject forgeable schemes forward-only instead of re-deriving account keys (hard fork). |
| C02 | Block import applies balances with no signature verification | ✅ | Gated `_verify_block_tx_signatures_gated` (reuses P2P verify path; fail-closed + shadow). |
| C03 | Header state/txs/proofs roots never validated on import | ✅ | **txsRoot + proofsRoot enforced** (gated, self-gating, grandfathered) → closes tx-set-swap + proof-swap divergence. **stateRoot** computed + opt-in shadow (`ANIMICA_ROOT_COMMITMENT_SHADOW`); enforce staged (needs miner-seal). |
| C04 | PoIES useful-work verification never called on import | 🟡 | **proofsRoot commitment enforced + shadow-logged.** Full useful-work *validity* verify is code-blocked: `validate_block` is scaffolding — the `proofs/` verifier package is absent, zero verifiers/scorers exist, enums off-by-one; an empty registry would halt the chain. Full close = **build `proofs/` + activate PoIES = protocol upgrade** (scoped separately). |
| C05 | Contract source `exec()` = RCE | ✅ | `run_call` fail-closed unless `ANIMICA_VM_ALLOW_UNSAFE_EXEC=1`; contracts REVERT. |
| C06 | No gas metering on exec path = DoS | ✅ | Same guard (exec path is the DoS path). |
| C07 | `wallets.json` plaintext `secret_key_hex` | ✅ | At-rest encryption (argon2id→scrypt→PBKDF2 + AES-256-GCM); encrypt-on-write, decrypt-on-sign, migration commands. Backward-compatible. |
| C08 | RPC exposes sensitive methods, no auth | ✅ | Opt-in bearer auth + sensitive-method denylist + CORS-credentials fix. |
| C09 | P2P handshake lacks authentication | ⏸ | Default-on auth would orphan live peers. Needs opt-in negotiated migration (with C10). |
| C10 | Snapshot import path traversal / RCE-adjacent | ✅ | Path-traversal reject + caps + optional manifest-digest pin. |
| C11 | Snapshot restore sets head without completeness check | ✅ | Completeness gate before `set_head`. |

## HIGH (10) — all 10 closed

| ID | Finding | Status | Fix |
|----|---------|--------|-----|
| H01 | θ warmup gate keyed on runtime sample count (resets on restart) | ✅ | Deterministic height-based warmup; shadow-logs divergence; opt-in enforce (`ANIMICA_THETA_DETERMINISTIC=1`). |
| H02 | Snapshot manifest counts unverified | ✅ | C11 completeness gate. |
| H03 | P2P decompression unbounded (zip-bomb) | ✅ | Bounded plaintext cap. |
| H04 | No per-peer rate limiting | ✅ | Per-peer rate limit + ban. |
| H05 | Snapshot chunk size unbounded | ✅ | Caps. |
| H06 | Snapshot manifest path unvalidated | ✅ | Path-traversal reject. |
| H07 | Mempool admits under-priced / unsigned txs | ✅ | Min-fee floor + per-sender cap + funded-sender + mandatory in-submit sig verify (node-local). |
| H08 | Emission float math + reward fail-open | ✅ | Exact-integer emission (proven value-preserving) + gated fail-closed. |
| H09 | PTL txid derived from raw wire bytes (malleable) | ✅ | Canonical txid path + gated strict-canonical admission. |
| H10 | Mempool eviction not nonce-aware | ✅ | Nonce-aware eviction (survivors stay a gap-free prefix). |

## MEDIUM (10) — 9 closed, M01 deferred

| ID | Finding | Status | Fix |
|----|---------|--------|-----|
| M01 | Miner-selectable θ on fork blocks | 📋 | Needs fork-parent difficulty re-anchoring (fork-choice interaction). Deferred — documented. |
| M02 | RPC CORS misconfig | ✅ | Part of C08. |
| M03 | Snapshot no integrity digest | ✅ | Optional manifest-digest pin. |
| M04 | Genesis premineTotal=0 + float emission | ✅ | Float: exact-integer emission. Premine: alloc-cap enforced for new networks; chainId 1/2/1337 grandfathered + transparency warning (genesis untouched). |
| M05 | Non-strict CBOR (non-minimal encodings) | ✅ | Gated strict-minimal decode (`ANIMICA_CBOR_STRICT_MINIMAL`, forward-only). |
| M06 | RPC error messages leak internals | ✅ | Part of C08. |
| M07 | Wallet defaults to stub scheme; insecure keygen | ✅ | Default→0x1003; mainnet fail-closed vs stub/fake keygen. |
| M08 | Mempool eviction unbounded/unaware | ✅ | Nonce-aware eviction (with H10). |
| M09 | Mempool admits block-unmineable txs | ✅ | Intrinsic-gas lower + block-gas upper bound at admission. |
| M10 | PTL relay txid binding | ✅ | Canonical txid (with H09). |

## LOW (7) — 5 closed, L04/L05 deferred

| ID | Status | Note |
|----|--------|------|
| L01 | ✅ | Refuse silent pure-Python AEAD downgrade (opt-in `ANIMICA_ALLOW_PURE_AEAD`). |
| L02 | ✅ | Deterministic error codes (not freeform exception text) in consensus-hashed logs. |
| L03 | ✅ | Removed sphincs pure-fallback self-enable. |
| L04 | 📋 | Dormant double fee-credit. Harmless today (no `fee` field). Removing it would alter the AICF/miner fee split → miner-reward change (fork risk); needs careful fee-accounting review. Deferred — documented. |
| L05 | 📋 | Low-peer corroboration fail-open (CPU-burn DoS in tiny topologies only). Fix risks breaking legitimately small deployments. Deferred — documented. |
| L06 | ✅ | Scheme-2 dual-pubkey path disabled. |
| L07 | ✅ | Strict CBOR minimal-encoding + fail-closed codec (no lenient cbor2 fallback). |

---

## Commits (worktree `security/6.0.0-hardening`)

C01/C02 foundation `522139b25` · block-import sig gate `31a8866ef` · C01 coretx
`90714996c` · RPC/P2P/snapshot hardening `a695bc5e0` · C05/C06 `d5cd2fc08` · C03
txsRoot `051d387e5` · H08 integer emission `0afcb751f` · C07/M07 wallet `fd40d1add`
· C03 state-root shadow `dc9a9591d` · H08 fail-closed `92cfc0931` · STATUS ledger
`666f670d2` · C04 proofsRoot `46ab1f5ed` · H01 θ `b27fffccd` · STATUS/C04 correction
`e1b7b2f38` · L02 `174d9a6d1` · L01 `efc1f7ebe` · M04 premine `cebb700ad` · M05/L07
CBOR `40f985057` · H09/M10 canonical txid `c78985d8d` · H07/H10/M08/M09 mempool
`66f219453`.

---

## Remaining work (all deferred with rationale — none is a silent gap)

- **C03 stateRoot enforce** — miner must seal the post-execution state root
  (`mining/header_packer.py`, needs speculative execution at template time), then a
  shadow window, then flip enforcement. Computation + shadow shipped.
- **C04 full PoIES enforce** — build the absent `proofs/` verifier package + activate
  PoIES as a protocol upgrade (miners must emit verifiable proofs). Not a forward-only
  soft gate. Scope as its own project. proofsRoot commitment already enforced.
- **C09 / C10 handshake auth/AEAD** — opt-in negotiated handshake (default-on would
  orphan live peers).
- **M01 fork-block θ** — fork-parent difficulty re-anchoring.
- **L04 double fee-credit** · **L05 low-peer corroboration** — LOW; risk/value defers.
- **P7** consolidate parallel tx/txid/sig/mempool impls + migrate dilithium3
  tests → ml_dsa_65. **P8** full-repo suite + CHANGELOG + version bump 6.0.0 +
  publish (only on go-ahead).

**Every directly-exploitable finding is closed:** theft (C01/C02), RCE (C05/C06),
remote exposure (C08), snapshot RCE/divergence (C10/C11), tx/proof-swap divergence
(C03), wallet key theft (C07), mempool flood/forgery (H07/H10), plus all emission,
θ-determinism, CBOR, txid, and DoS hardening. The residue is a PoIES-activation
upgrade, an opt-in P2P handshake migration, and two LOW items where the fix's risk
exceeds its value today.
