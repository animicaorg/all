import { NextRequest } from 'next/server';
import { publicOk, publicPreflight, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/stream/item/[id] -> one media item's public metadata.
// Public, read-only — the wallet player + native .anm sites read this cross-origin.
export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const row = await prisma.mediaItem.findUnique({
      where: { id: params.id },
      select: {
        id: true, kind: true, title: true, creatorName: true, ownerAddress: true,
        posterCid: true, mime: true, durationSec: true, plays: true,
        tipTotalNanm: true, tipCount: true, createdAt: true,
        description: true, sizeBytes: true,
      },
    });
    if (!row) throw new ApiError(404, 'not_found', 'media item not found');

    const item = {
      id: row.id,
      kind: row.kind,
      title: row.title,
      creatorName: row.creatorName,
      ownerAddress: row.ownerAddress,
      posterCid: row.posterCid,
      mime: row.mime,
      durationSec: row.durationSec,
      plays: row.plays,
      tipTotalNanm: row.tipTotalNanm, // -> string via jsonSafe
      tipCount: row.tipCount,
      createdAt: row.createdAt,
      description: row.description,
      sizeBytes: row.sizeBytes, // -> string via jsonSafe
    };

    return publicOk({ item: jsonSafe(item) });
  } catch (e) {
    return err(e);
  }
}

export function OPTIONS() {
  return publicPreflight();
}
