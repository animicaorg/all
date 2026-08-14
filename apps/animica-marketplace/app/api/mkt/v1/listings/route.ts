import { NextRequest, NextResponse } from 'next/server';
import { err, publicOk, publicPreflight } from '@/lib/api';
import { prisma } from '@/lib/db';
import { STORE_TYPES } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/listings?search=&category=&type=&sort= -> public catalog (published only).
//
// KEPT ALIVE for its shipped consumers — market.anm / media.anm (opaque-origin sandbox reads,
// hence publicOk CORS), app/names/page.tsx and python/animica/growth/collectors.py — but the
// AI-listing era is over: results are restricted to STORE types (APP, DIGITAL_GOOD). Asking
// for a retired AI type (RAG_ASSISTANT/AGENT/WORKFLOW/KNOWLEDGE_AI/MEDIA) returns an EMPTY
// list, not an error, so old catalog readers degrade to "nothing here" instead of breaking.
// Deployed Python functions live on their own catalog under /api/cloud/v1.
export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const search = sp.get('search')?.trim() ?? '';
    const category = sp.get('category')?.trim() ?? '';
    const type = sp.get('type')?.trim() ?? '';
    const sort = sp.get('sort') ?? 'trending';
    const take = Math.min(Number(sp.get('limit') ?? 40), 100);

    // Retired-type filter -> empty result (same envelope, so collectors drain naturally).
    if (type && !(STORE_TYPES as readonly string[]).includes(type)) {
      return publicOk({ listings: [] });
    }

    const where: any = {
      status: 'PUBLISHED',
      visibility: 'PUBLIC',
      type: type ? type : { in: [...STORE_TYPES] },
    };
    if (category) where.category = category;
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

// POST /api/mkt/v1/listings -> 410 GONE. This endpoint only ever created AI-type listings
// (type coerced into the retired RAG_ASSISTANT/AGENT/WORKFLOW/KNOWLEDGE_AI/MEDIA set), and
// that product is retired in favor of Animica Python Cloud. Store apps and games were never
// created here — they go through POST /api/mkt/v1/store/apps (lib/appStore.ts), which is
// untouched. A documented public API never gets a framework 404: agents that still POST here
// get a machine-readable pointer to the successor.
export async function POST() {
  return NextResponse.json(
    {
      error: {
        code: 'gone',
        message:
          'The AI marketplace is retired. Deploy a Python function on Animica Python Cloud instead: ' +
          'POST /api/cloud/v1/functions (then invoke it at /api/cloud/v1/fn/{owner}/{slug}).',
        details: {
          successor: 'animica-python-cloud',
          create: { method: 'POST', path: '/api/cloud/v1/functions' },
          invoke: { method: 'POST', path: '/api/cloud/v1/fn/{owner}/{slug}' },
          browse: '/apps',
          store_apps_unchanged: { method: 'POST', path: '/api/mkt/v1/store/apps' },
        },
      },
    },
    { status: 410 },
  );
}
