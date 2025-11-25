# Website assets (favicons, PWA, social previews)

This folder contains **production-ready assets** for the Animica website(s) and any web property that embeds our brand (Explorer, Studio, Docs, Landing). It is safe to serve these files directly from a CDN with long-lived immutable caching.

> See also: `contrib/README.md` (asset philosophy), `contrib/LICENSING.md` (third-party licenses), and `contrib/tokens/*` (design tokens that theme the web UIs).

---

## Contents

- **favicons/**
  - `favicon-16x16.png`, `favicon-32x32.png`, `favicon-48x48.png`, `favicon-96x96.png`
- **pwa/**
  - `maskable-192.png`, `maskable-512.png` (mask-safe icons for Android/Chrome)
- **og/**
  - `og-default.png`, `og-home.png`, `og-explorer.png`, `og-studio.png` (Open Graph/Twitter cards)

---

## How to use (Next.js / Vite)

### 1) Put files under `public/` or serve from a CDN
- **Next.js**: copy this tree under `apps/web/public/website/` or `public/` at the repo root.
- **Vite**: same idea—anything under `public/` is copied to the dist root.

Example (Next.js, paths under `/website/...`):
```tsx
// pages/_document.tsx
<link rel="icon" type="image/png" sizes="16x16" href="/website/favicons/favicon-16x16.png" />
<link rel="icon" type="image/png" sizes="32x32" href="/website/favicons/favicon-32x32.png" />
<link rel="icon" type="image/png" sizes="48x48" href="/website/favicons/favicon-48x48.png" />
<link rel="icon" type="image/png" sizes="96x96" href="/website/favicons/favicon-96x96.png" />
<link rel="apple-touch-icon" sizes="192x192" href="/website/pwa/maskable-192.png" />
<link rel="apple-touch-icon" sizes="512x512" href="/website/pwa/maskable-512.png" />
2) Progressive Web App (PWA) manifest
Add a web manifest that references these icons (example public/manifest.webmanifest):

json
Copy code
{
  "name": "Animica",
  "short_name": "Animica",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0B0D12",
  "theme_color": "#0B0D12",
  "icons": [
    { "src": "/website/pwa/maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable" },
    { "src": "/website/pwa/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable" }
  ]
}
Wire it in <head>:

html
Copy code
<link rel="manifest" href="/manifest.webmanifest" />
<meta name="theme-color" content="#0B0D12" />
3) Social share images (Open Graph / Twitter)
Use the most relevant OG image per page:

tsx
Copy code
// Next.js 13+ (App Router) example
export const metadata = {
  openGraph: {
    title: "Animica — Build with Determinism",
    description: "AI, DA, quantum, and a deterministic Python VM.",
    images: [{ url: "/website/og/og-default.png", width: 1200, height: 630 }]
  },
  twitter: {
    card: "summary_large_image",
    images: ["/website/og/og-default.png"]
  }
};
Per-route overrides:

Home: /website/og/og-home.png

Explorer: /website/og/og-explorer.png

Studio: /website/og/og-studio.png

Caching & versioning
Recommended headers for CDN/edge:

arduino
Copy code
Cache-Control: public, max-age=31536000, immutable
We bump a token whenever assets change:

Token source: contrib/CHANGELOG.md and design token version in contrib/tokens/*.

URL versioning (optional): /website/og/og-default.png?v=2025-11-01 or folder hashing in your deploy script.

Never overwrite a file in-place without also bumping the cache key (query or path). Immutable caches assume content doesn’t change.

Quality & accessibility checks
Sizes:

Favicons: 16, 32, 48, 96 px (PNG, sRGB, no color profiles).

PWA icons: 192, 512 px, maskable-safe (content centered with safe padding).

OG images: 1200×630 px, < 1 MB (PNG or high-quality JPEG), strong contrast.

Color & contrast: follow contrib/brand/ACCESSIBILITY.md.

No embedded fonts in SVGs when rasterizing—convert text to paths first (avoid “non-conforming drawing primitive” errors on CI).

Quick local validation (macOS):

bash
Copy code
sips -g pixelWidth -g pixelHeight contrib/website/favicons/favicon-32x32.png
sips -g pixelWidth -g pixelHeight contrib/website/og/og-default.png
Pipeline integration
Tokens: publish contrib/tokens/build/css/tokens.css and tokens.dark.css; import them globally in the site for consistent brand color/spacing.

Static analysis (optional):

Lint OG sizes: ensure every page that should have an OG image does; fail CI if missing.

Validate PNGs are sRGB and stripped (no ICC profiles): magick identify -verbose.

Image optimization: if your build adds an optimizer (Sharp/Imagemin), whitelist these files to avoid quality regressions on OG cards.

Common pitfalls
Blurry favicons: ensure the base vector is snapped and rasterized at exact pixel sizes; do not upscale from 16→32 etc—always render from vector or 1024 master.

Maskable icons clipped: keep a 20–24% border around the mark; test on Android’s mask preview.

Social previews not updating: social platforms cache OG images—use a versioned URL (?v=stamp) on content changes.

Rebuilding from vectors (optional)
If you need to regenerate favicons from the mark-only SVG:

bash
Copy code
magick -background none -density 1024 contrib/logos/animica-mark-only.svg -resize 96x -strip -colorspace sRGB contrib/website/favicons/favicon-96x96.png
magick -background none -density 1024 contrib/logos/animica-mark-only.svg -resize 48x -strip -colorspace sRGB contrib/website/favicons/favicon-48x48.png
magick -background none -density 1024 contrib/logos/animica-mark-only.svg -resize 32x -strip -colorspace sRGB contrib/website/favicons/favicon-32x32.png
magick -background none -density 1024 contrib/logos/animica-mark-only.svg -resize 16x -strip -colorspace sRGB contrib/website/favicons/favicon-16x16.png
Change process
Update assets or regenerate from vectors.

Bump the asset token in contrib/CHANGELOG.md.

If served via CDN, ensure immutable caching and a versioned URL.

Verify locally (sizes, contrast, sRGB), then deploy.

License
Brand assets are © Animica. Redistribution is permitted within Animica repos and websites; third-party or commercial usage requires written permission. See contrib/LICENSING.md for full details.

