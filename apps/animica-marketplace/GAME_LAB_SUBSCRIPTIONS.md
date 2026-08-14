# Game Lab — Subscriptions (custodial recurring billing across marketplace × Forge × wallet)

Completes the **Free / Paid / Subscription** trifecta for the LIVE Animica app store. A creator can
publish a game (or any DIGITAL_GOOD / APP) priced as a **recurring subscription**; a buyer signs a
one‑time **consent** and the store auto‑renews it each period by debiting the buyer's **in‑app
marketplace balance**. Fully **additive** to the existing Free / One‑time purchase + play flows.

> **HONESTY — read this first.** Subscriptions are **CUSTODIAL by necessity.** The Animica chain has
> no pull‑payment / allowance primitive, so a non‑custodial "auto‑pay from your wallet each month" is
> **impossible**. Instead:
> - Renewals debit the buyer's **in‑app marketplace ledger balance** (`Account.balanceNanm`) — a
>   **custodial** balance that is backed 1:1 by real ANM and is **withdrawable on‑chain at any time**.
> - A renewal happens **only** because the buyer signed a **`StoreConsent`** up front (ML‑DSA‑65
>   signature over a purpose‑bound, single‑use challenge that binds the exact **listing + period +
>   amount**). No consent row ⇒ the worker **never** debits.
> - **Cancel anytime.** Access continues until the paid period ends; there is no partial‑period refund.
>
> The UI (wallet consent sheet, Forge publish form) and every API response say this plainly
> (`custodial: true` + a `disclosure` string). **Do not** describe this as non‑custodial auto‑pay.

---

## 1. The end‑to‑end contract (what agrees with what)

```
Forge publish            Wallet subscribe                     Store backend                 Renewal worker
─────────────            ────────────────                     ─────────────                 ──────────────
POST /store/apps  ──▶  (creates SUBSCRIPTION Price,           GET  /auth/challenge          reads due subs and
  prices:[{model:        periodDays seeded)                     ?purpose=subscribe            renews them each
  SUBSCRIPTION,                │                                 &listing=<slug>              period from balance
  amountNanm,                 │  1. fetch consent challenge     &period=<days>
  periodDays}]                ▼     binding listing/period/     &amount=<nanm>
                        GET /auth/challenge ◀──────────────────  amount
                              │  2. ML-DSA-65 sign
                              ▼  3. POST /store/subscriptions/start
                        POST .../subscriptions/start ─────▶  verify sig → validateChallengeV2
                          {slug, priceId, address,             → enforce bound listing/period/amount
                           challenge, signature, publicKey}    → burn challenge (single-use)
                              │                                → $tx: StoreConsent
                              │                                       + first-period debit (settlePurchaseInTx)
                              │                                       + Purchase{SUBSCRIPTION, ACTIVE,
                              │                                          autoRenew=true, consentId set,
                              │                                          expiresAt = now + periodDays}
                              │                                       + SUBSCRIPTION License
                              ▼                                              │  (at expiry) ────────────▶ picks it up
                        GET /store/subscriptions  ◀── list tails            ▼
                        POST .../subscriptions/{id}/cancel ─▶ autoRenew=false (worker then skips it)
```

**Start‑flow ↔ renewal‑worker field match (verified, exact):** the worker's due‑query is

```ts
where: { priceModel: 'SUBSCRIPTION', status: 'ACTIVE', autoRenew: true,
         expiresAt: { lte: now + RENEW_AHEAD_MS }, renewals: { none: {} } }
```

and `POST /store/subscriptions/start` writes a `Purchase` with **exactly**
`priceModel:'SUBSCRIPTION'`, `status:'ACTIVE'`, `autoRenew:true`, `expiresAt = now + periodDays`,
`consentId` set (+ a linked `StoreConsent` row), and **no** child renewals — so the query selects it,
and `renewOne()` (which additionally requires `consentId` + a `StoreConsent` row before it ever
debits) proceeds. **The worker renews precisely what the start flow writes.** Fee resolution is
identical on both sides (`feeBpsFor(listing.type)` → `STORE_FEE_BPS` for APP/DIGITAL_GOOD, else
`MKT_FEE_BPS`), so the first period and every renewal split the same 70/30.

