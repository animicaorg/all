# Game Lab — Playable games + PWA "Add App" (marketplace × Forge × wallet)

Makes published **Game Lab** games actually **playable by end users** and **installable as
PWAs** ("Add App" home-screen install, no App Store). Fully additive to the existing
store/purchase/generation flows — **no DB migration, no new columns** (the play page and the
per-game web manifest are derived entirely from existing `Listing` + `StoreAsset` fields).

A published game is a `DIGITAL_GOOD` store listing under `AppCategory = GAMES` whose play
bundle is a single self-contained, sandboxed HTML document at `Listing.bundleCid`, served:
- **FREE** via `GET /api/mkt/v1/content/[cid]` (public, immutable, sandbox CSP), and
- **PAID** via `POST /api/mkt/v1/store/play-token/[slug]` → `GET /api/mkt/v1/store/play/[token]`
  (entitlement checked at mint **and** at serve; the public CID route is never used for paid bytes).

---

## Three play surfaces

1. **Standalone hosted PLAY page (marketplace)** — a real URL a browser/PWA opens:
   `https://animica.dev/play/[slug]`. Chrome-minimal full-viewport shell that plays the same
   bundle the detail-page panel does, and links a **per-game Web App Manifest** so mobile users
   "Add to Home Screen" and launch it `display: standalone`.
   - `app/play/[slug]/page.tsx` — server component, gate = published web game only
     (`DIGITAL_GOOD` + `GAMES` + `bundleCid`, not `DRAFT`/`DELISTED`), else `notFound()`. Emits
     `<link rel="manifest">`, `apple-mobile-web-app-*`, `apple-touch-icon` (only when a raster
     `StoreAsset ICON` exists), `theme-color`, `viewport-fit=cover`, `robots noindex`.
   - `app/play/[slug]/PlayStandalone.tsx` — `'use client'` shell. FREE → iframe of the content
     route; PAID → auto-mints a play-token (401 → "Connect wallet", 403 → "Buy to play"). iframe
     is `sandbox="allow-scripts"`, `referrerPolicy="no-referrer"`.
   - `app/play/[slug]/manifest.webmanifest/route.ts` — dynamic Web App Manifest (route handler),
     `{id, name, short_name, start_url:/play/[slug], scope:/play/[slug], display:standalone,
     theme_color, background_color, categories:[games], icons}`. Icons from `StoreAsset ICON`
     (192/512 + maskable → Chrome installability) or a self-contained default SVG data URI.
   - `lib/playManifest.ts` — shared helpers (`PLAY_BG`/`PLAY_THEME = #07080c`, `rasterIcon()`,
     `shortName()`, default SVG icon data URI) so the page head and the manifest agree.

2. **Storefront + Forge play links** — every publish surface links the hosted play page:
   - `components/InstallCta.tsx` (marketplace) — new optional `playHref`; renders "▶ Play" +
     an "Add App" hint above the wallet CTA (game listings only; APP/APK listings unchanged).
   - `app/marketplace/apps/[slug]/page.tsx` — passes `playHref={isGameBundle ? /play/[slug] : undefined}`.
   - `/root/animica-forge/lib/store/publish.ts` — `PublishResult.playUrl = ${STORE_BASE_URL}/play/[slug]`.
   - `/root/animica-forge/components/game-lab/PublishToStore.tsx` — "Play now" primary CTA +
     "Add to your device (iOS / Android)" hint on the publish-success screen.

3. **Wallet Store-tab in-app play (Flutter)** — games play inside the wallet via an in-app
   WebView (`flutter_inappwebview`, already a dependency):
   - `lib/screens/store/game_play_screen.dart` (NEW) — full-screen player. `resolveGamePlayUrl()`
     centralizes FREE (public content) vs PAID (mint play-token) resolution; `playStoreGame()`
     launches it. No `window.animica` injection (game HTML carries its own sandbox CSP).
   - `lib/services/marketplace_api.dart` — `gameBundle(slug)`, `playToken(slug)`, `freePlayUrl(slug)`.
   - `lib/models/store.dart` — `GameBundle`, `PlayToken`, plus `StoreAppDetail` game getters.
   - `lib/state/store_state.dart` — `gameBundleProvider` (autoDispose family, cached).
   - `lib/screens/store/store_app_detail.dart` — Play for free games and paid+owned; Buy otherwise.
   - `lib/screens/store/store_library.dart` — Play button on owned game licenses that carry a bundle.

---

## Build / verification status (this pass)

Verified **without disturbing any live service** — Next builds ran into an alternate `distDir`
(`NEXT_VERIFY_DIST_DIR=.next-verify`, a temporary `next.config.mjs` line added then reverted; the
live `.next` was never touched, both verify dirs deleted afterward). No fixes were required —
every app already compiled/typechecked/analyzed clean.

| App | Check | Result |
|-----|-------|--------|
| **Marketplace** | `npx tsc --noEmit` | exit 0 |
| **Marketplace** | `next build` (alt distDir) | exit 0 — routes registered: `ƒ /play/[slug]`, `ƒ /play/[slug]/manifest.webmanifest` |
| **Forge** | `npx tsc --noEmit` | exit 0 |
| **Forge** | `next build` (alt distDir) | exit 0 — `/game-lab` built |
| **Wallet** | `flutter analyze` (7 lane files) | **No issues found** |
| **Wallet** | `flutter analyze` (full project) | 19 issues — all pre-existing baseline in unrelated files; **zero** in Game Lab play files |
| **Wallet** | `flutter test` | 34 pass / 1 fail — the single fail is the known pre-existing `keys_test.dart` SPHINCS case; no regression |

