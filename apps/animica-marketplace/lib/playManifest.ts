// Shared, read-only helpers for the standalone Game Lab PLAY surface (app/play/[slug]) and its
// per-game Web App Manifest (app/play/[slug]/manifest.webmanifest). Everything here is DERIVED
// from existing Listing + StoreAsset fields — no new DB columns. The page head (apple-touch-icon /
// apple-web-app meta) and the manifest must agree on the icon + colors, so both read from here.

// Marketplace brand background — the PWA theme/background colors and the play-shell backdrop.
// Matches styles/globals.css :root --bg so the standalone launch looks like the store.
export const PLAY_BG = '#07080c';
export const PLAY_THEME = '#07080c';

// Icon asset shape (a StoreAsset row narrowed to what the manifest/head need). Only ICON assets
// are ever passed in — the play queries already filter `kind: 'ICON'`.
export type PlayIconAsset = { kind: string; cid: string; width: number | null; height: number | null };

// The publisher-uploaded raster ICON for a listing, if any (first by sortOrder). A real raster is
// preferred for both the Android manifest and the iOS apple-touch-icon; the SVG default below is a
// fallback for games with no icon asset yet.
export function rasterIcon(assets: PlayIconAsset[]): PlayIconAsset | null {
  return assets.find((a) => a.kind === 'ICON') ?? null;
}

// Default game icon when a listing has no StoreAsset ICON: a self-contained SVG (marketplace
// gradient tile + play glyph) as a data URI, so the manifest stays same-origin-CSP-clean with no
// bundled binary asset. Declared in the manifest with type image/svg+xml; NOT emitted as an iOS
// apple-touch-icon (iOS renders SVG touch icons unreliably — it falls back to a page snapshot).
const DEFAULT_PLAY_ICON_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">' +
  '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">' +
  '<stop offset="0" stop-color="#6c5cff"/><stop offset="1" stop-color="#14e0c8"/>' +
  '</linearGradient></defs>' +
  '<rect width="512" height="512" rx="112" fill="#0d0f16"/>' +
  '<rect x="28" y="28" width="456" height="456" rx="96" fill="url(#g)"/>' +
  '<path d="M204 168 L364 256 L204 344 Z" fill="#07080c"/>' +
  '</svg>';

export const DEFAULT_PLAY_ICON_DATA_URI =
  `data:image/svg+xml,${encodeURIComponent(DEFAULT_PLAY_ICON_SVG)}`;

// Home-screen label: iOS/Android truncate hard, so keep it short. Whole name when it already fits,
// otherwise the first word (or a 12-char clip) — never empty.
export function shortName(name: string): string {
  const n = (name || 'Game').trim();
  if (n.length <= 12) return n;
  const first = n.split(/\s+/)[0];
  return (first.length <= 12 && first.length > 0 ? first : n.slice(0, 12)).trim() || 'Game';
}
