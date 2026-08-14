import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, publicOk, publicPreflight, authenticate, requireScope, ApiError } from '@/lib/api';
import { findPublishedApp, pageParams, str } from '../../_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET  /api/cloud/v1/apps/[slug]/reviews — public review list + real rating histogram.
// POST /api/cloud/v1/apps/[slug]/reviews — one review per account, and ONLY from a genuine
// user: a real purchaser, or someone with a SUCCEEDED execution of this app on record.
// Ratings transactionally recompute app.ratingSum/ratingCount from the review rows, so the
// catalog numbers are always reproducible from the database (§92).

export async function OPTIONS() {
  return publicPreflight();
}

export async function GET(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    const app = await findPublishedApp(decodeURIComponent(ctx.params.slug).trim().toLowerCase());
    if (!app) throw new ApiError(404, 'not_found', 'no such app');
    const { take, skip } = pageParams(req.nextUrl.searchParams, 20, 50);

    const [rows, agg, histogramRows] = await Promise.all([
      prisma.cloudReview.findMany({
        where: { appId: app.id, hidden: false },
        orderBy: { createdAt: 'desc' },
        take,
        skip,
        include: { account: { select: { handle: true, displayName: true } } },
      }),
      prisma.cloudReview.aggregate({
        where: { appId: app.id, hidden: false },
        _avg: { rating: true },
        _count: { _all: true },
      }),
      prisma.cloudReview.groupBy({
        by: ['rating'],
        where: { appId: app.id, hidden: false },
        _count: { _all: true },
      }),
    ]);

    const histogram: Record<string, number> = { '1': 0, '2': 0, '3': 0, '4': 0, '5': 0 };
    for (const h of histogramRows) histogram[String(h.rating)] = h._count._all;

    return publicOk({
      app: app.slug,
      summary: {
        avg: agg._avg.rating != null ? Math.round(agg._avg.rating * 10) / 10 : null,
        count: agg._count._all,
        histogram,
      },
      reviews: rows.map((r) => ({
        rating: r.rating,
        body: r.body,
        createdAt: r.createdAt,
        updatedAt: r.updatedAt,
        author: { handle: r.account.handle, displayName: r.account.displayName },
      })),
      limit: take,
      offset: skip,
    });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key to review');
    requireScope(auth, 'use');

    const app = await findPublishedApp(decodeURIComponent(ctx.params.slug).trim().toLowerCase());
    if (!app) throw new ApiError(404, 'not_found', 'no such app');
    if (app.ownerId === auth.accountId) {
      throw new ApiError(403, 'own_app', 'developers cannot review their own app');
    }

    const body = await req.json().catch(() => ({}));
    const rating = Number(body?.rating);
    if (!Number.isInteger(rating) || rating < 1 || rating > 5) {
      throw new ApiError(400, 'bad_request', 'rating must be an integer 1-5');
    }
    const text = str(body?.body, 4000);

    // The review gate: a purchase row (any state — they paid) OR a succeeded execution.
    const [purchase, execution] = await Promise.all([
      prisma.cloudAppPurchase.findFirst({ where: { appId: app.id, accountId: auth.accountId }, select: { id: true } }),
      prisma.cloudExecution.findFirst({
        where: { appId: app.id, callerAccountId: auth.accountId, status: 'SUCCEEDED' },
        select: { id: true },
      }),
    ]);
    if (!purchase && !execution) {
      throw new ApiError(
        403,
        'not_a_user',
        'only genuine users may review: purchase this app or run one of its functions successfully first',
      );
    }

    const existing = await prisma.cloudReview.findUnique({
      where: { appId_accountId: { appId: app.id, accountId: auth.accountId } },
      select: { id: true },
    });

    const review = await prisma.$transaction(async (tx) => {
      const saved = await tx.cloudReview.upsert({
        where: { appId_accountId: { appId: app.id, accountId: auth.accountId } },
        create: { appId: app.id, accountId: auth.accountId, rating, body: text },
        update: { rating, body: text },
      });
      // Recompute the cached rating from the authoritative rows, in the same transaction.
      const agg = await tx.cloudReview.aggregate({
        where: { appId: app.id, hidden: false },
        _sum: { rating: true },
        _count: { _all: true },
      });
      await tx.cloudApp.update({
        where: { id: app.id },
        data: { ratingSum: agg._sum.rating ?? 0, ratingCount: agg._count._all },
      });
      return saved;
    });

    return ok(
      {
        review: { rating: review.rating, body: review.body, createdAt: review.createdAt, updatedAt: review.updatedAt },
        updated: Boolean(existing),
        basis: purchase ? 'purchase' : 'execution',
      },
      { status: existing ? 200 : 201 },
    );
  } catch (e) {
    return err(e);
  }
}
