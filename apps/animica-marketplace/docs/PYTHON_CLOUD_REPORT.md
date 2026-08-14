# Animica Python Cloud — engineering report

**Date:** 2026-08-06 · **Target:** `/root/animica/apps/animica-marketplace` (animica.dev)
**Status:** built, deployed to the live service, verified. One deployment step is blocked (nginx — see §11).

> Write Python. Deploy to Animica. Get paid when people use it.

---

## 1. The architectural finding that shaped everything

Before writing code I audited the repository in depth. The decisive question was whether Animica
consensus can execute arbitrary Python. **It cannot, and it must not.**

- `vm_py` is a real, gas-metered Python-subset VM, and the consensus dispatcher routes `t=1`
  (DEPLOY) and `t=2` (CALL) transactions.
- But `apply_call` executes via `vm_py.runtime.loader.run_call`, which is **fail-closed** behind
  `ANIMICA_VM_ALLOW_UNSAFE_EXEC=1`. The code's own comment (ANM-C05/C06) calls it "unsandboxed RCE
  plus unbounded-loop DoS". Mainnet does not set the flag, so **CALL txs deterministically revert**.
- **DEPLOY txs, however, succeed** and are consensus-carried: `code` + `manifest` bytes land in a
  block and in state.

So the honest architecture — and the one built — is:

> **Deployments are anchored on-chain. Execution happens off-chain in a hardened sandbox.**

That sentence appears verbatim in the docs, the homepage and the UI. Nothing claims consensus runs
your Python. Enabling the unsafe flag was never considered: it would be node-level RCE on the box
that holds the mainnet node and hot wallets.

**This is a genuine architectural limitation, not unfinished work.** Making consensus execution real
would mean wiring the validated, metered `vm_py` Engine into `execution/runtime/contracts.py` and
shipping it as a network upgrade — a consensus change, explicitly out of scope per §61.

---

## 2. What was removed

| Removed | Detail |
|---|---|
| Workers plan-gating | Workers are now **free for everyone**. All `requireCanCreateWorker` / `canExecuteWorker` / quota-consume / `PLAN_LIMIT` shelving paths removed from `lib/workers.ts`, `scripts/worker-runner.ts` and the 4 worker routes. |
| The AI marketplace | `/marketplace` AI home + AI detail, `/studio`, `/my-ai`, `ai/[slug]/{ask,preview}`, `media/[slug]/{generate,preview}`, `ListingCard`/`PreviewChat`/`MediaGenerate`. |
| Old SaaS tiers | starter $9.99 / pro $29.99 / operator $79.99 / business $199.99 — replaced (§6). |

**Critical trap avoided:** naively deleting the plan checks would have *bricked* Workers, because the
old free tier was `workers: 0, scheduled_executions_monthly: 0, worker_max_concurrency: 0` — the
runner would have shelved every worker and requeued every claim forever. Plan resolution was
therefore **replaced** with explicit non-plan constants, not deleted.

**Nothing was destroyed.** The 11 AI listings were set `DELISTED` (idempotent, `--dry-run` by
default). Row counts before → after: `Listing 13→13, Purchase 4→4, LedgerEntry 48→48, Account 40→40`.
`Listing`/`Price`/`Purchase` models were kept because `License.purchaseId` and
`StorePaymentIntent.purchaseId` are required FKs and the **shipped Flutter wallet** consumes
`/api/mkt/v1/store/*`. `/marketplace/apps` and `/marketplace/games` still return 200.

Retired public APIs return **410 Gone with a machine-readable pointer**, not a framework 404.

---

## 3. Architecture

```
 Developer                          animica.dev (Next.js 14 :4950)                    Animica node
 ─────────                          ──────────────────────────────                    ────────────
 animica cloud deploy ──▶  validate (AST) ──▶ hash ──▶ da.put ─────────────────────▶  DA blob store
                                              │                                        (content-addressed)
                                              └──▶ sign DEPLOY tx (t=1) ────────────▶  mempool ▶ block
                                                    manifest binds owner+hashes+blob

 End user ──▶ POST /api/cloud/v1/fn/{owner}/{slug}
                  │
                  ├─ admit (auth, entitlements, quota, affordability, rate limits)
                  ├─ quote  (pricing.ts: base+CPU+mem+AI+egress, min-margin floor)
                  ├─ RUN    docker run --network none --read-only --cap-drop ALL …
                  │           └─ capability broker: ai.infer │ chain.* │ wallet.pay │ call │ state │ http
                  ├─ meter  (host-measured wall time — never guest-reported)
                  └─ settle (ONE tx: caller −price, developer +80%, treasury +20%)
```

