# Explorer web assets

Assets in this folder are used by the **Explorer (web/desktop)** for theming and charts. Files are production-ready and safe to serve via a CDN with long-lived immutable caching.

> See also: `contrib/tokens/*` (design tokens), `contrib/brand/ACCESSIBILITY.md` (contrast rules), and `contrib/LICENSING.md`.

---

## Contents

- **themes/**
  - `light.css` — light theme variables and base styles for Explorer UI.
  - `dark.css` — dark theme overrides (pairs with `data-theme="dark"`).
- **charts/**
  - `palette.json` — canonical color palette for charts (Γ spark, mempool, DA states).
- **components/**
  - `GammaSpark.svg` — tiny sparkline used in headers/cards (Γ trend).
  - `PoIESBreakdown.svg` — legend/diagram glyph for PoIES composition.

---

## How to consume in the Explorer app

### 1) Include CSS tokens and Explorer theme
Include global design tokens first, then Explorer theme:

```html
<link rel="stylesheet" href="/contrib/tokens/build/css/tokens.css" />
<link rel="stylesheet" href="/contrib/explorer/themes/light.css" />
To enable dark mode, add either:

html
Copy code
<html data-theme="dark">
<!-- or toggle at runtime: document.documentElement.setAttribute('data-theme','dark') -->
<link rel="stylesheet" href="/contrib/tokens/build/css/tokens.dark.css" />
<link rel="stylesheet" href="/contrib/explorer/themes/dark.css" />
The Explorer themes assume CSS variables from tokens.css are present.

2) Load chart palette
Use the canonical palette to provide consistent colors across Recharts/D3/Chart.js:

ts
Copy code
import palette from "/contrib/explorer/charts/palette.json" assert { type: "json" };

// examples
const gammaLine = palette.gamma.line;      // Γ sparkline color
const mempoolPending = palette.mempool.tx; // mempool tx bars/area
const daOK = palette.da.ok;                // DA availability good
Example structure (abbrev):

json
Copy code
{
  "gamma": { "line": "#5EEAD4", "glow": "rgba(94,234,212,0.25)" },
  "mempool": { "tx": "#60A5FA", "background": "#0B1220" },
  "da": { "ok": "#22C55E", "warn": "#F59E0B", "error": "#EF4444" }
}
3) Inline SVG components
For maximum themeability, inline the SVGs so fills/strokes can reference CSS variables:

tsx
Copy code
import GammaSpark from "/contrib/explorer/components/GammaSpark.svg?inline";

export function HeaderGamma() {
  return (
    <div className="gamma-wrap">
      <GammaSpark className="gamma-spark" />
    </div>
  );
}
In CSS, you can override colors with tokens:

css
Copy code
.gamma-spark path {
  stroke: var(--color-accent, #5EEAD4);
}
If your bundler doesn’t support ?inline, copy the SVG contents inline or use an SVGR loader.

Theming notes
Both light.css and dark.css set Explorer-specific vars (surfaces, borders, chart gridlines) on :root and [data-theme="dark"].

Prefer tokens (spacing, radii, elevation) from contrib/tokens/build/css/* and layer Explorer overrides on top.

For charts, keep background and gridline contrast within WCAG guidance; see contrib/brand/ACCESSIBILITY.md.

Caching & versioning
Serve via CDN with:

arduino
Copy code
Cache-Control: public, max-age=31536000, immutable
When you change files, bump a token in contrib/CHANGELOG.md and version your asset URLs (e.g., ?v=2025-11-01) or publish to a hashed path.

Quality checklist
Colors match palette.json for Γ, mempool, and DA states.

SVGs contain paths (no live fonts) and are small (<10KB ideal).

CSS only uses variables—no hardcoded colors where tokens exist.

Dark mode verified by toggling data-theme="dark".

License
Explorer assets are © Animica. Redistribution is permitted within Animica repositories and products. External/commercial usage requires prior written permission. See contrib/LICENSING.md for details.
