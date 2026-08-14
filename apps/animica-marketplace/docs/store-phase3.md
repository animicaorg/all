# App Store (phase 3) — frontend + dev portal + extension hard-gate runbook

Phase 1+2 shipped the store **backend** (18 routes under `app/api/mkt/v1/store/`,
plus the workers — see `docs/store-phase12.md`). Phase 3 is the **user-facing
surface** that sits on top of it:

- **Developer Center** — `/dev` (my-apps, per-app editor, build upload, earnings,
  API keys), wallet-signature login via `purpose=devportal` challenge.
- **Storefront** — `/marketplace/apps` + `/marketplace/apps/[slug]` (catalog +
  detail), an "App Store" featured section and an **Apps** nav link on
  `/marketplace`, plus `AppCard` / `InstallCta` components.
- **Admin review queue** — `/admin/store-review` (pending builds, approve /
  reject / delist, refunds, disk-budget panel).
- **Wallet browser extension v0.3.2** — the SECURITY hard-gate for portal GA
  (staged, **not yet published**; see §3).

Merged code state (verified 2026-07-19):

```
cd /root/animica/apps/animica-marketplace
npx tsc --noEmit     # exit 0 — clean
npm run build        # exit 0 — Compiled successfully; types valid; 18/18 static pages generated
```

`next build` reported **zero errors** across two clean runs — no source fixes were
required in this pass. All new pages compiled and the client-shell pages
(`/dev`, `/dev/earnings`, `/dev/keys`) **prerendered static without crashing**;
the data-driven pages (`/dev/apps/[slug]`, `/dev/apps/[slug]/builds`,
`/marketplace/apps`, `/marketplace/apps/[slug]`) are `ƒ (Dynamic)` and fetch the
live routes client-side.

Nothing below has been applied to the live box by the implementation lanes — this
file is the orchestrator's checklist, in order.

---

## 1. Restart the marketplace service (REQUIRED — do not skip)

`npm run build` writes into `.next` **in place** and mints a fresh random
`BUILD_ID` every run (the live service started on `93hUNLPMeD-HX90EoCBLI`; each
build after that produces a new one). The live service
(`animica-marketplace.service`, `next start`) loaded the old `BUILD_ID` and old
chunk hashes into memory, but the build overwrote `.next/static/*` with new
content-hashed filenames. Until the service
restarts, a browser that loads a page off the running server can 404 on chunks.
So the build **must** be followed immediately by a restart. Use the
**stop → backup → build → start** pattern so a bad build never leaves the service
serving a half-written `.next`:

```bash
cd /root/animica/apps/animica-marketplace

# 1. backup the currently-serving .next (fast; ~131M incl. cache)
systemctl stop animica-marketplace
cp -a .next ../.next.bak-$(date +%Y%m%d-%H%M%S)

# 2. build (prisma generate + next build); if it FAILS, restore the backup and
#    `systemctl start animica-marketplace` on the old build, then investigate.
npm run build

# 3. start on the fresh build
systemctl start animica-marketplace
systemctl status animica-marketplace --no-pager | head -12
curl -s http://127.0.0.1:4950/api/mkt/v1/health   # 200
```

(A backup of the pre-build `.next` from this pass is already in the session
scratchpad: `next-backup-<epoch>/.next`. The orchestrator should make its own on
the live box per above — the scratchpad copy is not on the deploy path.)

No nginx change is needed for phase 3 — the store `location ^~ /api/mkt/v1/store/`
block from phase 1+2 (`docs/store-phase12.md` §3) already covers the new
`/store/apps/mine` and `/store/assets` routes (they match the `^~ /store/` prefix),
and the page routes (`/dev`, `/marketplace/apps`, `/admin/store-review`) fall
through the existing catch-all proxy to `:4950`. If the store `location` block was
never applied, apply it now (it is the prerequisite for uploads / downloads).

**No new DB migration** — phase 3 is UI over the phase-1+2 schema. Confirm
`prisma/store-migration.sql` was already applied (`SELECT count(*) FROM "AppBuild";`
must not error). `npm run build` runs `prisma generate`, which is harmless to
re-run.

---

## 2. Auth model the pages depend on (know this before smoke-testing)

- The Developer Center logs in with the **`purpose=devportal`** wallet-signature
  flow: `GET /api/mkt/v1/auth/challenge?purpose=devportal` → `window.animica`
  signs the single-purpose string → `POST /api/mkt/v1/auth/verify` mints an
  httpOnly session **scoped `['publish','withdraw','read']`**.
- `DevGate` probes `GET /api/mkt/v1/store/apps/mine` (requires the `publish`
  scope). A plain buyer `store` session lacks `publish`, so it is correctly shown
  the connect-wallet gate, not the portal.
- `window.animica` is provided by **the wallet browser extension** (desktop) or
  **the dapp-browser inside the mobile wallet**. With neither present the gate
  renders install instructions instead of firing any request.
