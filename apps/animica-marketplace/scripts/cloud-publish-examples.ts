// Publish the shipped example functions as real, browsable Animica Python Cloud apps.
//
//   npx tsx scripts/cloud-publish-examples.ts [--dry-run] [--unpublish]
//
// These are NOT demo fixtures: each app wraps a function that is genuinely deployed and
// executable through the public endpoint, owned by the Animica Foundation address, and priced
// with the same metered economics as any third-party app. They exist so the marketplace, the
// indexable /apps/{slug} pages and the sitemap have real content from day one — and so a new
// developer can read working source before writing their own.
//
// Idempotent: re-running updates the rows in place and never duplicates.

import { prisma } from '../lib/db';

const dryRun = process.argv.includes('--dry-run');
const unpublish = process.argv.includes('--unpublish');

interface Spec {
  fnSlug: string;
  slug: string;
  name: string;
  tagline: string;
  category:
    | 'AI'
    | 'AGENTS'
    | 'DEVELOPER_TOOLS'
    | 'AUTOMATION'
    | 'DATA'
    | 'GAMES'
    | 'PRODUCTIVITY'
    | 'BLOCKCHAIN'
    | 'UTILITIES'
    | 'APIS';
  icon: string;
  description: string;
  tags: string[];
}

const SPECS: Spec[] = [
  {
    fnSlug: 'hello-api',
    slug: 'hello-api',
    name: 'Hello API',
    tagline: 'The smallest possible Animica Python Cloud function.',
    category: 'APIS',
    icon: '👋',
    description:
      'A minimal request/response function that shows the runtime ABI: define `def main(request)`, return anything JSON-serializable, and Animica handles the endpoint, metering and payment. Read the source, copy it, ship your own.',
    tags: ['starter', 'example', 'api'],
  },
  {
    fnSlug: 'ai-summarizer',
    slug: 'ai-summarizer',
    name: 'AI Summarizer',
    tagline: 'Summarize text with Animica AI — no API keys, no AI infrastructure.',
    category: 'AI',
    icon: '🧠',
    description:
      'Calls animica.ai.infer() from inside the sandbox under the AI_INFERENCE capability. Inference is metered per token and billed to the caller, so the developer never manages or pays for AI infrastructure.',
    tags: ['ai', 'nlp', 'summarize'],
  },
  {
    fnSlug: 'chain-pulse',
    slug: 'chain-pulse',
    name: 'Chain Pulse',
    tagline: 'Live Animica chain health, computed on demand.',
    category: 'BLOCKCHAIN',
    icon: '⛓️',
    description:
      'Reads the live Animica chain through the READ_CHAIN capability and reports the tip height plus real block-interval statistics. A worked example of a function that talks to the network without ever touching a key.',
    tags: ['chain', 'analytics', 'blockchain'],
  },
  {
    fnSlug: 'anm-toolkit',
    slug: 'anm-toolkit',
    name: 'ANM Toolkit',
    tagline: 'Address, balance and unit helpers for ANM.',
    category: 'DEVELOPER_TOOLS',
    icon: '🧰',
    description:
      'Utility endpoints for working with Animica: validate a bech32m address, convert between ANM and nANM exactly (integer math, never floats), and look up a live balance.',
    tags: ['tools', 'anm', 'utilities'],
  },
  {
    fnSlug: 'agent-calls-app',
    slug: 'agent-calls-app',
    name: 'Agent Calls App',
    tagline: 'One function paying another — the agent-to-agent economy.',
    category: 'AGENTS',
    icon: '🤝',
    description:
      'Demonstrates animica.call(): a function invokes another deployed function, the nested execution is recorded in the trace, and its cost is charged inside the caller authorized budget with enforced depth and spend limits.',
    tags: ['agents', 'composition', 'economy'],
  },
  {
    fnSlug: 'scheduled-agent',
    slug: 'scheduled-agent',
    name: 'Scheduled Agent',
    tagline: 'A function that wakes up on a schedule and remembers.',
    category: 'AUTOMATION',
    icon: '⏰',
    description:
      'Runs on a CloudSchedule and keeps state between runs with animica.state under the PERSIST_STATE capability — the pattern behind autonomous agents that accumulate work over time.',
    tags: ['automation', 'schedule', 'agent'],
  },
];

async function main() {
  const owner = await prisma.account.findFirst({ where: { handle: 'examples' } });
  if (!owner) {
    console.log(JSON.stringify({ msg: 'no examples account found — run scripts/cloud-examples.ts first' }));
    process.exitCode = 1;
    return;
  }

  if (unpublish) {
    const r = await prisma.cloudApp.updateMany({ where: { ownerId: owner.id }, data: { status: 'ARCHIVED' } });
    console.log(JSON.stringify({ msg: 'archived example apps', count: r.count }));
    return;
  }

  const results: any[] = [];
  for (const s of SPECS) {
    const fn = await prisma.cloudFunction.findFirst({ where: { ownerId: owner.id, slug: s.fnSlug } });
    if (!fn) {
      results.push({ slug: s.slug, skipped: 'function not deployed' });
      continue;
    }
    if (dryRun) {
      results.push({ slug: s.slug, would: fn.appId ? 'update' : 'create', functionId: fn.id });
      continue;
    }

    const app = await prisma.cloudApp.upsert({
      where: { slug: s.slug },
      create: {
        slug: s.slug,
        ownerId: owner.id,
        name: s.name,
        tagline: s.tagline,
        description: s.description,
        category: s.category as any,
        iconEmoji: s.icon,
        tags: s.tags,
        status: 'PUBLISHED',
        visibility: 'PUBLIC',
        pricingModel: 'PAY_PER_USE',
        priceNanm: 0n,
        capabilities: fn.capabilities,
        publishedAt: new Date(),
      },
      update: {
        name: s.name,
        tagline: s.tagline,
        description: s.description,
        category: s.category as any,
        iconEmoji: s.icon,
        tags: s.tags,
        status: 'PUBLISHED',
        visibility: 'PUBLIC',
        capabilities: fn.capabilities,
      },
    });

    // Link the function to its app so the app page can show the real endpoint and stats.
    if (fn.appId !== app.id) {
      await prisma.cloudFunction.update({ where: { id: fn.id }, data: { appId: app.id } });
    }

    // Refresh the cached counters from the AUTHORITATIVE execution rows rather than inventing
    // engagement numbers — an empty app must read as empty.
    const agg = await prisma.cloudExecution.aggregate({
      where: { functionId: fn.id, status: 'SUCCEEDED' },
      _count: { _all: true },
      _sum: { developerNanm: true },
    });
    await prisma.cloudApp.update({
      where: { id: app.id },
      data: { execCount: agg._count._all, revenueNanm: agg._sum.developerNanm ?? 0n },
    });

    results.push({ slug: s.slug, appId: app.id, functionId: fn.id, executions: agg._count._all });
  }

  console.log(JSON.stringify({ at: new Date().toISOString(), dryRun, owner: owner.address, results }, null, 2));
}

main()
  .catch((e) => {
    console.error(String(e?.message ?? e));
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
