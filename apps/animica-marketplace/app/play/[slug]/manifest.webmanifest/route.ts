import { prisma } from '@/lib/db';
import { assetUrl } from '@/lib/storeCatalog';
import { PLAY_BG, PLAY_THEME, DEFAULT_PLAY_ICON_DATA_URI, rasterIcon, shortName } from '@/lib/playManifest';

export const dynamic = 'force-dynamic';

// GET /play/[slug]/manifest.webmanifest -> a per-game Web App Manifest derived ENTIRELY from
// existing Listing + StoreAsset fields (no new columns). A mobile browser reads this (linked from
// the play page head) to offer "Add to Home Screen"; once installed the PWA opens
// start_url=/play/[slug] in display:standalone, so the game runs full-screen like a native app.
// Public read, published games only — mirrors the store detail route's DRAFT/DELISTED gate.

function notAManifest(status: number, message: string): Response {
  return new Response(JSON.stringify({ error: { code: 'not_found', message } }), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  });
}

export async function GET(_req: Request, { params }: { params: { slug: string } }): Promise<Response> {
  const slug = params.slug;
  const listing = await prisma.listing.findUnique({
    where: { slug },
    select: {
      name: true,
      tagline: true,
      type: true,
      appCategory: true,
      status: true,
      bundleCid: true,
      storeAssets: {
        where: { kind: 'ICON' },
        orderBy: { sortOrder: 'asc' },
        select: { kind: true, cid: true, width: true, height: true },
      },
    },
  });
  if (!listing) return notAManifest(404, 'game not found');
  if (listing.type !== 'DIGITAL_GOOD' || listing.appCategory !== 'GAMES' || !listing.bundleCid) {
    return notAManifest(404, 'not a web game');
  }
  if (listing.status === 'DRAFT' || listing.status === 'DELISTED') {
    return notAManifest(404, 'game not available');
  }

  const start = `/play/${slug}`;
  const icon = rasterIcon(listing.storeAssets);
  // A publisher raster ICON serves the app icon + a reused maskable variant (satisfies Chrome's
  // 192/512 installability); with no icon we fall back to the self-contained default SVG.
  const icons = icon
    ? [
        { src: assetUrl(icon.cid), sizes: '192x192', purpose: 'any' },
        {
          src: assetUrl(icon.cid),
          sizes: icon.width && icon.height ? `${icon.width}x${icon.height}` : '512x512',
          purpose: 'any',
        },
        { src: assetUrl(icon.cid), sizes: '512x512', purpose: 'maskable' },
      ]
    : [
        { src: DEFAULT_PLAY_ICON_DATA_URI, sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
        { src: DEFAULT_PLAY_ICON_DATA_URI, sizes: '512x512', type: 'image/svg+xml', purpose: 'maskable' },
      ];

  const manifest = {
    id: start,
    name: listing.name,
    short_name: shortName(listing.name),
    description: listing.tagline || `Play ${listing.name} on Animica.`,
    start_url: start,
    scope: start,
    display: 'standalone',
    orientation: 'any',
    theme_color: PLAY_THEME,
    background_color: PLAY_BG,
    categories: ['games'],
    icons,
  };

  return new Response(JSON.stringify(manifest), {
    status: 200,
    headers: {
      'content-type': 'application/manifest+json; charset=utf-8',
      // Derived from mutable listing fields — keep fresh but lightly cacheable at the edge.
      'cache-control': 'public, max-age=300',
    },
  });
}
