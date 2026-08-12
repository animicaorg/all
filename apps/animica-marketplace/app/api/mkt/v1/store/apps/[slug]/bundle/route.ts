import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withCredentialedCors, mixedPreflight } from '@/lib/api';
import { prisma } from '@/lib/db';
import { putObject, statObject } from '@/lib/storage';
import { STORE_TYPES, assetUrl } from '@/lib/storeCatalog';
import { GAME_BUNDLE_MIME, GAME_BUNDLE_MAX_BYTES, looksLikeHtmlDoc, ensureSandboxedBundle } from '@/lib/gameBundle';
import { jsonSafe } from '@/lib/nanm';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// CORS preflight. The Game Lab publish flow (animica.io) POSTs the raw text/html bundle
// cross-origin — a text/html body is NOT a CORS-safelisted content-type, so the browser always
// preflights it. Credentialed for the allow-listed Forge origin; public otherwise (the GET is a
// public price-aware read for the wallet Store tab / .anm sites).
export async function OPTIONS(req: NextRequest) {
  return mixedPreflight(req, 'GET, POST, OPTIONS');
}

// Web-game play bundle for a DIGITAL_GOOD listing (Game Lab -> store). The bundle is a single
// self-contained, sandboxed HTML document (Forge buildSandboxedSrcDoc output). It is stored on
// the CID content rail (lib/storage.ts, <=2MB, in-DB) and its CID recorded on Listing.bundleCid.
// This is deliberately NOT an AppBuild — web bundles never touch the APK registerBuild pipeline.
//
//   POST  raw text/html body (scope publish, owner) -> store bundle, set Listing.bundleCid
//   GET   public, price-aware                        -> bundle metadata + FREE play URL
//
// FREE games play straight from the public content route (GET /api/mkt/v1/content/[cid], which
// already serves under the exact `sandbox allow-scripts` CSP a game iframe needs). PAID games
// expose only `hasBundle` publicly; their bytes are served license-gated (play-token) so the
// immutable content URL never becomes a paywall leak.

function isFreeListing(prices: { model: string }[]): boolean {
  return prices.length === 0 || prices.every((p) => p.model === 'FREE');
}

// POST /api/mkt/v1/store/apps/[slug]/bundle  (raw text/html body) -> store the play bundle.
export async function POST(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');

    const listing = await prisma.listing.findUnique({
      where: { slug: params.slug },
      select: { id: true, ownerId: true, type: true },
    });
    if (!listing || !(STORE_TYPES as readonly string[]).includes(listing.type)) {
      throw new ApiError(404, 'not_found', 'store listing not found');
    }
    if (listing.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');
    // Web-game bundles attach only to DIGITAL_GOOD listings — APP listings ship APKs via AppBuild.
    if (listing.type !== 'DIGITAL_GOOD') {
      throw new ApiError(409, 'not_a_web_good', 'play bundles attach only to DIGITAL_GOOD listings (APP listings use APK builds)');
    }

    const mime = (req.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
    if (mime !== 'text/html') throw new ApiError(415, 'bad_mime', 'content-type must be text/html (a self-contained play bundle)');

    const raw = Buffer.from(await req.arrayBuffer());
    if (raw.length === 0) throw new ApiError(400, 'empty_body', 'raw HTML bundle body required');
    if (raw.length > GAME_BUNDLE_MAX_BYTES) {
      throw new ApiError(413, 'too_large', `max ${Math.round(GAME_BUNDLE_MAX_BYTES / 1048576)} MB per play bundle`);
    }

    const html = raw.toString('utf-8');
    if (!looksLikeHtmlDoc(html)) throw new ApiError(422, 'not_html', 'body must be a single self-contained HTML document');

    // Guarantee the doc carries the locked-down inner CSP (idempotent for Forge-built bundles).
    const sandboxed = ensureSandboxedBundle(html);
    const bytes = sandboxed === html ? raw : Buffer.from(sandboxed, 'utf-8');
    if (bytes.length > GAME_BUNDLE_MAX_BYTES) {
      throw new ApiError(413, 'too_large', `bundle exceeds ${Math.round(GAME_BUNDLE_MAX_BYTES / 1048576)} MB after sandboxing`);
    }

    const { cid, size, deduped } = await putObject(bytes, GAME_BUNDLE_MIME, ctx.accountId);
    await prisma.listing.update({ where: { id: listing.id }, data: { bundleCid: cid } });

    return withCredentialedCors(req, ok(jsonSafe({
      bundle: {
        cid, size, mime: GAME_BUNDLE_MIME, deduped,
        wrapped: sandboxed !== html, // true if we had to inject the CSP (non-Forge bundle)
        playUrl: assetUrl(cid),      // public content URL; expose to players only for FREE games
      },
    }), { status: 201 }));
  } catch (e) {
    return withCredentialedCors(req, err(e));
  }
}

// GET /api/mkt/v1/store/apps/[slug]/bundle -> bundle metadata. Public + price-aware: the direct
// content play URL is returned for FREE listings (public play) or to the owner (management); for
// PAID listings the public caller sees only `hasBundle` (play goes through the license-gated route).
export async function GET(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    // Optional auth — a missing/invalid credential is fine (public read); only rate-limit throws.
    const ctx = await authenticate(req).catch(() => null);

    const listing = await prisma.listing.findUnique({
      where: { slug: params.slug },
      select: {
        ownerId: true, type: true, status: true, bundleCid: true,
        prices: { where: { active: true }, select: { model: true } },
      },
    });
    if (!listing || !(STORE_TYPES as readonly string[]).includes(listing.type)) {
      throw new ApiError(404, 'not_found', 'store listing not found');
    }
    const isOwner = !!ctx && ctx.accountId === listing.ownerId;
    if (listing.status === 'DRAFT' && !isOwner) throw new ApiError(404, 'not_found', 'store listing not found');

    const hasBundle = !!listing.bundleCid;
    const free = isFreeListing(listing.prices);
    // Direct content URL is exposed for FREE listings (public play) or to the owner (management).
    const exposeDirect = hasBundle && (free || isOwner);
    const stat = exposeDirect && listing.bundleCid ? await statObject(listing.bundleCid) : null;

    return withCredentialedCors(req, ok(jsonSafe({
      slug: params.slug,
      hasBundle,
      free,
      mime: hasBundle ? GAME_BUNDLE_MIME : null,
      cid: exposeDirect ? listing.bundleCid : null,
      playUrl: exposeDirect && listing.bundleCid ? assetUrl(listing.bundleCid) : null,
      size: stat?.size ?? null,
    })));
  } catch (e) {
    return withCredentialedCors(req, err(e));
  }
}