`rpc.dart` / `address.dart` / vault format untouched; no wallet version bump.

---

## Deploy order (operator / orchestrator)

Deploy **marketplace first** so the `/play/[slug]` page + manifest exist before the links that
point at them go live (Forge and the storefront use absolute/relative `/play/[slug]` links that
404 until the route is deployed).

1. **Marketplace** (`/root/animica/apps/animica-marketplace`, `:4950`, animica.dev)
   - `npm run build` (`prisma generate && next build` → writes `.next`).
   - **Restart required** (the running `next start` must reload `.next`). Orchestrator restart.
   - Smoke: `GET https://animica.dev/play/<a-published-game-slug>` → 200 (chrome-minimal shell);
     `GET https://animica.dev/play/<slug>/manifest.webmanifest` → 200,
     `content-type: application/manifest+json`; `GET /play/<non-game-or-draft-slug>` → 404.

2. **Forge** (`/root/animica-forge`, `:4700`, animica.io)
   - `npm run build` (`prisma generate && next build`, `output: standalone`).
   - **Restart required.** Orchestrator restart.
   - Smoke: publish a Game Lab game → success screen shows **Play now** → opens
     `https://animica.dev/play/<slug>`.

3. **Wallet** (`/root/animica/apps/wallet-mobile-flutter`, Flutter)
   - Bump `pubspec.yaml` version to **0.2.1** (currently the published Store-tab is 0.2.0).
   - `flutter build appbundle --release` (+ iOS if shipping there); publish per the wallet release
     pipeline. **Keep** the pre-existing SPHINCS `keys_test` failure (do not "fix").
   - Smoke: Store tab → free game → **Play** in-app WebView; buy a paid game → **Play** unlocks
     (checkout invalidates `myLicensesProvider`); Library → owned game shows Play.

No `.env`, nginx, or schema changes. No `X-Frame-Options` change needed — the play iframe/WebView
is same-origin (marketplace already sends `SAMEORIGIN`, same as the existing detail-page panel).

---

## PWA "Add App" (Add-to-Home-Screen) UX

The operator-specced iOS "Add App" model: no App Store — the browser installs the play page as a
PWA to the home screen and it launches standalone (its own window, no browser chrome).

**iOS / iPadOS (Safari):** open `https://animica.dev/play/[slug]` → Share (□↑) →
**Add to Home Screen** → Add. The icon uses the game's `apple-touch-icon` (only emitted when the
listing has a raster `StoreAsset ICON`; otherwise iOS uses a page snapshot). Launching from the
home screen opens full-screen (`apple-mobile-web-app-capable`, black-translucent status bar). The
per-game manifest gives it its own name/`short_name`.

**Android (Chrome):** open the play page → menu (⋮) → **Install app / Add to Home screen** (Chrome
offers this automatically because the manifest is valid: name, icons ≥192 & 512 incl. maskable,
`start_url`, `display: standalone`). Launches standalone with the game's icon and `theme_color`.

**In-app hints** point users at this: the storefront `InstallCta` ("Add App — Share → Add to Home
Screen") and Forge's publish-success screen ("Add to your device (iOS / Android)").

The standalone launch opens `start_url = /play/[slug]` fresh; the page hides the marketplace
nav/footer from first paint, so the installed app looks like a native game, not a web page.

---

## What plays FREE vs PAID

| Case | Standalone play page | Wallet in-app |
|------|----------------------|---------------|
| **Free game** (price `FREE` / none) | iframe of `/api/mkt/v1/content/[bundleCid]`, no sign-in | WebView of the public content URL, no account |
| **Paid game, owned** | auto-mints play-token → iframe `/store/play/[token]` | mint play-token → WebView; Play shown when a valid license exists |
| **Paid game, not signed in** | 401 → "Connect wallet" CTA (`/my-ai`) | "Sign in to the Store to play" |
| **Paid game, signed in, not owned** | 403 → "Buy to play" CTA → `/marketplace/apps/[slug]` | "You need to buy this game" / Buy button |

Entitlement is enforced server-side (token minted only for owners, re-checked at serve). Paid bytes
never flow through the public CID route.

---

## Follow-ups (not blocking; explicitly out of scope this phase)

- **Service worker / offline launch:** intentionally skipped. iOS Add-to-Home never needed one and
  modern Chrome installs from a valid manifest without a fetch-handler SW. A cache-first SW at
  `/play/[slug]` would enable offline play of the (self-contained) bundle — additive later.
- **Default icon on iOS:** the SVG default icon is Android-manifest-only (iOS renders SVG touch
  icons unreliably → snapshot fallback). A generated raster default (192/512 PNG data URI) would
  give icon-less games a real iOS home-screen icon.
- **Wallet deep link:** Forge/storefront "Open in wallet" still targets the wallet root
  (`WALLET_BASE_URL`); a standardized `animica://store/apps/[slug]` intent filter would deep-link
  into the Store-tab detail. The `InstallCta` wallet deep link is still a placeholder scheme.
- **`generateMetadata` manifest URL** resolves against `metadataBase` (`https://animica.dev`) → the
  absolute prod origin (cosmetic in localhost dev, prod-correct).
- **Cross-origin publish (Forge → store):** unchanged pre-existing requirement — store must allow
  `Access-Control-Allow-Origin: https://animica.io` + credentials on the authed publish routes and
  set the session cookie `SameSite=None; Secure`, else the in-page publish falls back to the Dev
  Center. Independent of this play/PWA work.
