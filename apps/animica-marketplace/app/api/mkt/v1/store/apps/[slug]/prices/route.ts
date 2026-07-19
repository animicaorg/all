import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withCredentialedCors, credentialedPreflight } from '@/lib/api';
import { prisma } from '@/lib/db';
import { STORE_TYPES, asApiError } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// Credentialed preflight — the Game Lab publish flow sets a game's price cross-origin (animica.io).
export async function OPTIONS(req: NextRequest) {
  return credentialedPreflight(req, 'GET, PUT, OPTIONS');
}

// Store listing price editor (owner, scope 'publish'). The store POST /apps route already
// seeds prices on create; this route lets the Developer Center edit them afterward. Prices are
// ordinary Price rows (the same model the catalog + purchase-intent read) — APP / DIGITAL_GOOD
// listings use FREE | ONE_TIME | SUBSCRIPTION (USAGE is an AI-listing model, rejected here).
//
//   GET /api/mkt/v1/store/apps/[slug]/prices          -> { prices: [active] }
//   PUT /api/mkt/v1/store/apps/[slug]/prices { prices } -> replace the ACTIVE price set
//
// PUT deactivates the current active prices (never DELETEs — a Price may be referenced by a
// Purchase) and creates the new set active, so the catalog immediately reflects the change while
// historical purchases keep pointing at the price they settled with.

const STORE_PRICE_MODELS = ['FREE', 'ONE_TIME', 'SUBSCRIPTION'] as const;
const MAX_PRICES = 6;
const MAX_AMOUNT_NANM = 10n ** 27n; // generous sanity ceiling
const PRICE_SELECT = { id: true, model: true, amountNanm: true, perCallNanm: true, periodDays: true, label: true } as const;

async function requireOwnedStoreListing(req: NextRequest, slug: string) {
  const ctx = await authenticate(req);
  if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
  requireScope(ctx, 'publish');
  const listing = await prisma.listing.findUnique({ where: { slug }, select: { id: true, ownerId: true, type: true } });
  if (!listing || !(STORE_TYPES as readonly string[]).includes(listing.type)) throw new ApiError(404, 'not_found', 'store listing not found');
  if (listing.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');
  return { ctx, listing };
}

export async function GET(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const { listing } = await requireOwnedStoreListing(req, params.slug);
    const prices = await prisma.price.findMany({
      where: { listingId: listing.id, active: true },
      orderBy: { createdAt: 'asc' },
      select: PRICE_SELECT,
    });
    return withCredentialedCors(req, ok({ prices: jsonSafe(prices) }));
  } catch (e) {
    return withCredentialedCors(req, err(asApiError(e)));
  }
}

interface CleanPrice { model: string; amountNanm: bigint; periodDays: number; label: string | null }

export async function PUT(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const { listing } = await requireOwnedStoreListing(req, params.slug);
    const body = await req.json().catch(() => ({}));
    const arr = Array.isArray(body.prices) ? body.prices : null;
    if (!arr) throw new ApiError(400, 'bad_request', 'prices (array) required');
    if (arr.length > MAX_PRICES) throw new ApiError(400, 'too_many_prices', `max ${MAX_PRICES} prices`);

    const clean: CleanPrice[] = arr.map((p: any) => {
      const model = String(p?.model ?? '');
      if (!(STORE_PRICE_MODELS as readonly string[]).includes(model)) {
        throw new ApiError(400, 'bad_price', `model must be one of ${STORE_PRICE_MODELS.join(', ')}`);
      }
      let amountNanm = 0n;
      if (model !== 'FREE') {
        try { amountNanm = BigInt(String(p?.amountNanm ?? '0')); }
        catch { throw new ApiError(400, 'bad_price', 'amountNanm must be an integer nANM value'); }
        if (amountNanm < 0n) throw new ApiError(400, 'bad_price', 'amount must be >= 0');
        if (amountNanm > MAX_AMOUNT_NANM) throw new ApiError(400, 'bad_price', 'amount too large');
      }
      let periodDays = 30;
      if (model === 'SUBSCRIPTION') {
        periodDays = Math.floor(Number(p?.periodDays ?? 30));
        if (!Number.isInteger(periodDays) || periodDays < 1 || periodDays > 3650) {
          throw new ApiError(400, 'bad_price', 'periodDays must be an integer between 1 and 3650');
        }
      }
      const label = typeof p?.label === 'string' && p.label.trim() ? p.label.trim().slice(0, 60) : null;
      return { model, amountNanm, periodDays, label };
    });

    await prisma.$transaction(async (tx) => {
      await tx.price.updateMany({ where: { listingId: listing.id, active: true }, data: { active: false } });
      for (const c of clean) {
        await tx.price.create({
          data: {
            listingId: listing.id,
            model: c.model as any,
            amountNanm: c.amountNanm,
            perCallNanm: 0n,
            periodDays: c.periodDays,
            label: c.label,
            active: true,
          },
        });
      }
    });

    const prices = await prisma.price.findMany({
      where: { listingId: listing.id, active: true },
      orderBy: { createdAt: 'asc' },
      select: PRICE_SELECT,
    });
    return withCredentialedCors(req, ok({ prices: jsonSafe(prices) }));
  } catch (e) {
    return withCredentialedCors(req, err(asApiError(e)));
  }
}
