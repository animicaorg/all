import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err } from '@/lib/api';
import { PAYPAL_CLIENT_ID, paypalEnv } from '@/lib/paypal';
import { PLAN_CATALOG, isContactSalesPlan, limitsFor } from '@/lib/planConfig';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Public tier catalog for /pricing. Never exposes PayPal plan ids — only checkoutReady
// (mirrors the hire catalog DTO posture). Fails soft to the code catalog while the
// subs migration hasn't landed yet (P2021), with checkout disabled.
export async function GET(_req: NextRequest) {
  try {
    let rows: Array<{
      key: string; name: string; tagline: string; icon: string; features: string[];
      priceUsdCents: number; featured: boolean; sortOrder: number; paypalPlanId: string | null;
      limitsJson: string | null;
    }> = [];
    let fromDb = true;
    try {
      rows = await prisma.plan.findMany({ where: { active: true }, orderBy: { sortOrder: 'asc' } });
    } catch {
      fromDb = false;
      rows = PLAN_CATALOG.map((p) => ({ ...p, paypalPlanId: null, limitsJson: null }));
    }
    const plans = rows.map((p) => ({
      key: p.key,
      name: p.name,
      tagline: p.tagline,
      icon: p.icon,
      features: p.features,
      priceUsdCents: p.priceUsdCents,
      featured: p.featured,
      sortOrder: p.sortOrder,
      limits: limitsFor(p.key, p.limitsJson),
      // Contact-sales tiers (enterprise) are NEVER checkout-ready: no PayPal plan exists for
      // them by design; the client renders "Contact sales" → /api/cloud/v1/enterprise.
      contactSales: isContactSalesPlan(p.key),
      checkoutReady:
        !isContactSalesPlan(p.key) &&
        (p.priceUsdCents === 0 || (fromDb && !!p.paypalPlanId && !!PAYPAL_CLIENT_ID)),
    }));
    return ok({ plans, paypal: { clientId: PAYPAL_CLIENT_ID, env: paypalEnv } });
  } catch (e) {
    return err(e);
  }
}