- **This is the GA hard gate**: the extension that must be trusted to sign the
  devportal challenge is v0.3.2, which closes the `signMessage` approval-bypass
  (see §3). The Developer Center must **not** be announced/GA'd until v0.3.2 is
  the published wallet download.

---

## 3. Wallet extension v0.3.2 — publish BEFORE announcing the portal

The v0.3.1 extension that is **currently live** has a signMessage approval-bypass
(a connected page could self-approve its own signature / exfiltrate secret keys —
a blind-oracle restoration). v0.3.2 wires the method gate + content allowlist that
closes it. The devportal login asks the wallet to sign a challenge, so **shipping
the portal on top of the vulnerable extension is not acceptable** — publish
v0.3.2 first.

Staged, verified, **not published** (this pass):

| Item | Value |
|---|---|
| Zip | `/root/animica/apps/wallet-extension/staging/animica-wallet-extension-chrome-0.3.2.zip` (129569 bytes) |
| sha256 | `f98caf357b81d0d98ff627a2230c530006c05e9d771d2c2ce85773c6e8720397` (matches sidecar `…-chrome.sha256`) |
| version | `0.3.2` (sidecar `…-chrome.version`; manifest inside the zip agrees) |
| Live download (UNCHANGED / still vulnerable) | `/var/www/animica.org/wallet/animica-wallet-extension-chrome.zip` sha `40a0aa92…` (v0.3.1) |

Publish flow (from `apps/wallet-extension/docs/SHIP_NOTES-0.3.2.md`):

```bash
# canonical path: run the repo release script (rebuilds dist, zips, writes
# zip + .sha256 + .version into website/public/wallet/); the download PAGE is
# data-driven — version/sha/size regenerate from those three files at Astro build.
node packages/animica-agent/cli/scripts/release-extension.mjs
# then rebuild+deploy the website so animica.org/wallet advertises 0.3.2 / f98caf35…
```

Verify after publish: `sha256sum /var/www/animica.org/wallet/animica-wallet-extension-chrome.zip`
must be `f98caf35…` and the wallet download page must show `0.3.2`.

**Distribution hazard (must communicate to users):** the extension has no
`update_url`/`key` (load-unpacked), so Chrome will **not** auto-push 0.3.2. Users
on v0.3.1 must manually re-download, verify sha `f98caf35…`, remove the old
unpacked extension and Load-unpacked the new one (have the vault JSON ready to
re-import). Until a user is on 0.3.2, their desktop devportal login rides the
vulnerable signer.

Rollback re-exposes the oracle → **fix-forward only**; do not roll the extension
back to 0.3.1.

---

## 4. Smoke test — per page (after §1 restart, with §3 published for the desktop path)

Load each page in a browser with the wallet available (extension v0.3.2 or the
mobile dapp-browser). `B=https://animica.dev`. Every page's data comes from the
live `/api/mkt/v1` routes; a quick way to pre-check a route is `curl` with a
devportal session cookie or an `anm_mkt_…` bearer key with `publish`+`buy` scopes.

### 4.1 Developer Center `/dev` (my apps)
- Unauthenticated / no wallet: renders the **connect gate** with extension +
  mobile-dapp instructions and fires **no** authed request (confirm in devtools).
- Click **Connect** → wallet prompts to sign the `devportal` challenge → gate
  flips to the portal; address shows short-form in the nav.
- Route probe: `GET /store/apps/mine` returns owner listings **including DRAFTs**
  with per-build-status counts (public `/store/apps` hides drafts).
- **Create app** form POSTs `/store/apps` → new listing appears (DRAFT chip).
- Nav (My apps / Earnings / API keys) routes without re-login (session cookie).
- **Sign out** → `POST /api/mkt/v1/auth/logout` clears the cookie → back to gate.

### 4.2 App editor `/dev/apps/[slug]`
- Loads via `GET /store/apps/mine?slug=…` (owner detail the public route hides for
  drafts) + `…/prices` + `GET /store/assets` (owner imagery; public detail omits
  assets for DRAFTs).
- Edit metadata/category/tagline/description/visibility/.anm-link → **Save** PATCHes
  `/store/apps/[slug]`; reload shows persisted values.
- **Prices**: add/change → PUT `/store/apps/[slug]/prices` (deactivates + recreates
  the active set; never DELETEs — Price rows are referenced by Purchases).
- **Imagery**: upload → POST `/store/assets`; delete → DELETE `/store/assets?id=…`.
- **Publish / Delist / Draft** controls flip listing status (PATCH). A listing with
  no APPROVED build cannot be usefully published — expect the storefront to keep it
  hidden until a build is approved.

### 4.3 Build upload `/dev/apps/[slug]/builds`
- Drag-drop (or pick) an **APK** → POST `/store/apps/[slug]/builds` (raw body,
  through nginx — proves the 512m `client_max_body_size` block).
- Verdict renders live from `apkVerify`: signature scheme, signer cert DN,
  permission list with **sensitive** flags called out, sha3-256, min/target SDK.