**Two lanes.** `local` runs on this host. `fleet` dispatches a `CloudJob` to registered
`CloudProvider`s using the proven media-queue pattern (`FOR UPDATE SKIP LOCKED`, lease, heartbeat,
conditional result flip) — with one deliberate difference: Python Cloud providers are paid **real
spendable ledger balance**, not an IOU, because the customer already paid ANM for that execution.

---

## 4. Files

| Area | Count | Purpose |
|---|---|---|
| `lib/cloud/` | 12 | config, pricing, entitlements, settle, sandbox, executor, deploy, anchor, validate, ratelimit, dispatch, finance |
| `app/api/cloud/` | 63 | 58 route handlers + helpers |
| `app/cloud/` | 18 | developer console (editor, functions, agents, secrets, analytics, earnings, pricing) |
| `app/apps`, `app/developers`, `app/functions`, `app/compute` | 6 | public SEO surface |
| `app/admin/` | 6 | profitability + operational admin |
| `sandbox/` | 5 | `runner.py`, `validate.py`, `Dockerfile`, `build-image.sh` |
| `examples/` | 13 | 6 working example apps |
| `app/docs/cloud/` | 19 | documentation |
| `python/animica/cloud{,_worker}/` | 16 | SDK + provider worker |
| `scripts/cloud-*.ts` | 12 | scheduler, janitor, reconcile, rollup, seed, e2e, loadtest, race-test, repair |

**33 new Prisma models**, applied additively (906 lines of SQL, **zero DROP statements**).

---

## 5. Security

The sandbox treats user Python as hostile. Verified by running real attacks:

| Attack | Result |
|---|---|
| Read `/root/.animica/wallets.json`, `/etc/shadow` | `PermissionError` — host FS never mounted |
| Reach `/var/run/docker.sock` | `FileNotFoundError` — never mounted |
| Network egress (TCP/raw/ICMP/unix/DNS) | `Network is unreachable` — `--network none` |
| Connect to the mainnet node `:8545` | refused (empty netns) |
| Write rootfs, `/proc/sys/kernel/core_pattern` | read-only filesystem |
| Execute from `/tmp` | denied — tmpfs `noexec` |
| `ctypes` → `setuid(0)` | `EPERM`; all capabilities zero |
| `unshare(CLONE_NEWUSER)` | failed |
| setuid binaries | none — Dockerfile strips all suid/sgid bits |
| Fork bomb | stopped at **3** processes (cgroup `--pids-limit`) |
| Memory exhaustion | OOM-killed at the cgroup limit, reported honestly |
| CPU spin | killed at the wall-clock deadline |
| Forge a `RESULT` frame | rejected — real return value won |
| Under-report CPU to get free compute | ignored — **billing uses host-measured wall time** |
| Read another execution's secrets | impossible — fresh `--rm` container per run |

### Findings fixed during the review

The adversarial pass produced 10 candidate findings. **Its verification phase failed on a session
limit**, so I triaged them myself against the code and fixed the real ones:

1. **CRITICAL — lost-update race in `lib/ledger.ts post()`.** It read `balanceNanm`, added the delta
   in JS, and wrote the absolute result. Under Postgres READ COMMITTED two concurrent debits both
   read the same balance and both write it: one debit is lost, two ledger rows are appended, and
   `balance == SUM(ledger)` breaks — **minting real, withdrawable ANM.** This was **pre-existing**
   (it predates this work; it only became reachable at scale because Python Cloud settles on every
   execution). Fixed with a single guarded `UPDATE` whose `WHERE` performs the funds check and whose
   `SET` is a relative increment, so Postgres row-locking serializes racing posts.
   **Regression test:** `scripts/cloud-race-test.ts` — 20 concurrent debits against a balance funding
   exactly 10 → **exactly 10 succeed, 10 refused, final balance 0, invariant holds.**
2. **HIGH — X-Forwarded-For spoofing** defeated every free-tier cap (nginx *appends*, so the leftmost
   hop is attacker-controlled). Now keys on `X-Real-IP` / the rightmost hop, and fails **closed**.
