import { NextRequest } from 'next/server';
import { ok, err } from '@/lib/api';
import { prisma } from '@/lib/db';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/content/pinnable?limit= -> CIDs a node can replicate to earn hosting rewards,
// least-replicated first (so new capacity goes where it's most needed). The discovery half of the
// hosting protocol: a node calls this, fetches each /content/[cid], stores it, then heartbeats a pin.
export async function GET(req: NextRequest) {
  try {
    const limit = Math.min(Number(req.nextUrl.searchParams.get('limit') || 50), 200);
    const objs = await prisma.contentObject.findMany({
      orderBy: [{ pins: 'asc' }, { createdAt: 'desc' }],
      take: limit,
      select: { cid: true, mime: true, size: true, pins: true },
    });
    // Which .anm names each CID backs (context for the hosting node).
    const cids = objs.map((o) => o.cid);
    const domains = await prisma.anmDomain.findMany({ where: { contentCid: { in: cids } }, select: { name: true, contentCid: true } });
    const byCid: Record<string, string[]> = {};
    for (const d of domains) if (d.contentCid) (byCid[d.contentCid] ||= []).push(`${d.name}.anm`);
    return ok({
      objects: objs.map((o) => ({ ...o, fetch: `/api/mkt/v1/content/${o.cid}`, names: byCid[o.cid] || [] })),
      note: 'fetch each CID, verify bytes hash to it, then POST /content/{cid}/pin to earn hosting rewards',
    });
  } catch (e) {
    return err(e);
  }
}