**Cancel:** `POST /store/subscriptions/{purchaseId}/cancel` walks the renewal chain to its tail and
sets `autoRenew=false`; the worker's `autoRenew:true` filter then excludes it. Access persists to
`expiresAt` (entitlement enforces expiry; grace never extends access).

---

## 2. What shipped in each app

### Marketplace (`apps/animica-marketplace`, :4950)
- **`POST /api/mkt/v1/store/subscriptions/start`** — consent‑gated subscription START (records
  `StoreConsent`, first‑period debit, `autoRenew=true` + `consentId` Purchase, SUBSCRIPTION License).
- **`GET /api/mkt/v1/store/subscriptions[?state=active]`** — lists one row per subscription lineage
  (chain tails), with derived `state` (active | grace | expired | cancelled | refunded).
- **`GET /api/mkt/v1/me/balance`** — custodial ledger balance + a personal deposit address to top up.
- **`POST /api/mkt/v1/purchases`** hardened — now **refuses** a `SUBSCRIPTION` price
  (`400 subscription_requires_consent`) and redirects to the start route, instead of silently
  creating a consent‑less, never‑renewing orphan subscription (the original CRITICAL GAP).
- **Deposit finality** — `lib/deposit.ts` now ages a newly observed deposit delta across
  `TX_FINALITY_CONFIRMATIONS` (default 12) before crediting the ledger (reorg‑safe funding).
- Existing `subscription-renewal-worker.ts` is **unchanged** (the start flow was written to match it).

### Forge Game Lab (`/root/animica-forge`, :4700)
- `lib/store/publish.ts` + `components/game-lab/PublishToStore.tsx` gain a **Subscription** pricing
  mode (Free / One‑time / **Subscription** trifecta), passing `{model:SUBSCRIPTION, amountNanm,
  periodDays}` to `POST /store/apps`. **Free and One‑time paths are unchanged.**

### Wallet (`apps/wallet-mobile-flutter`, Flutter)
- Subscribe consent sheet, Subscriptions manage screen, Top‑up screen, and the store client
  (`marketplace_api.dart`: `subscribe`, `listSubscriptions`, `cancelSubscription`, `balance`).
- **Integration fix applied in this lane** — `subscribe()` was aligned to the built backend
  contract: it now fetches the challenge with `listing`/`period`/`amount` (was `slug`/`price`),
  POSTs to **`/store/subscriptions/start`** (was `/store/subscriptions`, which is GET‑only), and
  includes **`address`** in the body. Without this the wallet subscribe would have hit a
  405/consent_mismatch/address_mismatch. See §5.

---

## 3. Deploy order (orchestrator)

Everything below is **additive**; no genesis / fork / hard‑fork changes.

1. **Store DB migration (deposit funding only).**
   The subscription columns/tables (`Purchase.autoRenew|consentId|graceUntil|parentPurchaseId`,
   `StoreConsent`, `ChallengeBurn`) are **already live** (shipped in `prisma/store-migration.sql`;
   the armed renewal worker already depends on them) — **no new subs migration**. The only pending
   migration is the deposit‑finality columns for the top‑up funding path:
   ```
   psql "$DATABASE_URL" -f prisma/store-migration-deposit-finality.sql   # adds 2 columns to DepositAddress
   ```
   Both columns are `NOT NULL DEFAULT 0` (additive, existing rows backfill to a fresh window).
   Apply this **before** deploying the new `lib/deposit.ts` (it references the 2 columns), then
   `prisma generate` as part of the build.

