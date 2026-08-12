import { notFound } from 'next/navigation';
import type { Metadata, Viewport } from 'next';
import { prisma } from '@/lib/db';
import { isFreeToPlay } from '@/lib/entitlement';
import { assetUrl } from '@/lib/storeCatalog';
import { PLAY_BG, PLAY_THEME, rasterIcon, shortName } from '@/lib/playManifest';
import { gameStatFor } from '@/lib/gameStats';
import { compactCount } from '@/components/AppCard';
import PlayStandalone from './PlayStandalone';

export const dynamic = 'force-dynamic';

// Standalone, chrome-minimal PLAY page for a Game Lab web game — a REAL URL (/play/[slug]) a
// browser or an installed PWA can open. Additive: it plays the same self-contained HTML bundle
// (Listing.bundleCid) the detail-page panel does, but full-viewport with no site nav, and it
// advertises a per-game Web App Manifest so mobile users "Add to Home Screen" and launch it
// display:standalone. Only PUBLISHED games (DIGITAL_GOOD / GAMES / bundleCid) resolve here —
// mirrors the store detail route's DRAFT/DELISTED gate; everything is derived from existing
// Listing + StoreAsset fields (no new columns).

type PlayGame = {
  id: string;
  name: string;
  tagline: string | null;
  bundleCid: string;
  prices: { model: string }[];
  storeAssets: { kind: string; cid: string; width: number | null; height: number | null }[];
};

async function loadGame(slug: string): Promise<PlayGame | null> {
  const listing = await prisma.listing.findUnique({
    where: { slug },
    select: {
      id: true,
      name: true,
      tagline: true,
      type: true,
      appCategory: true,
      status: true,
      bundleCid: true,
      prices: { where: { active: true }, select: { model: true } },
      storeAssets: {
        where: { kind: 'ICON' },
        orderBy: { sortOrder: 'asc' },
        select: { kind: true, cid: true, width: true, height: true },
      },
    },
  });
  if (!listing) return null;
  // Web game only, and only while publicly available (parity with the store detail page).
  if (listing.type !== 'DIGITAL_GOOD' || listing.appCategory !== 'GAMES' || !listing.bundleCid) return null;
  if (listing.status === 'DRAFT' || listing.status === 'DELISTED') return null;
  return {
    id: listing.id,
    name: listing.name,
    tagline: listing.tagline,
    bundleCid: listing.bundleCid,
    prices: listing.prices,
    storeAssets: listing.storeAssets,
  };
}

export async function generateMetadata({ params }: { params: { slug: string } }): Promise<Metadata> {
  const game = await loadGame(params.slug);
  if (!game) return { title: 'Game not found — Animica Play' };
  const icon = rasterIcon(game.storeAssets);
  return {
    title: `${game.name} — Play on Animica`,
    description: game.tagline || `Play ${game.name} on Animica.`,
    // Emits <link rel="manifest" href="/play/[slug]/manifest.webmanifest"> for Add-to-Home-Screen.
    manifest: `/play/${params.slug}/manifest.webmanifest`,
    // iOS home-screen launch: apple-mobile-web-app-capable + title + status bar meta.
    appleWebApp: {
      capable: true,
      title: shortName(game.name),
      statusBarStyle: 'black-translucent',
    },
    // apple-touch-icon only for a real raster icon (iOS renders SVG touch icons unreliably).
    ...(icon ? { icons: { apple: assetUrl(icon.cid) } } : {}),
    // The play surface is an app runtime, not a landing page to index.
    robots: { index: false, follow: false },
  };
}

export const viewport: Viewport = {
  themeColor: PLAY_THEME,
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
};

export default async function PlayPage({ params }: { params: { slug: string } }) {
  const game = await loadGame(params.slug);
  if (!game) notFound();

  const free = isFreeToPlay(game.prices);
  // Cosmetic, player-reported play count (never tied to ANM). Fails closed to 0.
  const stat = await gameStatFor(game.id);

  return (
    <>
      {/* Chrome-minimal: hide the shared marketplace nav/footer so the game owns the viewport
          (also matters for the PWA standalone launch at start_url=/play/[slug]). Additive — the
          style is scoped to this page's render and reverts on client navigation away. */}
      <style>{`.nav,.footer{display:none!important} html,body{background:${PLAY_BG};overflow:hidden}`}</style>
      <PlayStandalone slug={params.slug} bundleCid={game.bundleCid} name={game.name} isFree={free} />
      {/* Header play-count badge — a static, non-interactive chrome pill (pointer-events:none so it
          never blocks the game; sits below the play shell's own chrome/leaderboard z-layers).
          Rendered outside PlayStandalone so it never collides with the capture lane's overlay. */}
      {stat.plays > 0 && (
        <div className="play-plays" aria-hidden title={`${stat.plays.toLocaleString()} plays${stat.players > 0 ? ` · ${stat.players.toLocaleString()} players` : ''}`}>
          ▶ {compactCount(stat.plays)} play{stat.plays === 1 ? '' : 's'}
          {stat.topScore != null ? <span className="play-plays-sep">· ★ {compactCount(stat.topScore)}</span> : null}
        </div>
      )}
      <style>{`
        .play-plays{position:fixed;top:calc(env(safe-area-inset-top,0px) + 10px);left:50%;transform:translateX(-50%);
          z-index:9;pointer-events:none;font-size:11.5px;font-weight:600;color:var(--text);
          background:rgba(13,15,22,0.55);border:1px solid var(--border-bright);border-radius:999px;
          padding:5px 11px;backdrop-filter:blur(8px);opacity:.4;white-space:nowrap;display:inline-flex;gap:6px}
        .play-plays-sep{color:var(--warn)}
      `}</style>
    </>
  );
}
