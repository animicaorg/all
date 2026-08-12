// Idempotent bootstrap for the Python Cloud commercial plans (hire-setup.ts pattern).
//
//   set -a; . ./.env.production; set +a
//   npx tsx scripts/subs-setup.ts                 # DRY RUN (default): print the diff, write nothing
//   npx tsx scripts/subs-setup.ts --apply         # actually upsert rows + mint missing PayPal plans
//   npx tsx scripts/subs-setup.ts --apply --recreate-plans
//   npx tsx scripts/subs-setup.ts --apply --webhook https://animica.dev/api/mkt/v1/billing/paypal/webhook
//
// - DRY RUN BY DEFAULT: this script touches the LIVE Plan catalog and the LIVE PayPal REST
//   app. Without --apply it only reads (DB + getPlan probes) and prints every action it
//   WOULD take, so a deploy can review the diff before money-adjacent state moves.
// - Upserts the Plan catalog rows from lib/planConfig PLAN_CATALOG (which mirrors
//   lib/cloud/config CLOUD_PLAN_CATALOG — the single source of truth). Marketing fields are
//   refreshed; limitsJson is NEVER touched — that's the admin's knob; prices are refreshed
//   only together with a fresh PayPal plan, because PayPal plan prices are immutable.
// - DEACTIVATES the retired legacy tiers (starter/operator) so /pricing and /billing/plans
//   stop offering them. Their rows and any live subscriber rows are preserved — retiring a
//   tier must never delete billing history. NOTE: subscribers still on a retired key resolve
//   to free limits (effectivePlan fails isPlanKey); migrate any remaining ones by hand first.
// - Mints one PayPal product + one billing plan per paid, self-serve tier (contact-sales
//   tiers like enterprise NEVER get a PayPal plan — they quote through
//   /api/cloud/v1/enterprise). Minting is skipped when the stored paypalPlanId still
//   resolves via getPlan, unless --recreate-plans.
// - --webhook registers the subscriptions webhook (reusing an existing registration for the
//   same URL) and prints the id to put in .env.production as SUBS_PAYPAL_WEBHOOK_ID
//   (value must be '$'-free — PayPal webhook ids are alphanumeric, so they are).

import { PrismaClient } from '@prisma/client';
import {
  paypalConfigured,
  createProduct,
  createMonthlyPlanCents,
  getPlan,
  createWebhook,
  listWebhooks,
  paypalEnv,
} from '../lib/paypal';
import { PLAN_CATALOG, SUBS_WEBHOOK_EVENTS, centsToUsdString } from '../lib/planConfig';

const prisma = new PrismaClient();

const argv = process.argv.slice(2);
const APPLY = argv.includes('--apply');
const RECREATE = argv.includes('--recreate-plans');
const WEBHOOK_URL = (() => {
  const i = argv.indexOf('--webhook');
  return i >= 0 ? argv[i + 1] : null;
})();

// Tiers replaced by the Python Cloud catalog. 'pro' and 'business' keep their keys (new
// prices, new limits); these two have no successor key and must stop being offered.
const RETIRED_KEYS = ['starter', 'operator'];

const tag = APPLY ? '[subs-setup]' : '[subs-setup:DRY-RUN]';

