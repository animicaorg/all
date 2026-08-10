# Store tab + PayPal Buy — implementation notes

The Android/iOS wallet gained a **Store** tab (App Store / digital-goods
marketplace) and a **Buy with PayPal** on-ramp screen. Backend lives on
`animica.dev`. This doc covers what shipped, the auth + payment flows, and the
known gaps.

## What shipped

### Navigation
- `lib/router.dart` — a 6th `StatefulShellBranch` at **`/store`** (storefront
  icon), inserted before Settings. The original five tabs (Wallet, Tokens,
  NFTs, Browser, Settings) are unchanged. Push routes:
  - `/store/app/:slug` → app detail
  - `/store/library` → my purchased apps / licenses
  - `/buy` → Buy-with-PayPal (also reachable from the Wallet tab)

### Screens (`lib/screens/store/`, `lib/screens/buy.dart`)
- `store_home.dart` — catalog grid + category chips (All / Games / AI Agents /
  Tools / Developer / Mobile / Web).
- `store_app_detail.dart` — screenshots, description, permissions expander,
  version/size/minSDK, reviews (stub — see gaps), pinned **Buy** bar.
- `store_checkout_sheet.dart` — intent → price + 70/30 split + payTo →
  slide-to-confirm → build/sign/broadcast the transfer → submit txid → poll to
  ACTIVE → license.
- `store_library.dart` — "My Apps" from `GET /store/licenses` with
  Active/Pending/Expired/Revoked chips + on-chain anchor badge.
- `buy.dart` — PayPal on-ramp (presets $25/$50/$100/$500 + custom), dormant-safe.

### Services / models / state
- `lib/services/marketplace_api.dart` — REST client for `https://animica.dev/api/mkt/v1`.
  Public reads (`catalog`, `appDetail`, `verifyLicense`) + session-gated buyer
  routes (`createPurchaseIntent`, `submitPurchaseTxid`, `purchaseStatus`,
  `myLicenses`, `requestDownloadToken`). Typed `MarketplaceApiException` over the
  server `{error:{code,message}}` envelope (`isUnavailable`=503, `isUnauthorized`,
  `isNotFound`, `isExpired`…).
- `lib/services/license_store.dart` — encrypted offline license cache
  (`animica.wallet.licenses.v1`) + offline Merkle-proof verification; server
  `POST /licenses/verify` stays authoritative for revocation.
- `lib/models/store.dart` — tolerant `fromJson` models (StoreApp, StoreAppDetail,
  StorePrice, StoreBuild, StoreAsset, StoreReview, PurchaseIntent, PurchaseSplit,
  SubmitResult, PurchaseRecord, PaymentIntentStatus, PurchaseStatusResult,
  License, LicenseAnchor, LicenseVerifyResult, DownloadToken).
- `lib/state/store_state.dart` — Riverpod providers (`marketplaceApiProvider`
  bound to the active account + unlock key; catalog / detail / licenses).
- `lib/services/signer.dart` — `buildTransferBody` gained an optional
  `Uint8List? data` param (see Payment).

### Config (`lib/constants.dart`)
- `storeApiUrl = https://animica.dev/api/mkt/v1`
- `onrampApiUrl = https://animica.dev/api/onramp`

## Auth flow (store session)

Buyer routes use a **challenge → sign → session-cookie** flow (mirrors the web
`animica_signMessage` connect). Bearer API keys are NOT used by the wallet.

1. `GET /api/mkt/v1/auth/challenge?address=<anim1…>&purpose=store` → `{ challenge }`.
   `purpose=store` mints a purpose-scoped v2 session (scopes: read, buy, use).
2. Sign `UTF8("animica:signMessage:" + challenge)` with the active account's
   **ML-DSA-65** key in **pure mode** (`MlDsa65.sign` of the raw bytes — NOT the
   tx canonical/prehash pipeline). The domain prefix is hard-coded in the client,
   not trusted from the server echo. Non-`ml_dsa_65` (0x1003) accounts are
   refused with a clear error.
3. `POST /api/mkt/v1/auth/verify { address, challenge, signature(0x hex),
   publicKey(0x hex), purpose:"store" }` → server sets an **httpOnly
   `anm_mkt_session` cookie**.
4. The client captures that cookie from `Set-Cookie` and caches it, **bound to
   the signing address**, in secure storage under
   `animica.wallet.store.session.v1`, AES-GCM-encrypted with the wallet unlock
   key (same layering `Vault` uses; `vault.dart`'s own storage format is
   untouched). Buyer requests send `Cookie: anm_mkt_session=<value>`; a 401
   triggers exactly one silent re-challenge before failing. Switching accounts
   re-challenges.

