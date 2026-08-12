import { prisma } from '@/lib/db';
import { err, publicOk, publicPreflight } from '@/lib/api';
import { formatAnm } from '@/lib/nanm';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/stats — PUBLIC platform stats for the homepage.
//
// REAL COUNTS ONLY (§49): every number is a live aggregate over the authoritative tables.
// Results are cached in-process for a minute (this endpoint sits on the homepage) and marked
// cacheable downstream; nothing is ever estimated, padded, or invented.

const TTL_MS = 60_000;
let cache: { at: number; data: Record<string, unknown> } | null = null;

const CACHE_HEADERS = { 'cache-control': 'public, max-age=30, s-maxage=60' };

export async function OPTIONS() {
  return publicPreflight();
}

export async function GET() {
  try {
    if (cache && Date.now() - cache.at < TTL_MS) {
      return publicOk(cache.data, { headers: CACHE_HEADERS });
    }

    const [functionsDeployed, appsPublished, executions, devRows, execEarned, saleEarned] = await Promise.all([
      prisma.cloudFunction.count({ where: { status: 'PUBLISHED', currentVersion: { gt: 0 } } }),
      prisma.cloudApp.count({ where: { status: 'PUBLISHED', suspendedAt: null } }),
      prisma.cloudExecution.count(),
      prisma.$queryRaw<{ n: bigint }[]>`SELECT COUNT(DISTINCT "ownerId") AS n FROM "CloudFunction"`,
      prisma.cloudExecution.aggregate({ where: { billed: true }, _sum: { developerNanm: true } }),
      prisma.cloudAppPurchase.aggregate({ _sum: { developerNanm: true } }),
    ]);

    const developers = Number(devRows[0]?.n ?? 0n);
    const paidToDevelopersNanm = (execEarned._sum.developerNanm ?? 0n) + (saleEarned._sum.developerNanm ?? 0n);

    const data = {
      functionsDeployed,
      appsPublished,
      executions,
      developers,
      anmPaidToDevelopersNanm: paidToDevelopersNanm.toString(),
      anmPaidToDevelopers: formatAnm(paidToDevelopersNanm),
      generatedAt: new Date().toISOString(),
    };
    cache = { at: Date.now(), data };
    return publicOk(data, { headers: CACHE_HEADERS });
  } catch (e) {
    return err(e);
  }
}
