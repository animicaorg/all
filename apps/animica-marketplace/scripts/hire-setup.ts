/* Seed the Hire Me service catalog and provision live PayPal billing plans (with the one-time
 * setup fee) for subscription services that don't have one yet. Idempotent: services upsert by
 * slug (display fields refresh; an existing paypalPlanId is kept unless --recreate-plans).
 *
 * Run from the app dir with production env:
 *   set -a; . ./.env.production; set +a; npx tsx scripts/hire-setup.ts [--recreate-plans]
 *
 * With --webhook <url>, also registers a PayPal webhook for subscription lifecycle events and
 * prints its id — put that in .env.production as HIRE_PAYPAL_WEBHOOK_ID and restart.
 */
import { PrismaClient } from '@prisma/client';
import {
  createProduct, createMonthlyPlan, getPlan, createWebhook, listWebhooks, paypalConfigured,
} from '../lib/paypal';

const prisma = new PrismaClient();

const SERVICES = [
  {
    slug: 'mining-pool',
    name: 'Custom Mining Pool',
    tagline: 'Your own dedicated Animica pool, branded for your project.',
    description:
      'We’ll deploy and manage a dedicated Animica mining pool for your project — on its own managed Cloud VPS, with a custom branded pool website for your miners.',
    icon: '⛏️',
    kind: 'subscription',
    priceUsdMonthly: 300,
    setupFeeUsd: 500,
    sortOrder: 10,
    features: [
      'Dedicated VPS deployment — Cloud VPS 16: 16 vCPU · 64 GB RAM · 500 GB SSD · 1 Gbit/s · unlimited traffic · 3 snapshots, managed by us',
      'Mining pool installation & configuration',
      'Custom branded pool website (live stats, miner dashboard)',
      'Wallet integration',
      'SSL configuration',
      'Domain/subdomain support',
      'Automatic updates',
      '24/7 monitoring',
      'Monthly maintenance',
      'Email support',
    ],
  },
  {
    slug: 'rpc-node',
    name: 'Managed RPC Endpoint / Full Node',
    tagline: 'A professionally managed, fully synchronized Animica node.',
    description:
      'Receive a professionally managed RPC endpoint and fully synchronized Animica full node — deployed, monitored and kept in sync for you.',
    icon: '📡',
    kind: 'subscription',
    priceUsdMonthly: 100,
    setupFeeUsd: 500,
    sortOrder: 20,
    features: [
      'Dedicated RPC endpoint',
      'Full node deployment on managed infrastructure',
      'Automatic updates',
      'Snapshot syncing',
      '24/7 monitoring',
      'Monthly maintenance',
      'SSL',
      'Email support',
    ],
  },
  {
    slug: 'managed-site',
    name: 'Managed Website',
    tagline: 'Add a professionally managed website to any service — or standalone.',
    description:
      'We build, host and maintain a website for your project on managed infrastructure. We can manage the domain for you end-to-end; using your own custom domain? Open a support ticket and we’ll set up the DNS with you.',
    icon: '🌐',
    kind: 'subscription',
    priceUsdMonthly: 50,
    setupFeeUsd: 0,
    sortOrder: 30,
    features: [
      'Website deployment & hosting on managed infrastructure',
      'Domain managed by us (or support-assisted DNS for custom domains)',
      'SSL certificates',
      'Content & software updates',
      'Monitoring',
      'Monthly maintenance',
      'Email support',
    ],
  },
  {
    slug: 'custom',
    name: 'Custom Service',
    tagline: 'Need something else? Tell us what — we’ll quote it.',
    description:
      'Anything beyond the fixed offerings: bespoke infrastructure and development, priced per project. Describe what you need and we’ll reply with a quote within 24 hours.',
    icon: '🧩',
    kind: 'quote',
    priceUsdMonthly: null as number | null,
    setupFeeUsd: 0,
    sortOrder: 40,
    features: [
      'AI Agent Hosting',
      'GPU Hosting',
      'Validator Hosting',
      'Seed Node Hosting',
      'Custom Development',
      'Consulting',
      'Exchange Integration',
      'Wallet Integration',
      'Enterprise Infrastructure',
    ],
  },
];