2. **Rebuild + restart marketplace.** `npm run build` (runs `prisma generate && next build`) then
   restart the :4950 service. Verified clean: `tsc --noEmit` = 0 errors; a full `next build` into an
   alternate distDir registered all four routes
   (`/me/balance`, `/store/subscriptions`, `/store/subscriptions/start`,
   `/store/subscriptions/[purchaseId]/cancel`).

3. **Rebuild + restart Forge** (:4700). `npm run build` then restart. Verified clean: `tsc --noEmit`
   = 0 errors; full `next build` OK. Free / One‑time publish unaffected.

4. **Wallet 0.2.2 build + publish (orchestrator bumps the version).** The working tree is left at
   `0.2.1+7` per the hard rule; the orchestrator bumps `pubspec.yaml` to **0.2.2** (e.g. `0.2.2+8`)
   and builds/publishes the APK. Verified clean: `flutter analyze` on all touched files =
   "No issues found"; `flutter test` = only the **known pre‑existing SPHINCS `keys_test`** failure
   (unrelated; do not touch `rpc.dart` / `address.dart` / vault format). Do **not** ship a wallet
   built against the OLD `subscribe()` (it would call the wrong route) — ship the fixed tree.

5. **Arm the deposit‑watcher** (optional but recommended so top‑ups credit automatically):
   ```
   cp deploy/systemd/animica-store-deposits.{service,timer} /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now animica-store-deposits.timer   # every 5 min
   # observe first: journalctl -u animica-store-deposits -f  → would_credit_deposit / deposit_pending_finality
   # then arm:  set DEPOSIT_WATCHER_ENABLED=1 in .env.production (overrides the unit's Environment=…=0)
   ```
   Default is **observe‑only** (dry‑run) until `DEPOSIT_WATCHER_ENABLED=1`. The renewal worker
   (`animica-store-renewals`, `STORE_RENEWALS_ENABLED=1`) is already armed and needs **no** change.

**No** nginx changes, **no** genesis/fork changes, **no** consensus impact.

---

## 4. Custodial‑honesty checklist (must remain true)

