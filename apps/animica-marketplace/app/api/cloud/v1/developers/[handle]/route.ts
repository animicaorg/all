import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { err, publicOk, publicPreflight, ApiError } from '@/lib/api';
import { formatAnm } from '@/lib/nanm';
import { appCard } from '../../apps/_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/developers/[handle] — the PUBLIC developer profile.
//
// Everything here is a real aggregate over the developer's rows: published apps and functions,
// executions served, ANM actually earned (settled execution revenue + app sale revenue), join
// date. The Founding Developer badge appears ONLY for a genuinely ACCEPTED, unrevoked seat.

export async function OPTIONS() {
  return publicPreflight();
}

export async function GET(req: NextRequest, ctx: { params: { handle: string } }) {
  try {
    const handle = decodeURIComponent(ctx.params.handle).trim().toLowerCase();
    if (!handle) throw new ApiError(404, 'not_found', 'no such developer');

    const account = await prisma.account.findFirst({
      where: { handle: { equals: handle, mode: 'insensitive' } },
      select: {
        id: true,
        handle: true,
        displayName: true,
        bio: true,
        websiteUrl: true,
        avatarUrl: true,
        createdAt: true,
      },
    });
    if (!account) throw new ApiError(404, 'not_found', 'no such developer');

    const [apps, functions, execAgg, earnedExec, earnedSales, founding] = await Promise.all([
      prisma.cloudApp.findMany({
        where: { ownerId: account.id, status: 'PUBLISHED', visibility: 'PUBLIC', suspendedAt: null },
        orderBy: [{ execCount: 'desc' }, { publishedAt: 'desc' }],
        include: {
          owner: { select: { handle: true, displayName: true } },
          _count: { select: { functions: { where: { status: 'PUBLISHED' } } } },
        },
      }),
      prisma.cloudFunction.findMany({
        where: { ownerId: account.id, status: 'PUBLISHED', visibility: 'PUBLIC', suspendedAt: null },
        orderBy: { execCount: 'desc' },
        select: {
          slug: true,
          name: true,
          description: true,
          perCallNanm: true,
          capabilities: true,
          execCount: true,
          currentVersion: true,
          app: { select: { slug: true } },
        },
      }),
      prisma.cloudExecution.aggregate({
        where: { developerAccountId: account.id },
        _count: { _all: true },
      }),
      prisma.cloudExecution.aggregate({
        where: { developerAccountId: account.id, billed: true },
        _sum: { developerNanm: true },
      }),
      prisma.cloudAppPurchase.aggregate({
        where: { app: { ownerId: account.id } },
        _sum: { developerNanm: true },
      }),
      prisma.foundingDeveloper.findUnique({
        where: { accountId: account.id },
        select: { status: true, seq: true, revokedAt: true, acceptedAt: true },
      }),
    ]);

    const earnedNanm = (earnedExec._sum.developerNanm ?? 0n) + (earnedSales._sum.developerNanm ?? 0n);
    const foundingBadge =
      founding && founding.status === 'ACCEPTED' && !founding.revokedAt
        ? { seq: founding.seq, since: founding.acceptedAt }
        : null;

    return publicOk({
      developer: {
        handle: account.handle,
        displayName: account.displayName,
        bio: account.bio,
        websiteUrl: account.websiteUrl,
        avatarUrl: account.avatarUrl,
        joinedAt: account.createdAt,
        founding: foundingBadge,
      },
      apps: apps.map(appCard),
      functions: functions.map((f) => ({
        slug: f.slug,
        name: f.name,
        description: f.description,
        perCallNanm: f.perCallNanm,
        capabilities: f.capabilities,
        execCount: f.execCount,
        version: f.currentVersion,
        app: f.app?.slug ?? null,
        endpoint: `/api/cloud/v1/fn/${encodeURIComponent(account.handle!)}/${f.slug}`,
      })),
      usage: {
        executionsServed: execAgg._count._all,
        earnedNanm,
        earnedAnm: formatAnm(earnedNanm),
        publishedApps: apps.length,
        publishedFunctions: functions.length,
      },
    });
  } catch (e) {
    return err(e);
  }
}
