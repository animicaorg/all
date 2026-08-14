import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, publicOk, publicPreflight, authenticate, requireScope, ApiError } from '@/lib/api';
import {
  appCard,
  parseCaps,
  parseCategory,
  parseNanm,
  parseTags,
  ratingOf,
  str,
} from '../_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET  /api/cloud/v1/apps/[slug]  — public detail: functions, pricing, capabilities, REAL usage.
// PATCH /api/cloud/v1/apps/[slug] — owner edits the listing (status changes go through /publish).

export async function OPTIONS() {
  return publicPreflight();
}

export async function GET(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    const slug = decodeURIComponent(ctx.params.slug).trim().toLowerCase();
    const app = await prisma.cloudApp.findUnique({
      where: { slug },
      include: { owner: { select: { id: true, handle: true, address: true, displayName: true, avatarUrl: true } } },
    });
    if (!app) throw new ApiError(404, 'not_found', 'no such app');

    // Drafts, suspended and non-public apps are visible to their owner only.
    let viewerId: string | null = null;
    try {
      const auth = await authenticate(req);
      viewerId = auth?.accountId ?? null;
    } catch {}
    const isOwner = viewerId != null && viewerId === app.ownerId;
    const publiclyVisible = app.status === 'PUBLISHED' && !app.suspendedAt && app.visibility !== 'PRIVATE';
    if (!publiclyVisible && !isOwner) throw new ApiError(404, 'not_found', 'no such app');

    const monthAgo = new Date(Date.now() - 30 * 24 * 3600 * 1000);
    const [functions, execTotal, exec30d, purchasers, reviewAgg, recentReviews, myPurchase, myGrant] =
      await Promise.all([
        prisma.cloudFunction.findMany({
          where: isOwner
            ? { appId: app.id }
            : { appId: app.id, status: 'PUBLISHED', visibility: { in: ['PUBLIC', 'UNLISTED'] }, suspendedAt: null },
          select: {
            slug: true,
            name: true,
            description: true,
            status: true,
            perCallNanm: true,
            requiresAuth: true,
            capabilities: true,
            currentVersion: true,
            execCount: true,
          },
          orderBy: { createdAt: 'asc' },
        }),
        prisma.cloudExecution.count({ where: { appId: app.id } }),
        prisma.cloudExecution.count({ where: { appId: app.id, createdAt: { gte: monthAgo } } }),
        prisma.cloudAppPurchase.count({ where: { appId: app.id } }),
        prisma.cloudReview.aggregate({
          where: { appId: app.id, hidden: false },
          _avg: { rating: true },
          _count: { _all: true },
        }),
        prisma.cloudReview.findMany({
          where: { appId: app.id, hidden: false },
          orderBy: { createdAt: 'desc' },
          take: 3,
          include: { account: { select: { handle: true, displayName: true } } },
        }),
        viewerId
          ? prisma.cloudAppPurchase.findFirst({ where: { appId: app.id, accountId: viewerId, status: 'ACTIVE' } })
          : null,
        viewerId
          ? prisma.cloudGrant.findUnique({
              where: { accountId_subjectKind_subjectId: { accountId: viewerId, subjectKind: 'app', subjectId: app.id } },
            })
          : null,
      ]);

    const ownerKey = app.owner.handle ?? app.owner.address;
    const data: any = {
      app: {
        ...appCard(app as any),
        id: app.id,
        description: app.description,
        docsMd: app.docsMd,
        bannerUrl: app.bannerUrl,
        status: app.status,
        visibility: app.visibility,
        suspended: Boolean(app.suspendedAt),
        createdAt: app.createdAt,
        owner: {
          handle: app.owner.handle,
          displayName: app.owner.displayName,
          avatarUrl: app.owner.avatarUrl,
          profile: app.owner.handle ? `/api/cloud/v1/developers/${app.owner.handle}` : null,
        },
      },
      functions: functions.map((f) => ({
        ...f,
        endpoint: `/api/cloud/v1/fn/${encodeURIComponent(ownerKey)}/${f.slug}`,
      })),
      usage: { executionsTotal: execTotal, executions30d: exec30d, purchasers },
      rating: {
        avg: reviewAgg._avg.rating != null ? Math.round(reviewAgg._avg.rating * 10) / 10 : null,
        count: reviewAgg._count._all,
      },
      recentReviews: recentReviews.map((r) => ({
        rating: r.rating,
        body: r.body,
        createdAt: r.createdAt,
        author: { handle: r.account.handle, displayName: r.account.displayName },
      })),
    };
    if (viewerId) {
      data.viewer = {
        isOwner,
        purchased: Boolean(myPurchase),
        purchaseKind: myPurchase?.kind ?? null,
        grant: myGrant && !myGrant.revokedAt
          ? {
              capabilities: myGrant.capabilities,
              maxPerCallNanm: myGrant.maxPerCallNanm,
              maxPerExecNanm: myGrant.maxPerExecNanm,
              dailyCapNanm: myGrant.dailyCapNanm,
              expiresAt: myGrant.expiresAt,
            }
          : null,
      };
    }
    return publicOk(data);
  } catch (e) {
    return err(e);
  }
}

