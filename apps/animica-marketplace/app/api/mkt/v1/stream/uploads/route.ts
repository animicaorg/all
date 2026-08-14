import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, publicPreflight } from '@/lib/api';
import { prisma } from '@/lib/db';
import { daStoreFromFile } from '@/lib/da';
import {
  streamToFile,
  uploadPath,
  artifactPath,
  removeFile,
  freeDiskBytes,
  StreamTooLargeError,
} from '@/lib/mediaStore';
import { randomBytes } from 'node:crypto';
import { mkdir, rename } from 'node:fs/promises';
import { dirname } from 'node:path';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// music.anm / video.anm ingest — POST a raw audio/video file, stream it to disk, fan it
// out across the DA blob store, and mint a MediaItem the creator owns. The bytes never
// enter a JSON body or a DB row: request body → temp upload file → DA chunks; a copy of
// the upload is kept under artifacts/<id> as the local Range cache for the /stream reader.
// Authenticated (wallet session or scoped key, 'names' scope) — the row's ownerAddress is
// the creator's anim1 tip destination, so we take it from the acting account, not a header.

const MAX_BYTES: Record<string, number> = {
  audio: 200 * 1024 * 1024,
  video: 512 * 1024 * 1024,
};

// Header values may be percent-encoded by the browser (titles carry unicode). Decode
// defensively and cap the length so a hostile header can't bloat the row.
function header(req: NextRequest, name: string, max = 300): string {
  const v = req.headers.get(name);
  if (!v) return '';
  let out = v;
  try {
    out = decodeURIComponent(v);
  } catch {
    /* not encoded — use as-is */
  }
  return out.trim().slice(0, max);
}

export function OPTIONS() {
  return publicPreflight();
}

export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'names');

    const kind = header(req, 'x-anm-kind', 16).toLowerCase();
    const maxBytes = MAX_BYTES[kind];
    if (!maxBytes) {
      throw new ApiError(400, 'bad_kind', "x-anm-kind must be 'audio' or 'video'");
    }
    const title = header(req, 'x-anm-title', 200);
    if (!title) throw new ApiError(400, 'bad_title', 'x-anm-title is required');
    const creatorName = header(req, 'x-anm-creator', 120);
    const posterCid = header(req, 'x-anm-poster-cid', 200) || null;
    const mime =
      (req.headers.get('content-type') || 'application/octet-stream')
        .split(';')[0]
        .trim()
        .slice(0, 100) || 'application/octet-stream';

    // Disk budget: keep a hard free-space floor — the gateway disk also runs the node + DB.
    const RESERVE = Number(
      process.env.MEDIA_STORE_MIN_FREE_BYTES ?? 20 * 1024 * 1024 * 1024,
    );
    if ((await freeDiskBytes()) < RESERVE + maxBytes) {
      throw new ApiError(507, 'storage_full', 'Gateway storage is momentarily full — try again soon.');
    }

    // The creator account is the tip destination. ownerAddress is denormalized onto the row.
    const account = await prisma.account.findUnique({ where: { id: ctx.accountId } });
    if (!account) throw new ApiError(404, 'no_account', 'acting account not found');
    const ownerAddress = account.address;

    // Our own id — the path never contains client input.
    const id =
      'mi' +
      randomBytes(14).toString('base64url').replace(/[^a-zA-Z0-9]/g, '').slice(0, 20) +
      Date.now().toString(36);
    const dest = uploadPath(id);

    // Stream the raw body to disk (hashing as it goes); abort the moment it exceeds maxBytes.
    try {
      await streamToFile(req.body, dest, maxBytes);
    } catch (e) {
      if (e instanceof StreamTooLargeError) {
        throw new ApiError(413, 'too_large', `Max ${Math.round(maxBytes / 1048576)} MB for a ${kind} upload.`);
      }
      // streamToFile throws on empty/absent body and on write failure — treat as a bad upload.
      throw new ApiError(400, 'upload_failed', 'Upload stream failed or was empty — try again.');
    }

    // Fan the on-disk file out across DA blobs (never loads the whole file into memory).
    let stored: { blobIds: string[]; size: number; sha3: string };
    try {
      stored = await daStoreFromFile(dest, mime, ownerAddress);
    } catch (e) {
      await removeFile(dest);
      throw e;
    }

    // Keep the upload as the local Range cache under artifacts/<id> (atomic move).
    const artifact = artifactPath(id);
    try {
      await mkdir(dirname(artifact), { recursive: true });
      await rename(dest, artifact);
    } catch (e) {
      await removeFile(dest);
      throw e;
    }

    try {
      await prisma.mediaItem.create({
        data: {
          id,
          kind: kind.toUpperCase() as 'AUDIO' | 'VIDEO',
          ownerId: ctx.accountId,
          ownerAddress,
          title,
          creatorName,
          mime,
          sizeBytes: BigInt(stored.size),
          daBlobsJson: JSON.stringify(stored.blobIds),
          posterCid,
        },
      });
    } catch (e) {
      await removeFile(artifact);
      throw e;
    }

    return ok({ id, blobIds: stored.blobIds, sizeBytes: stored.size }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}