> Deviation from the brief: the session is a **cookie**, not a JSON bearer
> token, because that is what the backend actually returns. "Store the session
> token in the vault" is implemented as caching this cookie.

## Payment flow (on-chain purchase)

A purchase is a plain **kind=0 transfer** carrying the purchase memo:

- `value = amountNanm`, `to = payTo`, `data = memoHex` bytes (an `ANMSTORE1`
  JSON memo) — all from `POST /store/purchases/intent`.
- `signer.dart::buildTransferBody` threads the memo through a new optional
  `data` param (`kMaxTransferDataBytes = 1024` guard). **When `data` is omitted
  the body is byte-for-byte identical to the historical empty-data transfer**, so
  existing sends and every golden vector are unchanged.
- Byte-parity with the chain is proven in `test/canonical_test.dart`
  ("… WITH data"): the embedded golden hex was produced by the Python reference
  `omni_sdk.tx.build.make_tx(data=memo)` + `animica.tx.signing.build_signable_tx_bytes`
  and re-verified independently (MATCH). The `data` field name/position/CBOR-bstr
  encoding is identical to the Python builder.
- After broadcast: `POST /store/purchases/{id}/submit { txid }`, then poll
  `GET /store/purchases/{id}` PENDING → CONFIRMING → ACTIVE → license.

The wallet only enables **Buy** for a paid `ONE_TIME` price — the backend
restricts wallet-signed intents to one-time purchases. `FREE` and
`SUBSCRIPTION` prices show an honest disabled state (those are custodial /
web-store flows today).

## Buy with PayPal (on-ramp)

Base `https://animica.dev/api/onramp` (nginx strips the `/api` prefix →
Fastify `/paypal/...` routes on the desk). **These routes are DORMANT until the
operator arms the rail.** The screen is written to degrade gracefully.

- `POST /api/onramp/paypal/orders { usdAmount, animicaAddress }` →
  `{ order: { orderId, approveUrl, expectedAnm, quoteExpiresAt, status, … } }`
  (the desk wraps everything in an `order` envelope — the flat shape in the brief
  is simplified; the code reads `body['order']`).
- Open `approveUrl` externally via `url_launcher` (same pattern the old buy
  screen used).
- On resume, `POST /api/onramp/paypal/orders/{id}/capture-return { animicaAddress }`
  then poll `GET /api/onramp/paypal/orders/{id}?address=…` until `COMPLETE`;
  status ladder AWAITING_PAYMENT → PAID → DELIVERING → SENT → COMPLETE
  (+ FAILED / REFUND_NEEDED / MANUAL_REVIEW). COMPLETE shows delivered ANM + an
  explorer tx link and refreshes the balance.
- **Dormant behavior:** a `404` / `503` / unreachable `/api/onramp` renders a
  friendly **"Buy with PayPal is almost ready"** card instead of an error.
  Quote-expiry (`410`) offers a clear "New order" retry. There is no standalone
  quote endpoint, so `expectedAnm` is previewed from the order-create response
  (order is created on **Continue**).

Presets: **$25 / $50 / $100 / $500** plus a custom amount field.

## Known gaps / follow-ups

- **Reviews UI is a stub.** The detail screen renders the reviews list the API
  returns but there is no write/submit-a-review UI yet.
- **Subscriptions & free/usage pricing are wallet-disabled.** Only paid
  `ONE_TIME` purchases can be signed from the wallet today; `SUBSCRIPTION` /
  `FREE` are custodial/web flows. Wallet-side subscription support is a later
  milestone.
- **Offline license verification** checks integrity + Merkle inclusion against
  the anchor root the server returns; it does **not** independently fetch the
  `ANMLIC1` anchor tx over RPC (that would couple to the protected `rpc.dart`).
  Server `POST /licenses/verify` remains authoritative for revocation.
- **PayPal rail is dormant** until the operator arms it (see above).

## Verifying (read-only, no build)

```
cd apps/wallet-mobile-flutter
PATH=/opt/flutter/bin:$PATH flutter analyze     # only pre-existing lints remain
PATH=/opt/flutter/bin:$PATH flutter test        # green except the known keys_test SPHINCS case
PATH=/opt/flutter/bin:$PATH dart run tool/live_store_smoke.dart   # live catalog + e2e-test-pack parse check
```

`tool/live_store_smoke.dart` GETs the real catalog and the `e2e-test-pack`
acceptance listing from production and drives the exact model parsers the client
uses (`StoreApp.listFrom(j['apps'])`, `StoreAppDetail.fromJson(j['app'])`) —
read-only, no auth, no writes.
