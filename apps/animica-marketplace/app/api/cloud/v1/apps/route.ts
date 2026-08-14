import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, publicOk, publicPreflight, authenticate, requireScope, ApiError } from '@/lib/api';
import { requireSlot } from '@/lib/cloud/entitlements';
import { flags } from '@/lib/cloud/config';
import {
  appCard,
  parseCaps,
  parseCategory,
  parseNanm,
  parseSlug,
  parseTags,
  pageParams,
  requireStr,
  str,
} from './_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/apps — the PUBLIC marketplace catalog.
//
// Filters: ?pricing=free|paid|pay-per-use, ?category=..., ?tag=..., ?owner=<handle>,
// ?sort=newest|popular, ?limit/?offset. Popularity is ordered by the REAL execCount /
// installCount columns, which are refreshed transactionally at settlement time — never invented.
export async function OPTIONS() {
  return publicPreflight();
}

const PRICING_FILTERS: Record<string, string[]> = {
  free: ['FREE'],
  paid: ['ONE_TIME', 'SUBSCRIPTION'],
  'pay-per-use': ['PAY_PER_USE'],
};

export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const { take, skip } = pageParams(sp);

    const where: any = { status: 'PUBLISHED', visibility: 'PUBLIC', suspendedAt: null };

    const pricing = str(sp.get('pricing'), 20).toLowerCase();
    if (pricing) {
      const models = PRICING_FILTERS[pricing];
      if (!models) throw new ApiError(400, 'bad_request', 'pricing must be free | paid | pay-per-use');
      where.pricingModel = { in: models };
    }
    if (sp.get('category')) where.category = parseCategory(sp.get('category'));
    const tag = str(sp.get('tag'), 32).toLowerCase();
    if (tag) where.tags = { has: tag };
    const ownerHandle = str(sp.get('owner'), 30).toLowerCase();
    if (ownerHandle) where.owner = { handle: ownerHandle };

    const sort = str(sp.get('sort'), 20).toLowerCase() || 'newest';
    const orderBy =
      sort === 'popular'
        ? [{ execCount: 'desc' as const }, { installCount: 'desc' as const }, { publishedAt: 'desc' as const }]
        : [{ publishedAt: 'desc' as const }];
    if (sort !== 'popular' && sort !== 'newest') {
      throw new ApiError(400, 'bad_request', 'sort must be newest | popular');
    }

    const [rows, total] = await Promise.all([
      prisma.cloudApp.findMany({
        where,
        orderBy,
        take,
        skip,
        include: {
          owner: { select: { handle: true, displayName: true } },
          _count: { select: { functions: { where: { status: 'PUBLISHED' } } } },
        },
      }),
      prisma.cloudApp.count({ where }),
    ]);

    return publicOk({ apps: rows.map(appCard), total, limit: take, offset: skip, sort });
  } catch (e) {
    return err(e);
  }
}

// POST /api/cloud/v1/apps — create a draft app (its owner publishes it later via /publish).
export async function POST(req: NextRequest) {
  try {
    if (!flags.marketplace) throw new ApiError(503, 'disabled', 'the marketplace is not enabled on this node');
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(ctx, 'publish');

    const body = await req.json().catch(() => ({}));
    const slug = parseSlug(body?.slug);
    const name = requireStr(body?.name, 'name', 80);
    const tagline = str(body?.tagline, 160);
    const description = str(body?.description, 10_000);
    const docsMd = str(body?.docsMd, 60_000);
    const category = body?.category != null ? parseCategory(body.category) : 'UTILITIES';
    const iconEmoji = str(body?.iconEmoji, 8) || '🐍';
    const tags = parseTags(body?.tags);
    const capabilities = parseCaps(body?.capabilities);
    const pricingModel = str(body?.pricingModel, 20).toUpperCase() || 'PAY_PER_USE';
    if (!['FREE', 'PAY_PER_USE', 'ONE_TIME', 'SUBSCRIPTION'].includes(pricingModel)) {
      throw new ApiError(400, 'bad_request', 'pricingModel must be FREE | PAY_PER_USE | ONE_TIME | SUBSCRIPTION');
    }
    const priceNanm = body?.priceNanm != null ? parseNanm(body.priceNanm, 'priceNanm') : 0n;
    if ((pricingModel === 'ONE_TIME' || pricingModel === 'SUBSCRIPTION') && priceNanm <= 0n) {
      throw new ApiError(400, 'bad_request', `${pricingModel} pricing requires priceNanm > 0`);
    }
    if ((pricingModel === 'FREE' || pricingModel === 'PAY_PER_USE') && priceNanm !== 0n) {
      throw new ApiError(400, 'bad_request', `${pricingModel} apps must have priceNanm = 0 (usage is metered per call)`);
    }
    const visibility = str(body?.visibility, 12).toUpperCase() || 'PUBLIC';
    if (!['PUBLIC', 'UNLISTED', 'PRIVATE'].includes(visibility)) {
      throw new ApiError(400, 'bad_request', 'visibility must be PUBLIC | UNLISTED | PRIVATE');
    }

    // Live slot count, never a cached counter (§92).
    const current = await prisma.cloudApp.count({
      where: { ownerId: ctx.accountId, status: { not: 'ARCHIVED' } },
    });
    await requireSlot(ctx.accountId, 'max_apps', current);

    try {
      const app = await prisma.cloudApp.create({
        data: {
          slug,
          ownerId: ctx.accountId,
          name,
          tagline,
          description,
          docsMd,
          category,
          iconEmoji,
          tags,
          capabilities,
          pricingModel: pricingModel as any,
          priceNanm,
          visibility: visibility as any,
          status: 'DRAFT',
        },
        include: { owner: { select: { handle: true, displayName: true } } },
      });
      return ok(
        {
          app: { ...appCard(app as any), status: app.status, visibility: app.visibility, id: app.id },
          next: `publish it with POST /api/cloud/v1/apps/${app.slug}/publish once it has a deployed function`,
        },
        { status: 201 },
      );
    } catch (e: any) {
      if (e?.code === 'P2002') throw new ApiError(409, 'slug_taken', `an app named "${slug}" already exists`);
      throw e;
    }
  } catch (e) {
    return err(e);
  }
}
