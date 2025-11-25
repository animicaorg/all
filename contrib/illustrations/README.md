# Animica Illustrations

Illustrations, hero art, and background patterns used across **Website, Explorer, Wallet Extension, Flutter Wallet, Studio**, docs, and press. All vectors default to token-driven colors and are authored to scale crisply from mobile → desktop.

> For brand rules and contrast, see `contrib/brand/BRAND_GUIDE.md` and `contrib/brand/ACCESSIBILITY.md`.

---

## What’s inside

contrib/illustrations/
├─ README.md
├─ hero/
│ ├─ mesh-bg.svg # gradient mesh, responsive hero background
│ └─ animica-orb.svg # 3D-ish orb w/ subtle rings
├─ sections/
│ ├─ poies-diagram.svg # PoIES concept diagram (Γ signal)
│ ├─ da-diagram.svg # Data availability layout (blobs, shards, nodes)
│ ├─ vm-diagram.svg # Deterministic Python VM blocks
│ └─ quantum-diagram.svg # Bloch sphere motif
└─ patterns/
├─ noise.png # subtle film grain (transparent)
├─ grid.svg # alignment grid for docs
└─ radial.svg # radial vignette overlay

csharp
Copy code

**Conventions**
- **SVG first**: scalable, themeable, minimal payload.
- **Color**: prefer `currentColor` or CSS variables (see tokens).
- **Stroke**: rounded caps/joins where appropriate.
- **ViewBox**: always present; sizing controlled by CSS.
- **Export**: when raster is needed, export 1×/2× (and 3× if used in native).

---

## Quick usage

### HTML
```html
<section class="hero">
  <img src="/contrib/illustrations/hero/mesh-bg.svg" alt="" aria-hidden="true">
  <img class="orb" src="/contrib/illustrations/hero/animica-orb.svg" alt="Animica orb illustration">
</section>
React/Next.js
tsx
Copy code
import Mesh from '/contrib/illustrations/hero/mesh-bg.svg?url';
import Orb from '/contrib/illustrations/hero/animica-orb.svg?url';

export function Hero() {
  return (
    <div className="relative overflow-hidden">
      <img src={Mesh} alt="" aria-hidden className="absolute inset-0 w-full h-full object-cover" />
      <img src={Orb} alt="Animica orb" className="relative mx-auto w-[480px] h-auto" />
    </div>
  );
}
CSS background
css
Copy code
.hero-bg {
  background-image: url('/contrib/illustrations/hero/mesh-bg.svg');
  background-size: cover;
  background-position: center;
}
Theming (tokens)
css
Copy code
.illustration {
  color: var(--anm-color-primary-600); /* used by SVGs that inherit currentColor */
  filter: drop-shadow(var(--anm-shadow-lg, 0 8px 24px rgba(0,0,0,.1)));
}
Accessibility
Decorative images: alt="" aria-hidden="true".

Informative diagrams (e.g., poies-diagram.svg): include meaningful alt and, if complex, link a caption or descriptive text nearby for screen readers.

Maintain WCAG AA contrast where text or key symbols overlay illustrations (ACCESSIBILITY.md).

Optimization
All SVGs should be run through SVGO using contrib/icons/svgo.config.json (works for illustrations too).

bash
Copy code
npx svgo -f contrib/illustrations -c contrib/icons/svgo.config.json
Keep:

viewBox (responsive scaling).

IDs that are referenced by CSS/JS (don’t use cleanupIds for those files).

Raster exports (when required)
For platforms that demand PNG/JPEG:

bash
Copy code
# Install once (macOS): brew install librsvg
# 2× export (e.g., 2400×1200) from an SVG
rsvg-convert -w 2400 -h 1200 contrib/illustrations/hero/mesh-bg.svg \
  -o contrib/illustrations/hero/mesh-bg@2x.png
Prefer transparent PNG (noise.png) for grain overlays that stack above color layers.

Editing guidelines
Keep gradients and blurs subtle to avoid banding.

Use even stroke widths (1/2) for crisp rendering.

If using filters (blur/shadow), test performance on low-end mobile.

Versioning
Major visual changes → bump contrib/CHANGELOG.md (Visuals).

Coordinate with app icons and press kit when the hero/mark motif changes.

Licensing
Illustrations © Animica. Internal use across Animica apps permitted.

External use requires permission unless covered by a published brand policy.

Third-party textures or fonts must include attribution in contrib/LICENSING.md.

