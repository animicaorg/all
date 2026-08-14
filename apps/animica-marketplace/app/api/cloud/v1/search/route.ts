import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { err, publicOk, publicPreflight, ApiError } from '@/lib/api';
import { CloudCategory } from '@prisma/client';
import { appCard, str } from '../apps/_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/search?q=... — marketplace search across apps, developers, categories and
// tags. Postgres ILIKE through Prisma (`contains` + mode: insensitive) plus one raw unnest for
// tag matching — no external search dependency, and only live, public, unsuspended rows.

export async function OPTIONS() {
  return publicPreflight();
}

export async function GET(req: NextRequest) {
  try {
    const q = str(req.nextUrl.searchParams.get('q'), 80);
    if (q.length < 2) throw new ApiError(400, 'bad_request', 'q must be at least 2 characters');
    const take = Math.min(25, Math.max(1, Number(req.nextUrl.searchParams.get('limit')) || 10));

    const [apps, developers, tagRows] = await Promise.all([
      prisma.cloudApp.findMany({
        where: {
          status: 'PUBLISHED',
          visibility: 'PUBLIC',
          suspendedAt: null,
          OR: [
            { name: { contains: q, mode: 'insensitive' } },
            { tagline: { contains: q, mode: 'insensitive' } },
            { description: { contains: q, mode: 'insensitive' } },
            { tags: { has: q.toLowerCase() } },
          ],
        },
        orderBy: [{ execCount: 'desc' }, { installCount: 'desc' }],
        take,
        include: {
          owner: { select: { handle: true, displayName: true } },
          _count: { select: { functions: { where: { status: 'PUBLISHED' } } } },
        },
      }),
      prisma.account.findMany({
        where: {
          handle: { not: null },
          AND: [
            {
              OR: [
                { handle: { contains: q, mode: 'insensitive' } },
                { displayName: { contains: q, mode: 'insensitive' } },
              ],
            },
            {
              OR: [
                { cloudApps: { some: { status: 'PUBLISHED' } } },
                { cloudFunctions: { some: { status: 'PUBLISHED' } } },
              ],
            },
          ],
        },
        select: { handle: true, displayName: true, avatarUrl: true, bio: true },
        take,
      }),
      // Tag names that match, from live public apps only — a real DISTINCT over the arrays.
      prisma.$queryRaw<{ tag: string }[]>`
        SELECT DISTINCT t AS tag
        FROM "CloudApp", unnest("tags") AS t
        WHERE "status" = 'PUBLISHED' AND "visibility" = 'PUBLIC' AND "suspendedAt" IS NULL
          AND t ILIKE ${'%' + q + '%'}
        ORDER BY t
        LIMIT 20`,
    ]);

    const qUpper = q.toUpperCase().replace(/[\s-]+/g, '_');
    const categories = (Object.values(CloudCategory) as string[]).filter((c) => c.includes(qUpper));

    return publicOk({
      q,
      apps: apps.map(appCard),
      developers: developers.map((d) => ({
        handle: d.handle,
        displayName: d.displayName,
        avatarUrl: d.avatarUrl,
        bio: d.bio,
        profile: `/api/cloud/v1/developers/${d.handle}`,
      })),
      categories,
      tags: tagRows.map((r) => r.tag),
    });
  } catch (e) {
    return err(e);
  }
}
