import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { adminActor, audit, readJson, requireString, optionalString, pageParams } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/apps — marketplace app moderation (§39).
//   GET  ?q=&status= -> apps with live aggregates (executions/revenue recomputed, not cached)
//   POST {appId, action: 'pause'|'unpause', reason} -> availability change, audited.

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const q = url.searchParams.get('q')?.trim() ?? '';
    const status = url.searchParams.get('status') ?? '';
    const { take, skip } = pageParams(req);
    const where: Record<string, unknown> = {};
    if (status) where.status = status;
    if (q) {
      where.OR = [
        { slug: { contains: q, mode: 'insensitive' } },
        { name: { contains: q, mode: 'insensitive' } },
        { owner: { address: q } },
        { owner: { handle: { contains: q, mode: 'insensitive' } } },
      ];
    }
    const [rows, total] = await Promise.all([
      prisma.cloudApp.findMany({
        where: where as any,
        orderBy: { createdAt: 'desc' },
        take,
        skip,
        include: { owner: { select: { address: true, handle: true, displayName: true } }, _count: { select: { functions: true } } },
      }),
      prisma.cloudApp.count({ where: where as any }),
    ]);

    // Authoritative per-app numbers for the listed page (caches are display-only elsewhere).
    const ids = rows.map((r) => r.id);
    const [execAgg, purchaseAgg] = ids.length
      ? await Promise.all([
          prisma.cloudExecution.groupBy({
            by: ['appId'],
            where: { appId: { in: ids } },
            _count: { _all: true },
            _sum: { priceNanm: true, platformFeeNanm: true, developerNanm: true },
          }),
          prisma.cloudAppPurchase.groupBy({
            by: ['appId'],
            where: { appId: { in: ids }, status: { not: 'REFUNDED' } },
            _count: { _all: true },
            _sum: { amountNanm: true },
          }),
        ])
      : [[], []];
    const execBy = new Map(execAgg.map((r) => [r.appId, r]));
    const purchaseBy = new Map(purchaseAgg.map((r) => [r.appId, r]));

    return ok({
      rows: rows.map((r) => ({
        ...r,
        live: {
          executions: execBy.get(r.id)?._count._all ?? 0,
          grossNanm: execBy.get(r.id)?._sum.priceNanm ?? 0n,
          platformFeeNanm: execBy.get(r.id)?._sum.platformFeeNanm ?? 0n,
          developerNanm: execBy.get(r.id)?._sum.developerNanm ?? 0n,
          purchases: purchaseBy.get(r.id)?._count._all ?? 0,
          purchaseGrossNanm: purchaseBy.get(r.id)?._sum.amountNanm ?? 0n,
        },
      })),
      total,
    });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const actor = await adminActor(req);
    const body = await readJson(req);
    const appId = requireString(body, 'appId');
    const action = requireString(body, 'action', 40);
    const reason = optionalString(body, 'reason');

    const app = await prisma.cloudApp.findUnique({ where: { id: appId } });
    if (!app) throw new ApiError(404, 'not_found', 'app not found');

    if (action === 'pause') {
      if (!reason) throw new ApiError(400, 'bad_request', "'reason' is required to pause an app");
      if (app.status === 'SUSPENDED') throw new ApiError(409, 'conflict', 'app is already suspended');
      const updated = await prisma.$transaction(async (tx) => {
        const row = await tx.cloudApp.update({
          where: { id: appId },
          data: { status: 'SUSPENDED', suspendedAt: new Date(), suspendedReason: reason },
        });
        await audit(
          tx,
          actor,
          'app.pause',
          `cloud_app:${appId}`,
          { status: app.status, slug: app.slug },
          { status: 'SUSPENDED', suspendedReason: reason },
          reason,
        );
        return row;
      });
      return ok({ app: updated });
    }

    if (action === 'unpause') {
      if (app.status !== 'SUSPENDED') throw new ApiError(409, 'conflict', `app is ${app.status}, not SUSPENDED`);
      const restoreTo = app.publishedAt ? 'PUBLISHED' : 'DRAFT';
      const updated = await prisma.$transaction(async (tx) => {
        const row = await tx.cloudApp.update({
          where: { id: appId },
          data: { status: restoreTo, suspendedAt: null, suspendedReason: null },
        });
        await audit(
          tx,
          actor,
          'app.unpause',
          `cloud_app:${appId}`,
          { status: 'SUSPENDED', suspendedReason: app.suspendedReason },
          { status: restoreTo },
          reason,
        );
        return row;
      });
      return ok({ app: updated });
    }

    throw new ApiError(400, 'bad_request', `unknown action '${action}'`);
  } catch (e) {
    return err(e);
  }
}
