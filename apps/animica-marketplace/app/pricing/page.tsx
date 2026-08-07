import type { Metadata } from 'next';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';
import { PAYPAL_CLIENT_ID, paypalEnv } from '@/lib/paypal';
import { PLAN_CATALOG } from '@/lib/planConfig';
import PricingClient, { type PricingPlanDto } from './PricingClient';

export const metadata: Metadata = {
  title: 'Pricing — Animica',
  description:
    'AI is free. Put it to work. Animica plans unlock autonomous Workers, scheduled execution, production APIs, larger .anm deployments, marketplace selling, teams and business infrastructure.',
  alternates: { canonical: 'https://animica.dev/pricing' },
  openGraph: {
    title: 'Pricing — Animica',
    description: 'AI is free. Put it to work. Plans for automation, production APIs and teams.',
    url: 'https://animica.dev/pricing',
    siteName: 'Animica',
    type: 'website',
  },
};

export const dynamic = 'force-dynamic';

// Catalog rows come from the Plan table (admin-tunable, like /hire's HireService), failing
// soft to the code catalog while the subs migration hasn't landed (checkout disabled — a
// plan is only checkout-ready once a DB row carries its minted PayPal plan id).
export default async function PricingPage({ searchParams }: { searchParams?: { highlight?: string } }) {
  let rows: Array<{
    key: string; name: string; tagline: string; icon: string; features: string[];
    priceUsdCents: number; featured: boolean; sortOrder: number; paypalPlanId: string | null;
  }> = [];
  let fromDb = true;
  try {
    rows = await prisma.plan.findMany({ where: { active: true }, orderBy: { sortOrder: 'asc' } });
  } catch {
    fromDb = false;
  }
  if (rows.length === 0) {
    fromDb = false;
    rows = PLAN_CATALOG.map((p) => ({ ...p, paypalPlanId: null }));
  }
  const plans: PricingPlanDto[] = rows
    .slice()
    .sort((a, b) => a.sortOrder - b.sortOrder)
    .map((p) => ({
      key: p.key,
      name: p.name,
      tagline: p.tagline,
      icon: p.icon,
      features: p.features,
      priceUsdCents: p.priceUsdCents,
      featured: p.featured,
      checkoutReady: p.priceUsdCents === 0 || (fromDb && !!p.paypalPlanId && !!PAYPAL_CLIENT_ID),
    }));
  return (
    <PricingClient
      plans={jsonSafe(plans)}
      paypal={{ clientId: PAYPAL_CLIENT_ID, env: paypalEnv }}
      highlight={typeof searchParams?.highlight === 'string' ? searchParams.highlight : ''}
    />
  );
}