- API responses for subscribe / balance carry `custodial: true` + a plain‑language `disclosure`.
- Wallet consent sheet states: auto‑renew cadence, **custodial in‑app‑balance debit** ("Animica
  can't do non‑custodial auto‑pay"), 70/30 split, cancel‑anytime / no partial refund — **before** the
  user signs.
- Forge publish form + `docs` describe subscriptions as custodial (renewals debit the in‑app balance
  on signed consent, withdrawable / cancel anytime).
- The renewal worker **skips** any subscription lacking a `StoreConsent` row (never debits without
  recorded consent).

---

## 5. Integration reconciliation note (this lane)

The store‑backend and wallet lanes were built against **different assumed contracts**. The backend
built the security‑authoritative route `POST /store/subscriptions/start` binding the exact economic
terms (`listing` + `period` + `amount`) into the signed consent — the blind‑sign defense. The wallet
had assumed `POST /store/subscriptions` with a weaker `slug` + `price` binding and no `address` in the
body. This lane aligned the **wallet** to the built backend (the correct, more secure side):

- `lib/services/marketplace_api.dart` → `subscribe()` now binds `listing`/`period`/`amount`, POSTs to
  `/store/subscriptions/start`, and includes `address`. Signature gained `amountNanm` + `periodDays`.
- `lib/screens/store/store_subscribe_sheet.dart` → passes `widget.price.amountNanm` /
  `widget.price.periodDays` (the store detail route returns full price rows incl. `periodDays`).

No backend or migration change was needed for the reconciliation; the fix is wallet‑client only.

---

## 6. Integration verification (build + static end‑to‑end, 2026‑07‑20)

Concrete results from the integration pass. Builds used the `NEXT_VERIFY_DIST_DIR` alt‑distDir
pattern (config reverted after each build; the live `.next` was never touched).

**Marketplace (:4950)** — `tsc --noEmit` = **0 errors**. `next build` into `.next-verify` = **success**.
All four subscription/funding routes registered as dynamic (`ƒ`):
`/api/mkt/v1/me/balance`, `/api/mkt/v1/store/subscriptions`,
`/api/mkt/v1/store/subscriptions/start`, `/api/mkt/v1/store/subscriptions/[purchaseId]/cancel`.
`prisma/store-migration-deposit-finality.sql` confirmed **additive‑only** (two
`ADD COLUMN … NOT NULL DEFAULT 0` on `DepositAddress`); **not applied** here. No new subscriptions
migration exists or is needed (subscription columns/tables already live).

**Forge (:4700)** — `tsc --noEmit` = **0 errors**. `next build` into `.next-verify` = **success**
(`/game-lab` compiled). Free / One‑time validation preserved: `amountValid`/`periodValid` return
`true` for the Free plan and gate only priced/subscription plans.

**Wallet (Flutter)** — `flutter analyze` on all touched files = **No issues found**; full‑project
analyze surfaces only **19 pre‑existing** infos/warnings in untouched files (home/send/settings/
address/dilithium3/import_export/tests) — **zero new**. `flutter test` = **38 passed, 1 failed**,
the single failure being the known pre‑existing `test/keys_test.dart` (SPHINCS). Version unchanged at
`0.2.1+7`; `rpc.dart` / `address.dart` / vault untouched.

**Static end‑to‑end trace (field‑exact):**

1. **Publish** — Forge `PublishToStore` (Subscription plan) → `publishGameToStore` →
   `POST /store/apps` with `prices:[{model:'SUBSCRIPTION', amountNanm, periodDays, label}]`. Route
   creates `Price{model:'SUBSCRIPTION', amountNanm:BigInt, periodDays:Number(default 30), active}`.
2. **Subscribe** — wallet `subscribe({slug, priceId, amountNanm, periodDays})`:
   `GET /auth/challenge?purpose=subscribe&listing=<slug>&period=<days>&amount=<nanm>` (v2 challenge
   binds those exact params) → sign `UTF8("animica:signMessage:"+challenge)` ML‑DSA‑65 →
   `POST /store/subscriptions/start {slug, priceId, address, challenge, signature:0x…, publicKey:0x…}`.
   Backend: `verifyWalletLogin` (pk binds address; same `SIGN_MESSAGE_DOMAIN` prefix both sides) →
   `validateChallengeV2('subscribe')` enforcing `p.listing==slug|id`, `p.period==String(periodDays)`,
   `p.amount==amountNanm.toString()` → `consumeChallengeV2` (single‑use burn) → one `$transaction`:
   **StoreConsent recorded** → **first‑period debit** (`settlePurchaseInTx`, `feeBpsFor(listing.type)`)
   → `Purchase{priceModel:'SUBSCRIPTION', status:'ACTIVE', source:'balance', **autoRenew:true**,
   **consentId set**, **expiresAt = now + periodDays·86400s**}` → StoreConsent.purchaseId linked →
   SUBSCRIPTION License.
3. **Renewal at expiry** — worker due‑query
   `{priceModel:'SUBSCRIPTION', status:'ACTIVE', autoRenew:true, expiresAt:{lte:now+RENEW_AHEAD_MS}, renewals:{none:{}}}`
   selects the row **exactly** (fresh purchase has no child renewals); `renewOne` additionally
   requires `consentId` + a `StoreConsent` row — both present ⇒ it renews (debit + child Purchase via
   `parentPurchaseId` + new License, parent `autoRenew=false` as the double‑renew guard).
   **The worker renews precisely what the start flow writes — field names match exactly.**
4. **Cancel** — `POST /store/subscriptions/{id}/cancel` walks to the chain tail and sets
   `autoRenew=false` (+`graceUntil=null`); the worker's `autoRenew:true` filter then excludes it.

**Conclusion: the start‑flow ↔ renewal‑worker contract MATCHES**, and the wallet client, Forge
publisher, and store backend agree on every route, body field, and challenge parameter.
