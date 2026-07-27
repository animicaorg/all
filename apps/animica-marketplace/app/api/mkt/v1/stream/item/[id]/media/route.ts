import { NextRequest } from 'next/server';
import { promises as fs } from 'node:fs';
import { prisma } from '@/lib/db';
import { err, ApiError, PUBLIC_CORS, publicPreflight } from '@/lib/api';
import { daFetchMedia, parseBlobIds } from '@/lib/da';
import { artifactPath, fileSize, fileResponse } from '@/lib/mediaStore';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/stream/item/[id]/media — stream a MediaItem's bytes with HTTP Range support.
// This is the <audio>/<video> src endpoint: byte-range requests let the browser seek and buffer
// without pulling the whole file. Bytes live in the DA layer (content-addressed, immutable); the
// gateway keeps a lazy disk cache under artifactPath(id). On a cache miss we REHYDRATE the file
// from DA (daFetchMedia over the item's blob manifest) and write it to disk, then serve ranges
// off the local file. Public playback endpoint — no auth (the item is public/unlisted by id).

// Long, immutable cache: an item id maps to fixed, content-addressed DA bytes — they never change.
const CACHE_CONTROL = 'public, max-age=31536000, immutable';

function unsatisfiable(total: number): Response {
  return new Response(null, {
    status: 416,
    headers: {
      ...PUBLIC_CORS,
      'Accept-Ranges': 'bytes',
      'Content-Range': `bytes */${total}`,
      'Cache-Control': 'no-store',
    },
  });
}

function withHeaders(res: Response): Response {
  res.headers.set('Cache-Control', CACHE_CONTROL);
  for (const [k, v] of Object.entries(PUBLIC_CORS)) res.headers.set(k, v);
  return res;
}

export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const id = params.id;
    const item = await prisma.mediaItem.findUnique({ where: { id } });
    if (!item) throw new ApiError(404, 'not_found', 'media item not found');

    const path = artifactPath(id);

    // Cache miss → rehydrate the whole file from the DA blob manifest, then serve off disk.
    if ((await fileSize(path)) == null) {
      const bytes = await daFetchMedia(parseBlobIds(item.daBlobsJson));
      await fs.mkdir(path.slice(0, path.lastIndexOf('/')) || '/', { recursive: true }).catch(() => {});
      await fs.writeFile(path, bytes);
    }

    const total = await fileSize(path);
    if (total == null) throw new ApiError(404, 'not_found', 'media bytes unavailable');

    const rangeHeader = req.headers.get('range');
    if (rangeHeader) {
      const m = /^bytes=(\d*)-(\d*)$/.exec(rangeHeader.trim());
      if (!m) return unsatisfiable(total);
      const startStr = m[1];
      const endStr = m[2];

      let start: number;
      let end: number;
      if (startStr === '') {
        // Suffix range: last N bytes.
        const n = parseInt(endStr, 10);
        if (!Number.isFinite(n) || n <= 0) return unsatisfiable(total);
        start = Math.max(0, total - n);
        end = total - 1;
      } else {
        start = parseInt(startStr, 10);
        end = endStr === '' ? total - 1 : parseInt(endStr, 10);
      }

      // Clamp end into the file, then validate.
      if (end > total - 1) end = total - 1;
      if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || start > end || start >= total) {
        return unsatisfiable(total);
      }

      return withHeaders(fileResponse(path, { mime: item.mime, bytes: total, range: { start, end } }));
    }

    return withHeaders(fileResponse(path, { mime: item.mime, bytes: total, range: null }));
  } catch (e) {
    return err(e);
  }
}

export function OPTIONS() {
  return publicPreflight();
}
