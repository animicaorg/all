import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { putObject, MAX_CONTENT_BYTES } from '@/lib/storage';
import { STORE_TYPES, assetUrl } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Store imagery upload (icon / screenshots / banner). RAW image body (never JSON);
// bytes land as CID-addressed ContentObjects (lib/storage.ts, <=2MB, in-DB — same rail
// as .anm hosted content), the StoreAsset row orders them per listing. Served back via
// GET /api/mkt/v1/content/[cid].

const KINDS = ['ICON', 'SCREENSHOT', 'BANNER'] as const;
const IMAGE_MIMES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);
const MAX_ASSETS_PER_LISTING = 16;

// GET /api/mkt/v1/store/assets?listing=<slug> -> the owner's imagery for a store listing
// (icon/screenshots/banner), ordered. Owner-only; powers the Developer Center editor (the public
// detail route omits assets for DRAFT listings, so the editor needs this owner-scoped read).
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const slug = (req.nextUrl.searchParams.get('listing') || '').trim();
    if (!slug) throw new ApiError(400, 'bad_request', 'listing (slug) query param required');
    const listing = await prisma.listing.findUnique({ where: { slug }, select: { id: true, ownerId: true, type: true } });
    if (!listing || !(STORE_TYPES as readonly string[]).includes(listing.type)) throw new ApiError(404, 'not_found', 'store listing not found');
    if (listing.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');
    const assets = await prisma.storeAsset.findMany({
      where: { listingId: listing.id },
      orderBy: [{ kind: 'asc' }, { sortOrder: 'asc' }],
      select: { id: true, kind: true, cid: true, width: true, height: true, sortOrder: true },
    });
    return ok({ assets: jsonSafe(assets.map((a) => ({ ...a, url: assetUrl(a.cid) }))) });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/store/assets -> upload one image (scope: publish, listing owner).
// Headers: x-anm-listing (slug), x-anm-kind icon|screenshot|banner, optional
// x-anm-sort, x-anm-width, x-anm-height. ICON/BANNER replace any existing one.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');

    const slug = (req.headers.get('x-anm-listing') || '').trim();
    if (!slug) throw new ApiError(400, 'bad_request', 'x-anm-listing header (listing slug) required');
    const kind = (req.headers.get('x-anm-kind') || '').trim().toUpperCase();
    if (!(KINDS as readonly string[]).includes(kind)) {
      throw new ApiError(400, 'bad_kind', `x-anm-kind must be one of ${KINDS.join(', ').toLowerCase()}`);
    }
    const listing = await prisma.listing.findUnique({ where: { slug }, select: { id: true, ownerId: true, type: true } });
    if (!listing || !(STORE_TYPES as readonly string[]).includes(listing.type)) throw new ApiError(404, 'not_found', 'store listing not found');
    if (listing.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');

    const mime = (req.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
    if (!IMAGE_MIMES.has(mime)) throw new ApiError(415, 'bad_mime', `content-type must be one of ${[...IMAGE_MIMES].join(', ')}`);

    const bytes = Buffer.from(await req.arrayBuffer());
    if (bytes.length === 0) throw new ApiError(400, 'empty_body', 'raw image body required');
    if (bytes.length > MAX_CONTENT_BYTES) {
      throw new ApiError(413, 'too_large', `max ${Math.round(MAX_CONTENT_BYTES / 1048576)} MB per store image`);
    }

    const count = await prisma.storeAsset.count({ where: { listingId: listing.id } });
    if (count >= MAX_ASSETS_PER_LISTING && kind === 'SCREENSHOT') {
      throw new ApiError(409, 'too_many_assets', `max ${MAX_ASSETS_PER_LISTING} assets per listing — delete some first`);
    }

    const sortOrder = Math.max(0, Math.min(Number(req.headers.get('x-anm-sort') ?? 0) || 0, 99));
    const width = Number(req.headers.get('x-anm-width')) || null;
    const height = Number(req.headers.get('x-anm-height')) || null;

    const { cid } = await putObject(bytes, mime, ctx.accountId);
    const asset = await prisma.$transaction(async (tx) => {
      // A listing has exactly one icon + one banner — replacing is the obvious semantics.
      if (kind === 'ICON' || kind === 'BANNER') {
        await tx.storeAsset.deleteMany({ where: { listingId: listing.id, kind: kind as any } });
      }
      return tx.storeAsset.create({
        data: { listingId: listing.id, kind: kind as any, cid, width, height, sortOrder },
      });
    });
    return ok({ asset: jsonSafe(asset), url: assetUrl(cid) }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}

// DELETE /api/mkt/v1/store/assets?id= -> owner removes an asset row (the CID object
// stays — content-addressed bytes may be shared with other listings).
export async function DELETE(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const id = req.nextUrl.searchParams.get('id') || '';
    if (!id) throw new ApiError(400, 'bad_request', 'id required');
    const asset = await prisma.storeAsset.findUnique({
      where: { id },
      select: { id: true, listing: { select: { ownerId: true } } },
    });
    if (!asset) throw new ApiError(404, 'not_found', 'asset not found');
    if (asset.listing.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');
    await prisma.storeAsset.delete({ where: { id } });
    return ok({ deleted: true });
  } catch (e) {
    return err(e);
  }
}