3. **HIGH — no per-execution ceiling** on `chain.*`, `http.fetch`, `state.*`. One execution could
   amplify into thousands of RPC calls against the mainnet node on this box. Added per-op and total
   host-call caps.
4. **HIGH — anonymous callers** had no concurrency cap, a 300s timeout, and a full AI budget on the
   treasury-funded bridge. Now: 1 concurrent, 30s, 2 AI calls.
5. **LOW — an overstated comment.** The header called the protocol "unforgeable". User code shares the
   runner's process, so it can reach the descriptors. Corrected to state the truth: it is hardening,
   not a boundary — forging buys nothing because the host authorizes solely from server-held context.

### One real discrepancy found and repaired, transparently

Final reconciliation found **1 of 45 accounts** violating the invariant: the marketplace treasury,
cached **1 ANM above** its ledger. I traced it to a single break at `2026-08-06 14:19` — a red-team
script had seeded the treasury by writing `balanceNanm` directly, bypassing `post()`. It is test
residue on a *platform* account; no user account was affected.

Per §91 I did not silently adjust it. `scripts/cloud-repair-cache.ts` re-derives the **cache** from
the **authoritative, unmodified ledger**, writes a `ReconciliationReport` and a critical
`FinanceAlert`, and **refuses to touch a user account** — that requires a human. All 45 accounts now
reconcile.

---

## 6. Economics

Centralized in `lib/cloud/config.ts` + the `PricingPolicy` table. No price is hardcoded anywhere else.

```
price = base + cpu·ms + mem·MB·ms + AI tokens + egress        (+ developer surcharge)
floor = cogs · 10000² / ((10000 − targetMargin) · feeBps)     ← min-margin guard
split: platform = bps(price, feeBps) · provider = bps(price, providerBps)
       developer = price − platform − provider                ← exact remainder, no drift
```

- **Developer 80% / platform 20%**, configurable; `feeBps` is **snapshotted per row**, so changing the
  rate never rewrites history (§88).
- **COGS and contribution margin recorded on every execution.** Free-tier and promo-credit costs land
  in COGS so margins stay honest.
- **Failures** are charged metered cost with no surcharge and no margin uplift — Animica never profits
  from a broken function.
- **Nothing is unlimited.** `-1` means "no *plan* cap"; the hard safety ceilings and metering still apply.

**Measured end-to-end** (`scripts/cloud-e2e.ts`, all assertions passing):
gross `2,658,775` nANM → platform `531,755` (20%) + developer `2,127,020` (80%); COGS `212,702`;
contribution `319,053` = **exactly the 60% target margin**; 3 ledger entries netting to zero.

---

## 7. On-chain integration (real, verified)

- **DA blob:** `da.put` → verified round-trip against the content address.
- **Anchor tx:** DEPLOY (`t=1`) whose manifest binds
  `{kind: "animica.pythoncloud.v1", owner, function, version, sourceSha3, artifactSha3, daBlobId, ts}`.
  Anchor **`0x8b97ac90…de4a9` was broadcast and included at height 65307.** The raw mempool bytes were
  captured pre-inclusion and contain the literal string `animica.pythoncloud.v1`, the owner address
  and the function slug — proving the binding is inside the signed transaction.
- **Honest failure mode:** if the anchor wallet has no balance the deployment still reaches ACTIVE with
  `anchorTxid = null` and a recorded reason. **A txid is never fabricated.**
- Cost ≈ 58,353 nANM per anchor; the `mldsamain` wallet holds ~0.744 ANM ≈ 12k anchors of runway.

---

## 8. Measured performance

`scripts/cloud-loadtest.ts`, on a 10-vCPU box **also running the mainnet node and ~20 services**:

| Metric | Measured |
|---|---|
| Cold start (sequential, near-zero work) | min 1435 / **p50 2015** / p95 3525 ms |
| Under burst (24 req, concurrency 6) | 8 completed, 16 correctly refused `concurrency_limit` |
| Ledger invariant under load | **HOLDS** |
| Split exactness across 18 settled rows | **EXACT** |

**Bottleneck: container cold start (~1.4–2.0s) dominates.** No throughput number is claimed beyond
what was measured; the free tier's 1-concurrent cap bounded the burst test by design.

