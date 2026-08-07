import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { cleanStr } from '@/lib/hire';
import { requireHireAdmin } from '@/lib/hireAuth';
import { createProduct, createMonthlyPlan, paypalConfigured } from '@/lib/paypal';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Admin service-catalog management: the /hire page is fully data-driven from these rows, so
// future offerings (GPU hosting, validators, consulting, …) are launched from here without
// frontend changes.

export async function GET(req: NextRequest) {
  try {
    await requireHireAdmin(req);
    const services = await prisma.hireService.findMany({
      orderBy: [{ sortOrder: 'asc' }, { createdAt: 'asc' }],
      include: { _count: { select: { orders: true } } },
    });
    return ok({ services });
  } catch (e) {
    return err(e);
  }
}

function parseServiceBody(body: any) {
  const data: Record<string, unknown> = {};
  if (body.slug !== undefined) {
    const slug = cleanStr(body.slug, 80, 'slug', true)!.toLowerCase();
    if (!/^[a-z0-9][a-z0-9-]*$/.test(slug)) throw new ApiError(400, 'invalid', 'slug must be kebab-case');
    data.slug = slug;
  }
  if (body.name !== undefined) data.name = cleanStr(body.name, 120, 'name', true)!;
  if (body.tagline !== undefined) data.tagline = cleanStr(body.tagline, 200, 'tagline') ?? '';
  if (body.description !== undefined) data.description = cleanStr(body.description, 4000, 'description') ?? '';
  if (body.icon !== undefined) data.icon = cleanStr(body.icon, 16, 'icon') ?? '🛠️';
  if (body.features !== undefined) {
    if (!Array.isArray(body.features)) throw new ApiError(400, 'invalid', 'features must be an array');
    data.features = body.features.map((f: unknown) => String(f).trim()).filter(Boolean).slice(0, 30);
  }
  if (body.kind !== undefined) {
    if (!['subscription', 'quote'].includes(body.kind)) throw new ApiError(400, 'invalid', 'kind must be subscription|quote');
    data.kind = body.kind;
  }
  if (body.priceUsdMonthly !== undefined) {
    const p = body.priceUsdMonthly === null ? null : Number(body.priceUsdMonthly);
    if (p !== null && (!Number.isInteger(p) || p < 0 || p > 100000)) throw new ApiError(400, 'invalid', 'bad priceUsdMonthly');
    data.priceUsdMonthly = p;
  }
  if (body.setupFeeUsd !== undefined) {
    const f = Number(body.setupFeeUsd);
    if (!Number.isInteger(f) || f < 0 || f > 100000) throw new ApiError(400, 'invalid', 'bad setupFeeUsd');
    data.setupFeeUsd = f;
  }
  if (body.sortOrder !== undefined) {
    const s = Number(body.sortOrder);
    if (!Number.isInteger(s)) throw new ApiError(400, 'invalid', 'bad sortOrder');
    data.sortOrder = s;
  }
  if (body.active !== undefined) data.active = !!body.active;
  return data;
}

// Create a service. If it's a subscription with a price and createPaypalPlan=true, also
// provision the PayPal product + monthly plan (with setup fee) right away.
export async function POST(req: NextRequest) {
  try {
    await requireHireAdmin(req);
    let body: any = {};
    try { body = await req.json(); } catch {}
    const data = parseServiceBody(body);
    if (!data.slug || !data.name) throw new ApiError(400, 'invalid', 'slug and name are required');
    // Check before provisioning: a P2002 after createMonthlyPlan would orphan a live PayPal plan.
    const dupe = await prisma.hireService.findUnique({ where: { slug: data.slug as string } });
    if (dupe) throw new ApiError(409, 'exists', `a service with slug "${data.slug}" already exists`);

    if (body.createPaypalPlan && data.kind !== 'quote') {
      if (!paypalConfigured()) throw new ApiError(503, 'unavailable', 'PayPal not configured');
      const price = (data.priceUsdMonthly as number | null) ?? null;
      if (price == null || price <= 0) throw new ApiError(400, 'invalid', 'priceUsdMonthly required to create a plan');
      const productId = await createProduct(`Animica — ${data.name}`, (data.tagline as string) || (data.name as string));
      data.paypalPlanId = await createMonthlyPlan({
        productId,
        name: `${data.name} — $${price}/month`,
        description: `Animica managed service: ${data.name}`,
        monthlyUsd: price,
        setupFeeUsd: (data.setupFeeUsd as number) ?? 0,
      });
    }

    const service = await prisma.hireService.create({ data: data as any });
    return ok({ service });
  } catch (e) {
    return err(e);
  }
}

// Update a service (by id). Note: price/setup-fee edits do NOT retro-edit an existing PayPal
// plan — create a new plan (createPaypalPlan) if pricing changes; existing subscribers keep
// their old plan terms.
export async function PATCH(req: NextRequest) {
  try {
    await requireHireAdmin(req);
    let body: any = {};
    try { body = await req.json(); } catch {}
    const id = cleanStr(body?.id, 40, 'id', true)!;
    const existing = await prisma.hireService.findUnique({ where: { id } });
    if (!existing) throw new ApiError(404, 'not_found', 'service not found');
    const data = parseServiceBody(body);

    // A PayPal plan's prices are immutable, so changing the displayed price without minting a new
    // plan would advertise one amount and charge another. Refuse rather than mislead buyers.
    const priceChanged = data.priceUsdMonthly !== undefined && data.priceUsdMonthly !== existing.priceUsdMonthly;
    const setupChanged = data.setupFeeUsd !== undefined && data.setupFeeUsd !== existing.setupFeeUsd;
    if ((priceChanged || setupChanged) && existing.paypalPlanId && !body.createPaypalPlan) {
      throw new ApiError(
        400,
        'invalid',
        'changing the price or setup fee requires a new PayPal plan — tick "Create a new PayPal billing plan" (existing subscribers keep their current terms)',
      );
    }

    if (body.createPaypalPlan && (data.kind ?? existing.kind) !== 'quote') {
      if (!paypalConfigured()) throw new ApiError(503, 'unavailable', 'PayPal not configured');
      const price = (data.priceUsdMonthly as number | undefined) ?? existing.priceUsdMonthly;
      if (price == null || price <= 0) throw new ApiError(400, 'invalid', 'priceUsdMonthly required to create a plan');
      const name = (data.name as string | undefined) ?? existing.name;
      const productId = await createProduct(`Animica — ${name}`, name);
      data.paypalPlanId = await createMonthlyPlan({
        productId,
        name: `${name} — $${price}/month`,
        description: `Animica managed service: ${name}`,
        monthlyUsd: price,
        setupFeeUsd: (data.setupFeeUsd as number | undefined) ?? existing.setupFeeUsd,
      });
    }

    const service = await prisma.hireService.update({ where: { id }, data: data as any });
    return ok({ service });
  } catch (e) {
    return err(e);
  }
}