- A **debug-signed** APK comes back `CHECKS_FAILED` (fail-closed cert policy); a
  **release-signed** one lands `PENDING_REVIEW` (first build is never auto-approved).
- Release-history list shows prior builds + their review status.

### 4.4 Earnings `/dev/earnings`
- Loads `GET /me/earnings`, `…?byListing=1` (per-listing breakdown grouped on
  `SALE_CREDIT`/`FORK_ROYALTY` ledger refs), `/balance`, `/ledger`.
- **Withdraw** POSTs `/withdrawals` (subject to `PAYOUT_ENABLED` +
  `PAYOUT_MAX_PER_TX_ANM`/`…_PER_DAY_ANM` caps; if the payout gate is OFF the
  request is queued/observe-only per phase-1+2 config).
- History table renders past credits/withdrawals; amounts are ANM (base-unit
  math is done client-side in `app/dev/ui.tsx`, mirroring the admin console).

### 4.5 API keys `/dev/keys`
- Lists keys `GET /keys`; **Mint** POSTs `/keys` (shows the secret **once**);
  **Revoke** DELETEs `/keys?id=…` (own key only). Confirm a revoked key then 401s.

### 4.6 Storefront `/marketplace/apps` + `/marketplace/apps/[slug]`
- `/marketplace/apps`: hero/search/category chips/KPI + grids of `AppCard`
  (icon, name, category, price chip, verified badge). Mirrors `GET /store/apps`
  (PUBLISHED + PUBLIC, APP requires an APPROVED build). The one live listing
  (E2E Test Pack) should render.
- Detail `[slug]`: icon, screenshot scroller, About, latest-build metadata
  (sha3, signer cert), permissions with sensitive ones highlighted, reviews,
  sticky pricing + **Get in the Animica Wallet** `InstallCta`
  (deep-link `animica://store/apps/<slug>`).
- Cross-links: `/marketplace` shows the featured **App Store** section + **Apps**
  nav link; `?type=APP` redirects to `/marketplace/apps`; a store-type listing at
  `/marketplace/[slug]` redirects to `/marketplace/apps/[slug]` (the AI detail
  renders PreviewChat, wrong for apps).

### 4.7 Admin review queue `/admin/store-review`
- Auth: `x-admin-token` (held in sessionStorage) **or** an `ADMIN`-role session
  cookie. Pending-builds queue with status tabs + counts
  (`GET /store/admin/builds`, `…/stats`).
- Per build: sig scheme/signer/cert badging, all permissions + sensitive
  call-outs, cert-continuity flags, packageName-vs-claim flag, sha3/size/SDK.
- **Approve / Reject / Delist** → `POST /store/admin/builds/[id]/review`
  (approve disabled behind the `rotateCert` checkbox when the cert != pinned cert).
- **Refund** an ACTIVE purchase → `POST /store/admin/purchases/[id]/refund`
  (idempotent; shows licenses-revoked count).
- Disk-budget panel: per-publisher bytes-used vs `STORE_PUBLISHER_QUOTA_BYTES`
  (70% / 95% thresholds) + KPI row.

Negative checks worth a minute: probe `/store/apps/mine` with a **buyer** `store`
session → 403 (gate stays "connect as developer"); revoke an API key then reuse it
→ 401; mint a download token, refund the purchase, redeem the token → 4xx.

---

## 5. Deviations / cross-lane notes carried into deploy

- **New routes beyond the backend 18**: `GET /store/apps/mine` (owner listings incl.
  drafts + `?slug=` single owner detail; `requireScope('publish')` doubles as the
  gate's authz probe) and `POST /auth/logout` (clears the httpOnly cookie). Both
  mirror existing `store/route` conventions and are required for the pages to work.
- **Additive, back-compat route edits**: `keys/route.ts` +DELETE (revoke own key);
  `store/assets/route.ts` +GET (list owner imagery — public detail omits assets for
  drafts); `me/earnings` +`?byListing=1` (per-listing breakdown). `auth/challenge`
  + `auth/verify` gained the v2 purpose-scoped branch (server half of the devportal
  login). `store/apps/[slug]/prices` PUT deactivates+recreates (never DELETEs).
- **Nav**: the root nav (`app/layout.tsx`) has an **Apps** link but **no
  "Developers" (`/dev`)** link — developers reach the portal via direct URL or the
  storefront empty-state CTA. Recommend the nav owner add a "Developers" entry as a
  follow-up (kept out of this pass to avoid cross-lane conflict).
- **Slug edge case**: a listing whose slug is literally `mine` would have its public
  API detail shadowed by the static `/store/apps/mine` route (the web page
  `/marketplace/mine` is unaffected). Consider blocklisting `mine` as a slug.
- **Extension**: canonical repo staging path
  `apps/wallet-extension/animica-wallet-extension-chrome.zip` was intentionally left
  as the old byte-identical 0.3.1; the release script rebuilds it. Nothing live was
  changed by the ship-prep — §3 is publish-pending.
