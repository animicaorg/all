# App Store (phase 1+2) — integration runbook

Backend for the Animica app store lives entirely inside this app (`:4950` behind
animica.dev). Code is merged, typechecks (`npx tsc --noEmit`) and builds
(`npm run build`) clean. Nothing below has been applied to the live box by the
implementation lanes — this file is the orchestrator's checklist, in order.

## 1. Environment (.env.production)

Required before anything is armed:

| Var | Value / notes |
|---|---|
| `STORE_TREASURY_ADDRESS` | **Dedicated** anim1… address used ONLY for ANMSTORE1 store payments (the watcher's running-baseline reconciliation assumes no other traffic). Key must exist in `/root/.animica/wallets.json` (set `ANIMICA_WALLET_PASSPHRASE` in the unit env if encrypted) for anchor posting. No default — routes 503 and the watcher exits fail-closed while unset. |
| `STORE_FEE_BPS` | `3000` (70 % creator / 30 % treasury). No default — purchase-intent and refund routes fail closed (503) while unset. |
| `MKT_TREASURY_ADDRESS` | Already set (fee credit target + payout balance guard). |
| `SESSION_SECRET` | Already set. Also signs download tokens unless `STORE_DOWNLOAD_SECRET` is set (recommended: set a distinct `STORE_DOWNLOAD_SECRET` so rotating one doesn't invalidate the other). |
| `MKT_ADMIN_TOKEN` | Already set — `x-admin-token` for `/store/admin/*` (an `ADMIN`-role account session also works). |

Optional knobs (defaults in parentheses): `STORE_APK_MAX_BYTES` (512 MB),
`STORE_PUBLISHER_QUOTA_BYTES` (2 GiB), `MEDIA_STORE_MIN_FREE_BYTES` (20 GB),
`ANDROID_BUILD_TOOLS_DIR` (auto-discovered from `ANDROID_HOME`/`ANDROID_SDK_ROOT`,
newest build-tools first), `APK_VERIFY_TIMEOUT_MS`, `STORE_DOWNLOAD_SECRET`,
`STORE_REQUIRE_SENDER_PROOF` (0 — set 1 to also reject txs first seen
post-inclusion), `STORE_TREASURY_BASELINE_NANM` (pin the watcher's treasury
baseline before the first armed run; otherwise initialized fail-safe as
observed-minus-already-verified), `STORE_GRACE_DAYS` (3),
`STORE_RENEW_AHEAD_HOURS`, `STORE_ANCHOR_MIN_LICENSES`, `STORE_ANCHOR_MAX_BATCH`,
`STORE_TX_NOT_FOUND_GRACE_SECS`, `STORE_WATCHER_BATCH`, `STORE_WORKER_STATE_DIR`
(`var/store-workers`).

Worker arm gates (all default **OFF** = observe-only dry-run):
`STORE_PAYMENTS_ENABLED`, `STORE_ANCHOR_ENABLED`, `STORE_ANCHOR_POST`,
`STORE_RENEWALS_ENABLED`, `PAYOUT_ENABLED`, `DEPOSIT_WATCHER_ENABLED`.

## 2. Database migration (additive-only)

`prisma/store-migration.sql` — re-audited 2026-07-19, purely additive:
4 new enums; 2 new `ListingType` values (`APP`, `DIGITAL_GOOD`); nullable (or
constant-default `Purchase.autoRenew=false`) columns added to `Listing` and
`Purchase`; 7 new tables; indexes only on new tables/columns
(`Listing_packageName_key` is unique over a fresh all-NULL column — safe); FKs
only on new columns. No DROP / ALTER COLUMN / data rewrite.

```bash
cd /root/animica/apps/animica-marketplace
npx prisma db execute --file prisma/store-migration.sql --schema prisma/schema.prisma
npx prisma generate   # client already generated; harmless to re-run
```

Note: the two `ALTER TYPE … ADD VALUE` statements must not run in one
transaction with statements that USE the new values — this file doesn't, so both
`prisma db execute` and plain `psql -f` (autocommit) are fine on our Postgres.

Verify: `SELECT count(*) FROM "AppBuild";` → `0`.

## 3. App restart + nginx

1. Rebuild + restart the marketplace service (the running process predates these
   routes): `npm run build` then restart `animica-marketplace` however it is
   supervised.
2. nginx: copy the `location ^~ /api/mkt/v1/store/` block from
   `deploy/animica.dev-marketplace.nginx.conf` (lines ~88–107) into the live
   animica.dev server block **before** the existing `^~ /api/mkt/` location.
   It carries `client_max_body_size 512m`, `proxy_request_buffering off`,
   `proxy_buffering off`, 600 s timeouts, and **no sub_filter** (download bytes
   must match the catalog sha3). Then `nginx -t && systemctl reload nginx`.

## 4. Workers — install then arm, in this order

Install all five (templates in `deploy/systemd/`, headers repeat this):

```bash
cp deploy/systemd/animica-store-*.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now animica-store-payment-watcher.timer \
  animica-store-license-anchor.timer animica-store-renewals.timer \
  animica-store-payouts.timer animica-store-deposits.timer
```

All gates default OFF via `Environment=` in the units; `.env.production`
(EnvironmentFile) overrides to arm. Watch one observe-only cycle of each
(`journalctl -u animica-store-payment-watcher.service -f`) before arming.

Arm one at a time, verifying logs between steps:

1. Pin `STORE_TREASURY_BASELINE_NANM` **before the first store payment ever
   arrives** (0 for a fresh dedicated address). NOT optional in practice: the
   fail-safe auto-init uses the observed balance, so any payment that landed
   before the first armed run gets counted INTO the baseline and the watcher
   halts with a false shortfall (hit during the 2026-07-19 acceptance test).
   Remedy if it happens: correct `treasuryBaselineNanm` in
   `var/store-workers/store-payment-watcher.json`, then one `STORE_RESET_HALT=1`
   run.
2. `STORE_PAYMENTS_ENABLED=1` — payment watcher starts crediting (12-conf
   finality + sender balance-delta proof + treasury baseline reconciliation;
   a reconciliation shortfall HALTs crediting stickily — clear only with a
   one-shot `STORE_RESET_HALT=1` run after investigating).
3. `STORE_ANCHOR_ENABLED=1` — anchor worker batches licenses + computes proofs
   (no chain spend yet).
4. `STORE_ANCHOR_POST=1` — posts ANMLIC1 anchor txs (1 nANM + fee) via
   `scripts/post_anchor_tx.py`; needs the treasury key in wallets.json.
5. `STORE_RENEWALS_ENABLED=1` — subscription renewals + 3-day grace dunning.
6. `PAYOUT_ENABLED=1`, `DEPOSIT_WATCHER_ENABLED=1` — creator withdrawals /
   deposit crediting (existing config gates; caps `PAYOUT_MAX_PER_TX_ANM`,
   `PAYOUT_MAX_PER_DAY_ANM` apply).

## 5. Smoke test (after §2–§3, before arming money gates)

`AUTH="Authorization: Bearer anm_mkt_…"` — a key with `publish`+`buy` scopes
(or a purpose-scoped session cookie). `B=https://animica.dev/api/mkt/v1`.

```bash
# 0. health + public catalog (no auth) — 200, empty page initially
curl -s $B/health
curl -s "$B/store/apps?type=APP&sort=new"

# 1. create APP listing (owner) — 200 {listing…}; slug + unique packageName
curl -s -X POST $B/store/apps -H "$AUTH" -H 'content-type: application/json' \
  -d '{"name":"Demo","slug":"demo-app","category":"TOOLS","packageName":"org.example.demo","description":"demo"}'

# 2. upload APK (raw body; through nginx to prove the 512m block works) —
#    a debug-signed APK MUST come back CHECKS_FAILED (fail-closed cert policy);
#    a release-signed one lands PENDING_REVIEW (first build is never auto-approved)
curl -s -X POST $B/store/apps/demo-app/builds -H "$AUTH" \
  -H 'content-type: application/vnd.android.package-archive' \
  -H 'x-anm-channel: stable' --data-binary @app-release.apk

# 3. admin review queue + approve — 200; then PATCH status PUBLISHED
curl -s $B/store/admin/builds -H "x-admin-token: $MKT_ADMIN_TOKEN"
curl -s -X POST $B/store/admin/builds/<buildId>/review \
  -H "x-admin-token: $MKT_ADMIN_TOKEN" -H 'content-type: application/json' \
  -d '{"action":"approve"}'   # actions: approve | reject | delist; cert rotation needs {"rotateCert":true}
curl -s -X PATCH $B/store/apps/demo-app -H "$AUTH" \
  -H 'content-type: application/json' -d '{"status":"PUBLISHED"}'

# 4. update-check endpoints (public) — listing now visible with latest build
curl -s $B/store/apps/demo-app/versions/latest

# 5. purchase intent (needs STORE_FEE_BPS + STORE_TREASURY_ADDRESS set; ONE_TIME
#    price on the listing) — 200 {payTo, amountNanm, memoHex, expiresAt, split}
curl -s -X POST $B/store/purchases/intent -H "$AUTH" \
  -H 'content-type: application/json' -d '{"slug":"demo-app","priceId":"<priceId>"}'

# 6. pay on-chain from a wallet: value=amountNanm, data=memoHex, to=payTo; then
curl -s -X POST $B/store/purchases/<purchaseId>/submit -H "$AUTH" \
  -H 'content-type: application/json' -d '{"txid":"<txid>"}'
# poll until the ARMED watcher flips it (12 confs ≈ minutes):
curl -s $B/store/purchases/<purchaseId> -H "$AUTH"   # → status ACTIVE + license

# 7. entitled download — token mint then fetch; bytes must hash to the catalog sha3
curl -s -X POST $B/store/apps/demo-app/download-token -H "$AUTH"   # → {token,url,sha3,…}
curl -s -o got.apk "https://animica.dev/api/mkt/v1/store/download/<token>"
python3 -c "import hashlib;print(hashlib.sha3_256(open('got.apk','rb').read()).hexdigest())"  # == sha3 above

# 8. license + Merkle proof verification (public)
curl -s $B/store/licenses -H "$AUTH"
curl -s -X POST $B/store/licenses/verify -H 'content-type: application/json' \
  -d '{"licenseId":"<licenseId>"}'
```

Negative checks worth one minute: repeat step 6 with the same txid (idempotent,
no double-credit); let an intent pass 30 min then submit (410, watcher never
credits expired intents); mint a download token, refund via
`POST $B/store/admin/purchases/<id>/refund`, redemption must now 4xx.

## 6. Known integration notes (from lane reports)

- Sender balance-delta proof uses the watcher's own pre-inclusion baseline (the
  node has no historical-state RPC); treasury baseline reconciliation is the
  hard value-conservation gate either way.
- `GET /store/licenses` (not `/store/licenses/mine`) lists the caller's
  licenses; design's `/licenses/[id]/proof`, `/anchors`, `/revocations` and
  `GET /store/subscriptions` list are not built in this phase.
- The custodial `POST /api/mkt/v1/purchases` route was NOT modified for the
  STORE_FEE_BPS override / subscription consent — `settlePurchaseFromBalance`
  already accepts `feeBps`, so it's a follow-up one-liner if custodial APP
  purchases should split 70/30 too.
- Refunds recompute the split at the *current* `STORE_FEE_BPS` — don't change
  the bps while refunds for old sales are possible.
- `builds/` dir is created lazily on first upload; completed build files are
  named by AppBuild id and never swept (only `*.part-*`/`incoming-*` >1 h).