const WEBHOOK_EVENTS = [
  'BILLING.SUBSCRIPTION.ACTIVATED',
  'BILLING.SUBSCRIPTION.SUSPENDED',
  'BILLING.SUBSCRIPTION.CANCELLED',
  'BILLING.SUBSCRIPTION.EXPIRED',
  'BILLING.SUBSCRIPTION.PAYMENT.FAILED',
  'PAYMENT.SALE.COMPLETED',
  // Clawbacks: money can leave after provisioning, so the order must stop reading as paid.
  'PAYMENT.SALE.REFUNDED',
  'PAYMENT.SALE.REVERSED',
  'CUSTOMER.DISPUTE.CREATED',
  'CUSTOMER.DISPUTE.UPDATED',
];

async function main() {
  const recreate = process.argv.includes('--recreate-plans');
  const webhookIdx = process.argv.indexOf('--webhook');
  const webhookUrl = webhookIdx >= 0 ? process.argv[webhookIdx + 1] : null;

  for (const s of SERVICES) {
    const existing = await prisma.hireService.findUnique({ where: { slug: s.slug } });
    let paypalPlanId = existing?.paypalPlanId ?? null;

    if (s.kind === 'subscription' && s.priceUsdMonthly != null) {
      if (paypalPlanId && !recreate) {
        // Sanity-check the stored plan still exists & is ACTIVE.
        try {
          const plan = await getPlan(paypalPlanId);
          if (!plan || plan.status !== 'ACTIVE') {
            console.warn(`! plan ${paypalPlanId} for ${s.slug} is ${plan?.status ?? 'missing'} — recreating`);
            paypalPlanId = null;
          }
        } catch (e) {
          console.warn(`! could not verify plan for ${s.slug}: ${(e as Error).message}`);
        }
      } else if (recreate) {
        paypalPlanId = null;
      }
      if (!paypalPlanId) {
        if (!paypalConfigured()) {
          console.warn(`! PayPal not configured — ${s.slug} seeded without a plan (checkout disabled)`);
        } else {
          const productId = await createProduct(`Animica — ${s.name}`, s.tagline);
          paypalPlanId = await createMonthlyPlan({
            productId,
            name: `${s.name} — $${s.priceUsdMonthly}/month`,
            description: `Animica managed service: ${s.name}` +
              (s.setupFeeUsd ? ` ($${s.setupFeeUsd} one-time setup fee)` : ''),
            monthlyUsd: s.priceUsdMonthly,
            setupFeeUsd: s.setupFeeUsd,
          });
          console.log(`✓ PayPal plan for ${s.slug}: ${paypalPlanId} ($${s.priceUsdMonthly}/mo + $${s.setupFeeUsd} setup)`);
        }
      }
    }

    await prisma.hireService.upsert({
      where: { slug: s.slug },
      create: {
        slug: s.slug, name: s.name, tagline: s.tagline, description: s.description,
        features: s.features, icon: s.icon, kind: s.kind,
        priceUsdMonthly: s.priceUsdMonthly, setupFeeUsd: s.setupFeeUsd,
        sortOrder: s.sortOrder, paypalPlanId, active: true,
      },
      update: {
        name: s.name, tagline: s.tagline, description: s.description,
        features: s.features, icon: s.icon, kind: s.kind,
        priceUsdMonthly: s.priceUsdMonthly, setupFeeUsd: s.setupFeeUsd,
        sortOrder: s.sortOrder, paypalPlanId,
      },
    });
    console.log(`✓ service ${s.slug} upserted${paypalPlanId ? ` (plan ${paypalPlanId})` : ''}`);
  }

  if (webhookUrl) {
    const hooks = await listWebhooks();
    const existing = hooks.find((h: any) => h.url === webhookUrl);
    if (existing) {
      console.log(`✓ webhook already registered: ${existing.id} → ${webhookUrl}`);
      console.log(`  HIRE_PAYPAL_WEBHOOK_ID=${existing.id}`);
    } else {
      const id = await createWebhook(webhookUrl, WEBHOOK_EVENTS);
      console.log(`✓ webhook created: ${id} → ${webhookUrl}`);
      console.log(`  Add to .env.production: HIRE_PAYPAL_WEBHOOK_ID=${id} and restart the service.`);
    }
  }
}

main()
  .catch((e) => { console.error(e); process.exit(1); })
  .finally(() => prisma.$disconnect());
