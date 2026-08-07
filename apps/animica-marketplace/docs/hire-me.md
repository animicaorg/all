# Hire Me — managed services (2026-07-26)

Storefront `/hire`, customer dashboard `/dashboard`, operator console `/admin/hire`.
Subdomains: `admin.animica.dev` → `/admin/hire`, `dashboard.animica.dev` → `/dashboard`
(nginx: `/etc/nginx/sites-enabled/hire.animica.dev.conf`).

## Remaining manual step

Add DNS A records `admin.animica.dev` and `dashboard.animica.dev` → `144.126.133.21`, then:

    certbot --nginx -d admin.animica.dev -d dashboard.animica.dev

Until then those hostnames redirect :80 → :443 and present the animica.dev certificate (name
mismatch warning). The apex paths work today and are unaffected.

## Catalog (data-driven)

Services live in the `HireService` table; `/hire` renders whatever is `active`. Add or edit them
from the admin Services tab — no code change, no redeploy. Ticking "Create a new PayPal billing
plan" mints a live plan (monthly price + one-time setup fee) via the REST API.

Seeded: `mining-pool` $300/mo +$500 setup · `rpc-node` $100/mo +$500 setup ·
`managed-site` $50/mo · `custom` (quote only, no PayPal).

Changing the price of a service that already has a plan is refused unless a new plan is minted —
a PayPal plan's prices are immutable, so otherwise the page would advertise one amount and charge
another. Existing subscribers always keep the plan they signed up on.

## Payment integrity

`/api/mkt/v1/hire/orders/confirm` never trusts the browser. It re-fetches the subscription from
PayPal and requires: plan id == the order's plan, `custom_id` == the order id, amounts equal to
the quote (`assertSubscriptionMoney` rejects PayPal's inline plan-override), and status ACTIVE
(APPROVED waits for the webhook; APPROVAL_PENDING is rejected — the buyer never approved it).
Only an ACTIVE subscription enters the operator work queue.

The webhook (`/api/mkt/v1/hire/paypal/webhook`, id `36Y51799SG248750N`) verifies signatures via
PayPal's API and fails closed without `HIRE_PAYPAL_WEBHOOK_ID`. It resolves orders by our own
stored subscription id, or by `custom_id` only after re-proving the subscription belongs to that
order. Cancelled orders are never resurrected by replayed events; refunds, reversals and disputes
flip the order back to `failed` and email the operator.

## Bookkeeping

Each order has manually-entered `serverStartAt` / `serverEndAt` / `serverLabel` (admin → View
Details → Save server dates). The **Renewals** tab lists them soonest-first with a
"renews in N days" / "EXPIRED" pill. **Failed Payments** covers `failed` + `suspended`.

## Support tickets

Customers open and reply to tickets on the dashboard; the operator answers from the admin Tickets
tab. Every message emails the other party (`lib/hireMail.ts`, nodemailer, fail-soft).

## Env (.env.production)

`SMTP_HOST/PORT/USER/PASS`, `MAIL_FROM`, `HIRE_NOTIFY_EMAIL`, `HIRE_ADMIN_USER`,
`HIRE_ADMIN_PWHASH`, `HIRE_COOKIE_DOMAIN`, `HIRE_PAYPAL_WEBHOOK_ID`, plus the existing `PAYPAL_*`.

**Values must not contain `$`.** The file is parsed twice — systemd's EnvironmentFile parser and
Next.js's dotenv-expand — and a `$` is destroyed by both (`scrypt$16384$…` arrived as
`scrypt6384`). Password hashes therefore use `scrypt:N:r:p:salt:hash`. To rotate the operator
password:

    node -e 'const{randomBytes,scryptSync}=require("crypto");const s=randomBytes(16);
    console.log(`scrypt:16384:8:1:${s.toString("hex")}:${scryptSync(process.env.HP,s,32,{N:16384,r:8,p:1}).toString("hex")}`)' \
      HP='<new password>'

Paste into `HIRE_ADMIN_PWHASH` and `systemctl restart animica-marketplace`. Rotation also revokes
every outstanding admin session (the cookie's HMAC scope is bound to the hash).

## Deploy

Never `next build` in place under the running service:

    cp -a .next .next.bak && systemctl stop animica-marketplace
    npm run build && rm -rf .next.bak || { rm -rf .next && mv .next.bak .next; }
    systemctl start animica-marketplace
    curl -s http://127.0.0.1:4950/api/mkt/v1/health

Schema changes are additive SQL: `prisma migrate diff … --script > prisma/hire-migration-N.sql`,
audit, then `npx prisma db execute --file … --schema prisma/schema.prisma`.