export async function PATCH(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'publish');

    const slug = decodeURIComponent(ctx.params.slug).trim().toLowerCase();
    const app = await prisma.cloudApp.findUnique({ where: { slug } });
    if (!app || app.ownerId !== auth.accountId) throw new ApiError(404, 'not_found', 'no such app');
    if (app.suspendedAt) throw new ApiError(403, 'suspended', app.suspendedReason || 'this app is suspended');
    if (app.status === 'ARCHIVED') throw new ApiError(409, 'archived', 'archived apps cannot be edited');

    const body = await req.json().catch(() => ({}));
    const data: any = {};
    if (body?.name != null) data.name = str(body.name, 80) || app.name;
    if (body?.tagline != null) data.tagline = str(body.tagline, 160);
    if (body?.description != null) data.description = str(body.description, 10_000);
    if (body?.docsMd != null) data.docsMd = str(body.docsMd, 60_000);
    if (body?.category != null) data.category = parseCategory(body.category);
    if (body?.iconEmoji != null) data.iconEmoji = str(body.iconEmoji, 8) || app.iconEmoji;
    if (body?.iconUrl !== undefined) data.iconUrl = str(body.iconUrl, 500) || null;
    if (body?.bannerUrl !== undefined) data.bannerUrl = str(body.bannerUrl, 500) || null;
    if (body?.tags != null) data.tags = parseTags(body.tags);
    if (body?.capabilities != null) data.capabilities = parseCaps(body.capabilities);
    if (body?.visibility != null) {
      const v = str(body.visibility, 12).toUpperCase();
      if (!['PUBLIC', 'UNLISTED', 'PRIVATE'].includes(v)) {
        throw new ApiError(400, 'bad_request', 'visibility must be PUBLIC | UNLISTED | PRIVATE');
      }
      data.visibility = v;
    }
    if (body?.pricingModel != null || body?.priceNanm != null) {
      const model = body?.pricingModel != null ? str(body.pricingModel, 20).toUpperCase() : app.pricingModel;
      if (!['FREE', 'PAY_PER_USE', 'ONE_TIME', 'SUBSCRIPTION'].includes(model)) {
        throw new ApiError(400, 'bad_request', 'pricingModel must be FREE | PAY_PER_USE | ONE_TIME | SUBSCRIPTION');
      }
      const price = body?.priceNanm != null ? parseNanm(body.priceNanm, 'priceNanm') : app.priceNanm;
      if ((model === 'ONE_TIME' || model === 'SUBSCRIPTION') && price <= 0n) {
        throw new ApiError(400, 'bad_request', `${model} pricing requires priceNanm > 0`);
      }
      if ((model === 'FREE' || model === 'PAY_PER_USE') && price !== 0n) {
        throw new ApiError(400, 'bad_request', `${model} apps must have priceNanm = 0`);
      }
      data.pricingModel = model;
      data.priceNanm = price;
    }
    if (Object.keys(data).length === 0) throw new ApiError(400, 'bad_request', 'nothing to update');

    const updated = await prisma.cloudApp.update({
      where: { id: app.id },
      data,
      include: { owner: { select: { handle: true, displayName: true } } },
    });
    return ok({
      app: {
        ...appCard(updated as any),
        id: updated.id,
        status: updated.status,
        visibility: updated.visibility,
        description: updated.description,
      },
    });
  } catch (e) {
    return err(e);
  }
}
