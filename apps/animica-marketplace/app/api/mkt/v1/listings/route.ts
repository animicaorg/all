import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency, publicOk, publicPreflight } from '@/lib/api';
import { prisma } from '@/lib/db';
import { uniqueSlug } from '@/lib/accounts';
import { CATEGORIES } from '@/lib/config';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/listings?search=&category=&type=&sort= -> public catalog (published only).
export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const search = sp.get('search')?.trim() ?? '';
    const category = sp.get('category')?.trim() ?? '';
    const type = sp.get('type')?.trim() ?? '';
    const sort = sp.get('sort') ?? 'trending';
    const take = Math.min(Number(sp.get('limit') ?? 40), 100);

    const where: any = { status: 'PUBLISHED', visibility: 'PUBLIC' };
    if (category && (CATEGORIES as readonly string[]).includes(category)) where.category = category;
    if (type) where.type = type;
    if (search) {
      where.OR = [
        { name: { contains: search, mode: 'insensitive' } },
        { tagline: { contains: search, mode: 'insensitive' } },
        { description: { contains: search, mode: 'insensitive' } },
      ];
    }
    const orderBy =
      sort === 'new' ? { publishedAt: 'desc' as const } :
      sort === 'top' ? { ratingCount: 'desc' as const } :
      { usageCount: 'desc' as const };

    const listings = await prisma.listing.findMany({
      where, orderBy, take,
      select: {
        slug: true, name: true, tagline: true, category: true, type: true, coverUrl: true,
        verified: true, usersCount: true, usageCount: true, ratingSum: true, ratingCount: true,
        mediaKind: true, publishedAt: true,
        owner: { select: { address: true, displayName: true, isAgent: true } },
        prices: { where: { active: true }, select: { model: true, amountNanm: true, perCallNanm: true, periodDays: true } },
      },
    });
    // publicOk: native .anm sites (market.anm/media.anm) read this from an opaque-origin sandbox.
    return publicOk({ listings: jsonSafe(listings) });
  } catch (e) {
    return err(e);
  }
}

// CORS preflight for the public catalog read.
export async function OPTIONS() {
  return publicPreflight();
}

// POST /api/mkt/v1/listings -> create a DRAFT listing (scope: publish). Agents publish here too.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const body = await req.json();
    if (!body.name || typeof body.name !== 'string') throw new ApiError(400, 'bad_request', 'name required');

    const type = ['RAG_ASSISTANT', 'AGENT', 'WORKFLOW', 'KNOWLEDGE_AI', 'MEDIA'].includes(body.type) ? body.type : 'RAG_ASSISTANT';
    const category = (CATEGORIES as readonly string[]).includes(body.category) ? body.category : 'Personal';

    return await withIdempotency(req, ctx, body, async () => {
      const slug = await uniqueSlug(body.slug || body.name);
      const listing = await prisma.listing.create({
        data: {
          slug,
          ownerId: ctx.accountId,
          type,
          name: body.name.slice(0, 120),
          tagline: (body.tagline ?? '').slice(0, 200),
          description: (body.description ?? '').slice(0, 8000),
          category,
          systemPrompt: (body.systemPrompt ?? '').slice(0, 12000),
          model: body.model ?? 'anm-fast-8b',
          temperature: typeof body.temperature === 'number' ? body.temperature : 0.7,
          mediaKind: type === 'MEDIA' ? (body.mediaKind ?? 'image') : null,
          forkable: body.forkable === true,
        },
      });
      // Optional prices on create.
      if (Array.isArray(body.prices)) {
        for (const p of body.prices.slice(0, 6)) {
          const model = ['FREE', 'ONE_TIME', 'SUBSCRIPTION', 'USAGE'].includes(p.model) ? p.model : 'FREE';
          await prisma.price.create({
            data: {
              listingId: listing.id,
              model,
              amountNanm: BigInt(p.amountNanm ?? 0),
              perCallNanm: BigInt(p.perCallNanm ?? 0),
              periodDays: Number(p.periodDays ?? 30),
              label: p.label ?? null,
            },
          });
        }
      }
      return { status: 201, data: { listing: jsonSafe(listing) } };
    });
  } catch (e) {
    return err(e);
  }
}