async function main() {
  console.log(`${tag} PayPal env: ${paypalEnv}, configured: ${paypalConfigured()}${APPLY ? '' : ' — pass --apply to write'}`);

  // 0) Retire legacy tiers.
  for (const key of RETIRED_KEYS) {
    const row = await prisma.plan.findUnique({ where: { key } });
    if (!row) continue;
    if (!row.active) {
      console.log(`${tag} ${key}: already retired`);
      continue;
    }
    const live = await prisma.planSubscription.count({
      where: { planKey: key, status: { in: ['ACTIVE', 'PAST_DUE', 'GRACE_PERIOD', 'SUSPENDED'] } },
    });
    if (live > 0) {
      console.warn(`${tag} ${key}: ${live} live subscription(s) still on this retired tier — they resolve to FREE limits until migrated`);
    }
    if (APPLY) {
      await prisma.plan.update({ where: { key }, data: { active: false } });
      console.log(`${tag} retired plan row: ${key}`);
    } else {
      console.log(`${tag} would retire plan row: ${key}`);
    }
  }

  // 1) Catalog rows.
  for (const spec of PLAN_CATALOG) {
    const existing = await prisma.plan.findUnique({ where: { key: spec.key } });
    if (!existing) {
      if (APPLY) {
        await prisma.plan.create({
          data: {
            key: spec.key,
            name: spec.name,
            tagline: spec.tagline,
            icon: spec.icon,
            features: spec.features,
            priceUsdCents: spec.priceUsdCents,
            featured: spec.featured,
            sortOrder: spec.sortOrder,
          },
        });
        console.log(`${tag} created plan row: ${spec.key} ($${centsToUsdString(spec.priceUsdCents)}/mo)`);
      } else {
        console.log(`${tag} would create plan row: ${spec.key} ($${centsToUsdString(spec.priceUsdCents)}/mo)`);
      }
    } else {
      const priceChanged = existing.priceUsdCents !== spec.priceUsdCents;
      if (priceChanged && existing.paypalPlanId && !RECREATE && !spec.contactSales) {
        console.warn(
          `${tag} ${spec.key}: catalog price ${centsToUsdString(spec.priceUsdCents)} != DB ${centsToUsdString(existing.priceUsdCents)} — PayPal plans are immutable; rerun with --recreate-plans to mint a new plan (existing subscribers keep their old price).`,
        );
      }
      if (APPLY) {
        await prisma.plan.update({
          where: { key: spec.key },
          data: {
            name: spec.name,
            tagline: spec.tagline,
            icon: spec.icon,
            features: spec.features,
            featured: spec.featured,
            sortOrder: spec.sortOrder,
            active: true,
            // Price only moves together with a fresh PayPal plan (or when no plan exists
            // yet). Contact-sales tiers carry no PayPal plan, so their display "from"
            // price always tracks the catalog.
            ...(!existing.paypalPlanId || RECREATE || !priceChanged || spec.contactSales
              ? { priceUsdCents: spec.priceUsdCents }
              : {}),
          },
        });
        console.log(`${tag} refreshed plan row: ${spec.key}`);
      } else {
        console.log(`${tag} would refresh plan row: ${spec.key}${priceChanged ? ` (price ${centsToUsdString(existing.priceUsdCents)} -> ${centsToUsdString(spec.priceUsdCents)})` : ''}`);
      }
    }
  }

  // 2) PayPal billing plans for paid, self-serve tiers. Contact-sales tiers are excluded by
  // design: an enterprise deal has no fixed monthly price to mint.
  const mintable = PLAN_CATALOG.filter((p) => p.priceUsdCents > 0 && !p.contactSales);
  if (!paypalConfigured()) {
    console.warn(`${tag} PayPal not configured — skipping plan provisioning`);
  } else {
    let productId: string | null = null;
    for (const spec of mintable) {
      const row = await prisma.plan.findUnique({ where: { key: spec.key } });
      if (!row && !APPLY) {
        console.log(`${tag} would mint PayPal plan for ${spec.key} ($${centsToUsdString(spec.priceUsdCents)}/mo) after creating its row`);
        continue;
      }
      if (!row) continue;
      let needPlan = RECREATE || !row.paypalPlanId;
      if (!needPlan && row.paypalPlanId) {
        const live = await getPlan(row.paypalPlanId).catch(() => null);
        if (!live || live.status !== 'ACTIVE') {
          console.warn(`${tag} ${spec.key}: stored PayPal plan ${row.paypalPlanId} not usable — ${APPLY ? 'reminting' : 'would remint'}`);
          needPlan = true;
        } else {
          console.log(`${tag} ${spec.key}: PayPal plan OK (${row.paypalPlanId})`);
        }
      }
      if (!needPlan) continue;
      if (!APPLY) {
        console.log(`${tag} would mint PayPal plan for ${spec.key} ($${centsToUsdString(spec.priceUsdCents)}/mo)`);
        continue;
      }
      if (!productId) {
        productId = await createProduct(
          'Animica Python Cloud',
          'Animica Python Cloud plans: serverless Python functions, AI apps and agents on the Animica chain.',
        );
        console.log(`${tag} created product ${productId}`);
      }
      const planId = await createMonthlyPlanCents({
        productId,
        name: `Animica Cloud ${spec.name}`,
        description: `${spec.tagline} — $${centsToUsdString(spec.priceUsdCents)}/month`,
        monthlyCents: spec.priceUsdCents,
      });
      await prisma.plan.update({
        where: { key: spec.key },
        data: { paypalPlanId: planId, priceUsdCents: spec.priceUsdCents },
      });
      console.log(`${tag} ${spec.key}: minted PayPal plan ${planId} ($${centsToUsdString(spec.priceUsdCents)}/mo)`);
    }
  }

  // 3) Webhook registration.
  if (WEBHOOK_URL) {
    if (!APPLY) {
      console.log(`${tag} would register webhook for ${WEBHOOK_URL} (events: ${SUBS_WEBHOOK_EVENTS.length})`);
    } else {
      const hooks = await listWebhooks();
      const existing = hooks.find((h: any) => h?.url === WEBHOOK_URL);
      if (existing) {
        console.log(`${tag} webhook already registered for ${WEBHOOK_URL}`);
        console.log(`${tag} SUBS_PAYPAL_WEBHOOK_ID=${existing.id}`);
      } else {
        const id = await createWebhook(WEBHOOK_URL, [...SUBS_WEBHOOK_EVENTS]);
        console.log(`${tag} registered webhook ${id} for ${WEBHOOK_URL}`);
        console.log(`${tag} add to .env.production:  SUBS_PAYPAL_WEBHOOK_ID=${id}`);
      }
    }
  }

  console.log(`${tag} done`);
}

main()
  .catch((e) => {
    console.error(`${tag} FAILED:`, e);
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
