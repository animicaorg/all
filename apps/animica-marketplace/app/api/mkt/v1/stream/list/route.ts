import { NextRequest } from 'next/server';
import { publicOk, publicPreflight, err } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/stream/list?kind=AUDIO|VIDEO&search=&sort=new|top -> public media feed.
// Public, read-only, no secrets — the wallet Media tab + native .anm sites read this cross-origin.
export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const kind = sp.get('kind')?.trim().toUpperCase() ?? '';
    const search = sp.get('search')?.trim() ?? '';
    const sort = sp.get('sort') === 'top' ? 'top' : 'new';

    const where: any = { visibility: 'public' };
    if (kind === 'AUDIO' || kind === 'VIDEO') where.kind = kind;
    if (search) {
      where.OR = [
        { title: { contains: search, mode: 'insensitive' } },
        { creatorName: { contains: search, mode: 'insensitive' } },
      ];
    }

    const orderBy =
      sort === 'top'
        ? { tipTotalNanm: 'desc' as const }
        : { createdAt: 'desc' as const };

    const rows = await prisma.mediaItem.findMany({
      where,
      orderBy,
      take: 60,
      select: {
        id: true, kind: true, title: true, creatorName: true, ownerAddress: true,
        posterCid: true, mime: true, durationSec: true, plays: true,
        tipTotalNanm: true, tipCount: true, createdAt: true,
      },
    });

    const items = rows.map((r) => ({
      id: r.id,
      kind: r.kind,
      title: r.title,
      creatorName: r.creatorName,
      ownerAddress: r.ownerAddress,
      posterCid: r.posterCid,
      mime: r.mime,
      durationSec: r.durationSec,
      plays: r.plays,
      tipTotalNanm: r.tipTotalNanm, // -> string via jsonSafe
      tipCount: r.tipCount,
      createdAt: r.createdAt,
    }));

    return publicOk({ items: jsonSafe(items) });
  } catch (e) {
    return err(e);
  }
}

export function OPTIONS() {
  return publicPreflight();
}