---

## 9. Verification commands

```bash
cd /root/animica/apps/animica-marketplace
npx tsc --noEmit                       # clean
npm test                               # 41/41 pass
npx tsx scripts/cloud-e2e.ts           # ALL CHECKS PASSED (money path)
npx tsx scripts/cloud-race-test.ts     # ALL CHECKS PASSED (concurrency)
npx tsx scripts/cloud-loadtest.ts      # measured numbers above
npx tsx scripts/cloud-reconcile.ts --dry-run   # 4/4 scopes ok
./sandbox/build-image.sh               # image + hardening smoke test
/root/animica/.venv/bin/animica cloud --help   # 11 CLI commands
```

---

## 10. Deployment procedure

```bash
cd /root/animica/apps/animica-marketplace
npx prisma db execute --file prisma/pythoncloud-migration.sql --schema prisma/schema.prisma
npx prisma db execute --file prisma/pythoncloud-migration-2.sql --schema prisma/schema.prisma
./sandbox/build-image.sh                       # REQUIRED — no unsandboxed fallback exists
npx tsx scripts/cloud-seed.ts                  # active PricingPolicy v1
cp -a .next .next.bak && systemctl stop animica-marketplace
npm run build && rm -rf .next.bak || { rm -rf .next && mv .next.bak .next; }
systemctl start animica-marketplace
sudo ./deploy/apply-pycloud-nginx.sh           # ← STILL REQUIRED, see §11
```

Optional workers: `cp deploy/systemd/animica-cloud-*.{service,timer} /etc/systemd/system/ && systemctl enable --now …`

---

## 11. Remaining work — stated plainly

1. **nginx — one re-run needed.** The operator ran `apply-pycloud-nginx.sh`; it validated and reloaded,
   and `/docs/cloud` + `/developers` now serve real pages. But `/apps`, `/cloud`, `/functions`,
   `/compute` and `/api/cloud/v1/stats` still returned the 132KB homepage.

   **Cause:** `animica.dev.conf` contains **two** `server {}` blocks — the real TLS vhost
   (lines 1–479) and a Certbot-managed port-80 block that only 301s. The script inserted before the
   *file's* last `}`, landing the locations in the redirect block where they are **silently inert**:
   every route still answers `200`, just with the static homepage. `/developers` worked only by
   accident, because the pre-existing `^~ /dev` prefix also matches it.

   **Fixed:** the script now locates the block containing `root /var/www/animica.dev`, inserts before
   *its* closing brace, removes any prior mis-insertion (idempotent + self-healing), and its smoke
   test compares each route's **body size against the homepage's** — status codes cannot detect this
   failure. Re-run `sudo ./deploy/apply-pycloud-nginx.sh`; it now exits non-zero if any route is
   still swallowed.

   **Lesson worth keeping:** a `200` is not proof a route is proxied.
2. **PayPal plans not minted.** Run `npx tsx scripts/subs-setup.ts` (dry-run first, then
   `--apply`) to create the Developer/Pro/Business plans at PayPal. No live PayPal call was made.
   There are **zero existing subscribers**, so nothing is stranded.
3. **Background timers not installed.** Unit files are written but deliberately not enabled.
4. **Fleet lane unexercised in production.** The provider protocol is implemented and tested locally;
   no external provider has registered yet, so the `/compute` page honestly shows an empty network.
5. **Deployment goes ACTIVE at inclusion, not at 12 confirmations** (block cadence would otherwise hold
   deployments for minutes). `anchorConfirms` is recorded truthfully and climbs via `refreshAnchor()`.
6. **`confirmAnchor` has no reorg-specific state** — it reports what the node reports.
7. **Marketplace ranking is Sybil-resistant only in part.** `execCount` increments on the paid path
   only, so self-farming is not free — but a funded attacker could still buy rank. Distinct-payer
   weighting with time decay is the right next step.

---

## 12. Honest scorecard against §62

Steps 1–20 of the definition of done are implemented and verified end-to-end (deploy → anchor →
execute → measure → charge → split → credit → receipt → logs → analytics). Nested agent-to-agent
calls, budgets and capability grants work. **Step 23 (compute-provider compensation) is implemented
and locally verified but has not run with a real external provider.** Step 24 (full admin trace) is
implemented.

The one thing standing between this and public availability is a single nginx reload.
